"""Ties cameras to analysis: one analysis thread drains every camera's queue,
runs quality checks and the detector, and emits observations. On a GPU this
is where frames from several cameras are batched.

The health server also hosts the core -> vision clip endpoint (POST /v1/clips,
packages/contracts/API.md). It binds to 127.0.0.1 by default."""

from __future__ import annotations

import concurrent.futures
import hmac
import ipaddress
import json
import logging
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .camera import CameraWorker, NoVideoError, StreamState
from .config import WorkerConfig, redact
from .observation import Detector, build_observation
from .quality import QualityMonitor

log = logging.getLogger(__name__)

RGB_QUEUE_SIZE = 4  # per camera, when RGB analysis frames are queued (~0.7 MB each at 640x360)


class VisionWorker:
    def __init__(self, cfg: WorkerConfig, detector: Detector, sink):
        self.detector = detector
        width = getattr(detector, "frame_width", None) if detector.available else None
        qsize = RGB_QUEUE_SIZE if width else 8
        self.cameras = {c.id: CameraWorker(c, queue_size=qsize, analysis_width=width) for c in cfg.cameras}
        self.quality = {c.id: QualityMonitor() for c in cfg.cameras}
        self.sink = sink
        self._stop = threading.Event()
        self._analysis = threading.Thread(target=self._analyze, name="analysis", daemon=True)
        self.started_at = time.time()
        self.analysis_errors_total = 0
        self.frames_analyzed_total = 0
        self._last_err_log = 0.0

    def start(self) -> None:
        for cam in self.cameras.values():
            cam.start()
        self._analysis.start()

    def stop(self) -> None:
        self._stop.set()
        for cam in self.cameras.values():
            cam.stop()
        self._analysis.join(5)

    def process(self, f) -> dict:
        """Quality check, detection (only on good frames) and the observation."""
        q = self.quality[f.camera_id].update(f.gray, f.wall_ts)
        ai_ok = bool(self.detector.available)
        tracks: list[dict] = []
        if ai_ok and not q.degraded:
            try:
                analyze = getattr(self.detector, "analyze", None)
                if analyze is not None:
                    tracks = analyze(f.camera_id, f.wall_ts, f.gray, getattr(f, "rgb", None))
                else:
                    tracks = self.detector.detect(f.gray)
                self.frames_analyzed_total += 1
            except Exception as e:  # this frame has no valid AI result; say so explicitly
                ai_ok, tracks = False, []
                self.analysis_errors_total += 1
                now = time.monotonic()
                if now - self._last_err_log > 10:
                    self._last_err_log = now
                    log.warning("analysis failed on %s: %s", f.camera_id, redact(f"{type(e).__name__}: {e}"))
        return build_observation(f.camera_id, f.wall_ts, q, self.detector, tracks, ai_available=ai_ok)

    def _analyze(self) -> None:
        while not self._stop.is_set():
            idle = True
            for cam in self.cameras.values():
                f = cam.take_frame()
                if f is None:
                    continue
                idle = False
                self.sink.emit(self.process(f))
            if idle:
                self._stop.wait(0.005)

    def status(self) -> dict:
        cams = [c.status() for c in self.cameras.values()]
        st = {
            "detector": self.detector.name,
            "ai_available": bool(self.detector.available),
            "observations_written": self.sink.written,
            "frames_analyzed_total": self.frames_analyzed_total,
            "analysis_errors_total": self.analysis_errors_total,
            "sink": self.sink.status() if hasattr(self.sink, "status") else {},
            "cameras": cams,
        }
        if hasattr(self.detector, "status"):
            st["pipeline"] = self.detector.status()
        return st

    def ready(self) -> bool:
        """Ready means every camera is streaming. A worker that is up but
        blind is alive (healthz) but not ready."""
        return bool(self.cameras) and all(c.state == StreamState.STREAMING for c in self.cameras.values())


