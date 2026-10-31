"""One camera: connect, demux, buffer packets, decode, sample, and hand frames
to analysis through a bounded drop-oldest queue. Reconnects forever with
backoff; every state change is visible through status()."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import av

from .clip import write_clip
from .config import CameraConfig, redact
from .ringbuffer import PacketRingBuffer
from .timing import Backoff, FrameSampler

log = logging.getLogger(__name__)

THUMB_W, THUMB_H = 160, 90


class StreamState(str, Enum):
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"


@dataclass
class SampledFrame:
    camera_id: str
    wall_ts: float
    gray: object  # numpy uint8 thumbnail


@dataclass
class _ClipJob:
    start: float
    end: float
    path: Path
    deadline: float
    future: Future = field(default_factory=Future)


@dataclass
class CameraStats:
    packets_total: int = 0
    frames_decoded_total: int = 0
    frames_sampled_total: int = 0
    frames_dropped_total: int = 0  # analysis queue overflow
    decode_errors_total: int = 0
    reconnects_total: int = 0
    clips_written_total: int = 0
    last_error: str = ""


class CameraWorker:
    def __init__(self, cfg: CameraConfig, queue_size: int = 8,
                 open_timeout_s: float = 5.0, read_timeout_s: float = 3.0,
                 backoff: Backoff | None = None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.ring = PacketRingBuffer(cfg.ring_seconds, cfg.ring_max_bytes)
        self.sampler = FrameSampler(cfg.target_fps)
        self.backoff = backoff or Backoff()
        self.stats = CameraStats()
        self.state = StreamState.CONNECTING
        self._queue: deque[SampledFrame] = deque()
        self._queue_size = queue_size
        self._qlock = threading.Lock()
        self._open_timeout, self._read_timeout = open_timeout_s, read_timeout_s
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._template_stream = None
        self._clip_jobs: list[_ClipJob] = []
        self._clip_lock = threading.Lock()
        self.state_since = clock()

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._finish_clip_jobs(force=True)  # anything left fails with a reported error
        self._set_state(StreamState.STOPPED)

    # ---- analysis queue --------------------------------------------------
    def take_frame(self) -> SampledFrame | None:
        with self._qlock:
            return self._queue.popleft() if self._queue else None

    def _offer(self, f: SampledFrame) -> None:
        with self._qlock:
            if len(self._queue) >= self._queue_size:
                self._queue.popleft()
                self.stats.frames_dropped_total += 1
            self._queue.append(f)

    # ---- clips -----------------------------------------------------------
    def request_clip(self, start: float, end: float, path: str | Path, wait_s: float = 120.0) -> Future:
        """Clip [start, end] (wall clock). Resolves to (path, sha256, partial)."""
        job = _ClipJob(start, end, Path(path), deadline=end + wait_s)
        with self._clip_lock:
            self._clip_jobs.append(job)
        return job.future

    def _finish_clip_jobs(self, force: bool = False) -> None:
        if not self._clip_jobs:
            return
        newest = self.ring.newest_ts
        with self._clip_lock:
            jobs, self._clip_jobs = self._clip_jobs, []
        keep = []
        for job in jobs:
            ready = newest is not None and newest >= job.end
            if not (ready or force or self._clock() >= job.deadline):
                keep.append(job)
                continue
            try:
                pkts = self.ring.snapshot(job.start, job.end)
                if not pkts or self._template_stream is None:
                    raise RuntimeError("no buffered video covers the requested window")
                sha = write_clip(pkts, self._template_stream, job.path)
                self.stats.clips_written_total += 1
                job.future.set_result((job.path, sha, not ready))
            except Exception as e:  # report, never crash the camera loop
                job.future.set_exception(e)
        with self._clip_lock:
            self._clip_jobs.extend(keep)

    # ---- main loop -------------------------------------------------------
    def status(self) -> dict:
        return {
            "camera_id": self.cfg.id,
            "state": self.state.value,
            "state_since": self.state_since,
            "ring_seconds": (self.ring.newest_ts - self.ring.oldest_ts) if len(self.ring) else 0.0,
            "ring_bytes": self.ring.bytes,
            **self.stats.__dict__,
        }

    def _set_state(self, s: StreamState) -> None:
        if s != self.state:
            log.info("camera %s: %s -> %s", self.cfg.id, self.state.value, s.value)
            self.state, self.state_since = s, self._clock()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._stream_once()
            except Exception as e:
                self.stats.last_error = redact(f"{type(e).__name__}: {e}")
                log.warning("camera %s: %s", self.cfg.id, self.stats.last_error)
            if self._stop.is_set():
                break
            self._set_state(StreamState.RECONNECTING)
            self.stats.reconnects_total += 1
            self._finish_clip_jobs()
            self._stop.wait(self.backoff.next_delay())

    def _stream_once(self) -> None:
        url = self.cfg.resolved_url()
        opts = {"rtsp_transport": "tcp"} if url.startswith(("rtsp://", "rtsps://")) else {}
        with av.open(url, options=opts, timeout=(self._open_timeout, self._read_timeout)) as container:
            video = next((s for s in container.streams if s.type == "video"), None)
            if video is None:
                raise RuntimeError("stream has no video track")
            video.thread_type = "AUTO"
            self.ring.clear()
            self._template_stream = video
            try:
                self._demux(container, video)
            finally:
                # Write pending clips while the stream template is still valid;
                # the ring is cleared on reconnect, so they are marked partial.
                self._finish_clip_jobs(force=True)
        if not self._stop.is_set():
            raise ConnectionError("stream ended")

    def _demux(self, container, video) -> None:
        first_packet = True
        for packet in container.demux(video):
            if self._stop.is_set():
                return
            if packet.size == 0:  # flush packet at end of stream
                continue
            now = self._clock()
            if first_packet:
                self._set_state(StreamState.STREAMING)
                self.backoff.reset()
                first_packet = False
            self.stats.packets_total += 1
            self.ring.append(packet, now)
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                self.stats.decode_errors_total += 1
                continue
            for frame in frames:
                self.stats.frames_decoded_total += 1
                if self.sampler.should_sample(now):
                    gray = frame.reformat(width=THUMB_W, height=THUMB_H, format="gray").to_ndarray()
                    self.stats.frames_sampled_total += 1
                    self._offer(SampledFrame(self.cfg.id, now, gray))
            self._finish_clip_jobs()
