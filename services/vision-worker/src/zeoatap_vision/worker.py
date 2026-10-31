"""Ties cameras to analysis: one analysis thread drains every camera's queue,
runs quality checks and the detector, and emits observations. On a GPU this
is where frames from several cameras are batched."""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .camera import CameraWorker, StreamState
from .config import WorkerConfig
from .observation import Detector, JsonlSink, build_observation
from .quality import QualityMonitor

log = logging.getLogger(__name__)


class VisionWorker:
    def __init__(self, cfg: WorkerConfig, detector: Detector, sink: JsonlSink):
        self.cameras = {c.id: CameraWorker(c) for c in cfg.cameras}
        self.quality = {c.id: QualityMonitor() for c in cfg.cameras}
        self.detector = detector
        self.sink = sink
        self._stop = threading.Event()
        self._analysis = threading.Thread(target=self._analyze, name="analysis", daemon=True)
        self.started_at = time.time()

    def start(self) -> None:
        for cam in self.cameras.values():
            cam.start()
        self._analysis.start()

    def stop(self) -> None:
        self._stop.set()
        for cam in self.cameras.values():
            cam.stop()
        self._analysis.join(5)

    def _analyze(self) -> None:
        while not self._stop.is_set():
            idle = True
            for cam in self.cameras.values():
                f = cam.take_frame()
                if f is None:
                    continue
                idle = False
                q = self.quality[f.camera_id].update(f.gray, f.wall_ts)
                tracks = self.detector.detect(f.gray) if self.detector.available and not q.degraded else []
                self.sink.emit(build_observation(f.camera_id, f.wall_ts, q, self.detector, tracks))
            if idle:
                self._stop.wait(0.005)

    def status(self) -> dict:
        cams = [c.status() for c in self.cameras.values()]
        return {
            "detector": self.detector.name,
            "ai_available": self.detector.available,
            "observations_written": self.sink.written,
            "cameras": cams,
        }

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
    return "\n".join(lines) + "\n"


def serve_health(worker: VisionWorker, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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

        def log_message(self, *args):  # keep request logs out of stdout
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, name="health", daemon=True).start()
    return srv