def metrics_text(st: dict) -> str:
    lines = []
    counters = ["packets_total", "frames_decoded_total", "frames_sampled_total", "frames_dropped_total",
                "decode_errors_total", "reconnects_total", "clips_written_total"]
    for name in counters:
        lines.append(f"# TYPE zeoatap_camera_{name} counter")
        for c in st["cameras"]:
            lines.append(f'zeoatap_camera_{name}{{camera="{c["camera_id"]}"}} {c[name]}')
    lines.append("# TYPE zeoatap_camera_streaming gauge")
    for c in st["cameras"]:
        lines.append(f'zeoatap_camera_streaming{{camera="{c["camera_id"]}"}} {int(c["state"] == "STREAMING")}')
    lines.append("# TYPE zeoatap_camera_ring_bytes gauge")
    for c in st["cameras"]:
        lines.append(f'zeoatap_camera_ring_bytes{{camera="{c["camera_id"]}"}} {c["ring_bytes"]}')
    lines.append("# TYPE zeoatap_ai_available gauge")
    lines.append(f"zeoatap_ai_available {int(bool(st.get('ai_available')))}")
    for name in ("frames_analyzed_total", "analysis_errors_total"):
        lines.append(f"# TYPE zeoatap_analysis_{name} counter")
        lines.append(f"zeoatap_analysis_{name} {st.get(name, 0)}")
    pipe = st.get("pipeline")
    if pipe:
        lines.append("# TYPE zeoatap_pipeline_last_ms gauge")
        for stage, v in pipe["last_ms"].items():
            lines.append(f'zeoatap_pipeline_last_ms{{stage="{stage}"}} {v}')
        lines.append("# TYPE zeoatap_pipeline_inference_errors_total counter")
        lines.append(f"zeoatap_pipeline_inference_errors_total {pipe['inference_errors_total']}")
    sink = st.get("sink") or {}
    kind = sink.get("kind", "")
    for key, typ in (("emitted_total", "counter"), ("accepted_total", "counter"), ("written_total", "counter"),
                     ("dropped_total", "counter"), ("rejected_total", "counter"), ("batches_total", "counter"),
                     ("failures_total", "counter"), ("retries_total", "counter"), ("buffered", "gauge")):
        if key in sink:
            lines.append(f"# TYPE zeoatap_sink_{key} {typ}")
            lines.append(f'zeoatap_sink_{key}{{sink="{kind}"}} {sink[key]}')
    return "\n".join(lines) + "\n"


# ---- clip control endpoint ---------------------------------------------------
MAX_BODY = 16 * 1024
MAX_CLIP_SECONDS = 600.0
MAX_FUTURE_S = 600.0  # an end time further ahead than this is a caller bug
CLIP_WAIT_S = 120.0  # API.md: blocks until the clip is written (<= end + 120 s)
MAX_CONCURRENT_CLIPS = 8


class ClipError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_clip_request(body: dict, clip_dir: Path | None, now: float) -> tuple[str, float, float, Path]:
    if not isinstance(body, dict):
        raise ClipError(400, "body must be a JSON object")
    unknown = set(body) - {"camera_id", "start", "end", "path"}
    if unknown:
        raise ClipError(400, f"unknown fields: {sorted(unknown)}")
    cam, start, end, path = body.get("camera_id"), body.get("start"), body.get("end"), body.get("path")
    if not isinstance(cam, str) or not cam:
        raise ClipError(400, "camera_id must be a non-empty string")
    if not (_num(start) and _num(end)):
        raise ClipError(400, "start and end must be unix seconds (numbers)")
    if end <= start:
        raise ClipError(400, "end must be after start")
    if end - start > MAX_CLIP_SECONDS:
        raise ClipError(400, f"clip longer than {MAX_CLIP_SECONDS:.0f} s")
    if end > now + MAX_FUTURE_S:
        raise ClipError(400, "end is too far in the future")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ClipError(400, "path must be a non-empty string")
    p = Path(path)
    if not p.is_absolute() or p.suffix.lower() != ".mp4" or ".." in p.parts:
        raise ClipError(400, "path must be an absolute .mp4 path without '..'")
    if clip_dir is not None:
        root = clip_dir.resolve()
        if not p.parent.resolve().is_relative_to(root):
            raise ClipError(400, "path is outside the clip spool directory")
    return cam, float(start), float(end), p


