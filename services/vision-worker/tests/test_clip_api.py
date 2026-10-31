"""POST /v1/clips on the health server (core -> vision, packages/contracts/API.md):
auth, validation, 404 unknown camera, 422 no video, and a real clip over live
RTSP (synthetic test pattern via MediaMTX + ffmpeg)."""

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_live import MEDIAMTX, camera, rtsp_server, wait_for  # noqa: F401  (fixtures)
from zeoatap_vision.camera import StreamState
from zeoatap_vision.config import CameraConfig, WorkerConfig
from zeoatap_vision.observation import JsonlSink, MockDetector
from zeoatap_vision.timing import Backoff
from zeoatap_vision.worker import VisionWorker, serve_health

TOKEN = "clip-token-123"


def post(port, body, token=TOKEN, ctype="application/json", raw=None, timeout=60):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/clips", data=data, method="POST")
    if ctype:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def idle_server(tmp_path):
    cfg = WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/none")])
    vw = VisionWorker(cfg, MockDetector(), JsonlSink(open(tmp_path / "o.jsonl", "w")))  # not started
    srv = serve_health(vw, "127.0.0.1", 0, token=TOKEN, clip_dir=tmp_path)
    yield srv.server_address[1], tmp_path
    srv.shutdown()


def test_clip_auth_and_validation(idle_server):
    port, d = idle_server
    now = time.time()
    good = {"camera_id": "cam1", "start": now - 20, "end": now - 10, "path": str(d / "a.mp4")}
    assert post(port, good, token=None)[0] == 401
    assert post(port, good, token="nope")[0] == 401
    assert post(port, good, ctype="text/plain")[0] == 415
    assert post(port, None, raw=b"{not json")[0] == 400
    for bad in ({**good, "start": "x"}, {**good, "end": good["start"] - 1}, {**good, "camera_id": ""},
                {**good, "path": "relative.mp4"}, {**good, "path": str(d / "a.txt")},
                {**good, "path": "/etc/evil.mp4"}, {**good, "extra": 1}, {**good, "start": True},
                {**good, "end": now + 10_000}, [1, 2]):
        code, body = post(port, bad)
        assert code == 400 and body["error"], bad
    code, body = post(port, {**good, "camera_id": "nope"})
    assert code == 404 and "unknown camera" in body["error"]
    code, body = post(port, good)  # camera never streamed: no video for a past window
    assert code == 422 and "no buffered video" in body["error"]


def test_clip_endpoint_disabled_without_token(tmp_path):
    vw = VisionWorker(WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/x")]), MockDetector(), JsonlSink())
    srv = serve_health(vw, "127.0.0.1", 0, token=None)
    try:
        code, body = post(srv.server_address[1], {"camera_id": "cam1", "start": 1, "end": 2, "path": "/tmp/x.mp4"})
        assert code == 503 and "disabled" in body["error"]
    finally:
        srv.shutdown()


@pytest.mark.skipif(not Path(MEDIAMTX).exists(), reason="needs MediaMTX")
def test_clip_over_live_rtsp(camera, tmp_path):  # noqa: F811
    cam = camera()
    cfg = WorkerConfig([CameraConfig(id="cam1", url=cam.url)])
    vw = VisionWorker(cfg, MockDetector(), JsonlSink(open(tmp_path / "o.jsonl", "w")))
    for c in vw.cameras.values():
        c.backoff = Backoff(0.2, 1.0)
    vw.start()
    srv = serve_health(vw, "127.0.0.1", 0, token=TOKEN, clip_dir=tmp_path)
    port = srv.server_address[1]
    try:
        assert wait_for(lambda: vw.cameras["cam1"].state == StreamState.STREAMING), vw.cameras["cam1"].stats.last_error
        time.sleep(4)
        now = time.time()
        out = tmp_path / "spool" / "inc-1.mp4"
        t0 = time.time()
        code, body = post(port, {"camera_id": "cam1", "start": now - 3, "end": now + 1.5, "path": str(out)})
        waited = time.time() - t0
        assert code == 200, body
        assert body["path"] == str(out) and body["partial"] is False
        assert body["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
        assert 1.0 <= waited < 15, waited  # blocked until the window's end had been recorded
        # a window older than the ring: 422 straight away
        code, body = post(port, {"camera_id": "cam1", "start": now - 3600, "end": now - 3500,
                                 "path": str(tmp_path / "old.mp4")})
        assert code == 422
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5).read())
        assert st["cameras"][0]["clips_written_total"] == 1
    finally:
        srv.shutdown()
        vw.stop()