def serve_health(worker: VisionWorker, host: str, port: int, token: str | None = None,
                 clip_dir: str | Path | None = None, clip_wait_s: float = CLIP_WAIT_S) -> ThreadingHTTPServer:
    """Health, status, metrics and the clip control endpoint.

    POST /v1/clips needs `Authorization: Bearer <token>`; without a configured
    token the endpoint is disabled (503)."""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            log.warning("health/clip server bound to non-loopback address %s; keep it on a trusted network", host)
    except ValueError:
        log.warning("health/clip server bound to hostname %s; 127.0.0.1 is recommended", host)
    clip_root = Path(clip_dir) if clip_dir else None
    clip_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CLIPS)
    expected = f"Bearer {token}".encode() if token else None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj))

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self._send(200, '{"ok":true}')
            elif self.path == "/readyz":
                ok = worker.ready()
                self._send(200 if ok else 503, json.dumps({"ready": ok}))
            elif self.path == "/status":
                self._send(200, json.dumps(worker.status()))
            elif self.path == "/metrics":
                self._send(200, metrics_text(worker.status()), "text/plain; version=0.0.4")
            else:
                self._send(404, '{"error":"not found"}')

        def do_POST(self):  # noqa: N802
            if self.path != "/v1/clips":
                return self._json(404, {"error": "not found"})
            try:
                self._json(200, self._clip())
            except ClipError as e:
                self._json(e.code, {"error": e.msg})

        def _clip(self) -> dict:
            if expected is None:
                raise ClipError(503, "clip endpoint disabled: ZEOATAP_INTERNAL_TOKEN not set")
            got = (self.headers.get("Authorization") or "").encode()
            if not hmac.compare_digest(got, expected):
                raise ClipError(401, "unauthorized")
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise ClipError(415, "Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                raise ClipError(413 if length > MAX_BODY else 411, "missing or oversized body")
            self.connection.settimeout(10)
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except (ValueError, OSError):
                raise ClipError(400, "invalid JSON") from None
            now = time.time()
            cam_id, start, end, path = validate_clip_request(body, clip_root, now)
            cam = worker.cameras.get(cam_id)
            if cam is None:
                raise ClipError(404, f"unknown camera {cam_id!r}")
            oldest, newest = cam.ring.oldest_ts, cam.ring.newest_ts
            if end < now and (newest is None or newest < start or oldest > end):
                # A window entirely in the past that the ring does not cover can never be filled.
                raise ClipError(422, "no buffered video covers the requested window")
            if not clip_slots.acquire(blocking=False):
                raise ClipError(503, "too many clip requests in progress")
            try:
                fut = cam.request_clip(start, end, path, wait_s=clip_wait_s)
                timeout = max(0.0, end + clip_wait_s - time.time()) + 15.0
                try:
                    out_path, sha, partial = fut.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    # The job stays queued and finishes on the camera thread; cancelling
                    # here would race with its set_result.
                    raise ClipError(504, "timed out waiting for the clip") from None
                except NoVideoError as e:
                    raise ClipError(422, str(e)) from None
                except Exception as e:
                    log.warning("clip for %s failed: %s", cam_id, redact(f"{type(e).__name__}: {e}"))
                    raise ClipError(500, "clip could not be written") from None
            finally:
                clip_slots.release()
            return {"path": os.fspath(out_path), "sha256": sha, "partial": bool(partial)}

        def log_message(self, *args):  # keep request logs out of stdout
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="health", daemon=True).start()
    return srv
