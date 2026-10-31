"""Live tests: real RTSP through MediaMTX with ffmpeg as the camera, synthetic
test patterns only (no people, no footage). Skipped unless MediaMTX is found:
set MEDIAMTX_BIN, or place the binary at <repo>/.tools/mediamtx."""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

from zeoatap_vision.camera import CameraWorker, StreamState
from zeoatap_vision.config import CameraConfig, WorkerConfig
from zeoatap_vision.observation import JsonlSink, MockDetector
from zeoatap_vision.timing import Backoff
from zeoatap_vision.worker import VisionWorker

REPO = Path(__file__).parents[3]
MEDIAMTX = os.environ.get("MEDIAMTX_BIN") or str(REPO / ".tools" / "mediamtx")
SCHEMA = json.loads((REPO / "packages/contracts/observation.schema.json").read_text())

pytestmark = pytest.mark.skipif(
    not (Path(MEDIAMTX).exists() and shutil.which("ffmpeg")),
    reason="needs MediaMTX (MEDIAMTX_BIN or .tools/mediamtx) and ffmpeg",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(cond, timeout=20.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def rtsp_server(tmp_path):
    port = free_port()
    conf = tmp_path / "mediamtx.yml"
    conf.write_text(
        f"logLevel: warn\nrtspAddress: 127.0.0.1:{port}\nrtsp: yes\nrtmp: no\nhls: no\n"
        "webrtc: no\nsrt: no\napi: no\nmetrics: no\npprof: no\nplayback: no\n"
        "paths:\n  all_others:\n"
    )
    proc = subprocess.Popen([MEDIAMTX, str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_for(lambda: _port_open(port), 10)
    yield port
    proc.terminate()
    proc.wait(10)


def _port_open(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class Camera:
    """ffmpeg publishing a synthetic pattern, like an IP camera would."""

    def __init__(self, port, path, pattern="testsrc2=size=640x360:rate=25"):
        self.url = f"rtsp://127.0.0.1:{port}/{path}"
        self.pattern = pattern
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", self.pattern,
             "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "25", "-pix_fmt", "yuv420p",
             "-f", "rtsp", "-rtsp_transport", "tcp", self.url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return self

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)
            self.proc = None


@pytest.fixture
def camera(rtsp_server):
    cams = []

    def make(path="cam1", **kw):
        c = Camera(rtsp_server, path, **kw).start()
        cams.append(c)
        return c

    yield make
    for c in cams:
        c.stop()


def fast(worker: CameraWorker) -> CameraWorker:
    worker.backoff = Backoff(0.2, 1.0)
    return worker


def test_connects_streams_and_samples_at_target_rate(camera):
    cam = camera()
    w = fast(CameraWorker(CameraConfig(id="cam1", url=cam.url, base_fps=5)))
    w.start()
    try:
        assert wait_for(lambda: w.state == StreamState.STREAMING), w.stats.last_error
        time.sleep(4)
        n0 = w.stats.frames_sampled_total
        time.sleep(4)
        rate = (w.stats.frames_sampled_total - n0) / 4
        assert 4.0 <= rate <= 6.0, rate
        assert w.stats.frames_decoded_total > w.stats.frames_sampled_total  # 25 fps in, 5 fps analyzed
        assert w.ring.oldest_ts is not None and len(w.ring) > 0
    finally:
        w.stop()


def test_camera_outage_reconnects_and_recovers(camera):
    cam = camera()
    w = fast(CameraWorker(CameraConfig(id="cam1", url=cam.url)))
    w.start()
    try:
        assert wait_for(lambda: w.state == StreamState.STREAMING)
        cam.stop()  # camera unplugged
        assert wait_for(lambda: w.state == StreamState.RECONNECTING, 15)
        assert w.stats.reconnects_total >= 1
        cam.start()  # camera back
        assert wait_for(lambda: w.state == StreamState.STREAMING, 20), w.stats.last_error
    finally:
        w.stop()


def test_protected_camera_samples_at_15fps(camera):
    cam = camera()
    w = fast(CameraWorker(CameraConfig(id="cam1", url=cam.url, protected=True)))
    w.start()
    try:
        assert wait_for(lambda: w.state == StreamState.STREAMING)
        time.sleep(3)
        n0 = w.stats.frames_sampled_total
        time.sleep(4)
        rate = (w.stats.frames_sampled_total - n0) / 4
        assert 13.0 <= rate <= 17.0, rate
    finally:
        w.stop()


def test_clip_is_remuxed_without_reencoding(camera, tmp_path):
    cam = camera()
    w = fast(CameraWorker(CameraConfig(id="cam1", url=cam.url)))
    w.start()
    try:
        assert wait_for(lambda: w.state == StreamState.STREAMING)
        time.sleep(4)
        now = time.time()
        fut = w.request_clip(now - 3, now + 2, tmp_path / "inc.mp4")
        path, sha, partial = fut.result(timeout=15)
        assert not partial and len(sha) == 64
        probe = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
             "-of", "json", str(path)]))
        assert probe["streams"][0]["codec_name"] == "h264"
        assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (640, 360)
        dur = float(probe["format"]["duration"])
        assert 4.0 <= dur <= 7.5, dur  # starts at the keyframe before t-3 s
        # The whole clip decodes without errors.
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and r.stderr == ""
    finally:
        w.stop()


def test_clip_during_outage_is_written_as_partial(camera, tmp_path):
    cam = camera()
    w = fast(CameraWorker(CameraConfig(id="cam1", url=cam.url)))
    w.start()
    try:
        assert wait_for(lambda: w.state == StreamState.STREAMING)
        time.sleep(3)
        now = time.time()
        fut = w.request_clip(now - 2, now + 30, tmp_path / "cut.mp4")
        cam.stop()
        path, sha, partial = fut.result(timeout=15)
        assert partial and path.exists() and path.stat().st_size > 0
    finally:
        w.stop()


def test_black_camera_is_reported_degraded(camera, tmp_path):
    cam = camera(pattern="color=c=black:size=640x360:rate=25")
    out = tmp_path / "obs.jsonl"
    cfg = WorkerConfig(cameras=[CameraConfig(id="cam1", url=cam.url)])
    with open(out, "w") as f:
        vw = VisionWorker(cfg, MockDetector(), JsonlSink(f))
        vw.start()
        try:
            assert wait_for(lambda: any(
                json.loads(l)["quality"]["reason"] == "black" for l in out.read_text().splitlines() if l), 15)
        finally:
            vw.stop()


def test_pipeline_into_core_reports_outage_and_recovery(camera, tmp_path):
    """vision-worker -> observations JSONL -> Go event engine. The engine must
    show the outage as CAMERA_UNAVAILABLE and only then return to operational."""
    if not shutil.which("go"):
        pytest.skip("needs Go")
    cam = camera()
    out = tmp_path / "obs.jsonl"
    cfg = WorkerConfig(cameras=[CameraConfig(id="cam1", url=cam.url)])
    with open(out, "w") as f:
        vw = VisionWorker(cfg, MockDetector(), JsonlSink(f))
        for c in vw.cameras.values():
            fast(c)
        vw.start()
        try:
            assert wait_for(lambda: vw.cameras["cam1"].state == StreamState.STREAMING)
            time.sleep(3)
            cam.stop()
            time.sleep(5)  # longer than core's 3 s staleness limit
            cam.start()
            assert wait_for(lambda: vw.cameras["cam1"].state == StreamState.STREAMING, 20)
            time.sleep(3)
        finally:
            vw.stop()

    lines = [json.loads(l) for l in out.read_text().splitlines() if l]
    assert len(lines) > 20
    for obs in lines[:50]:
        jsonschema.validate(obs, SCHEMA)
        assert obs["source"] == "MOCK"

    cams = tmp_path / "cameras.json"
    cams.write_text(json.dumps({"cameras": [{"id": "cam1", "room_id": "infants-a", "zones": []}]}))
    r = subprocess.run(["go", "run", "./cmd/replay", "-config", str(cams), "-observations", str(out)],
                       cwd=REPO / "services/core", capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    states = [json.loads(l)["state"] for l in r.stdout.splitlines() if '"state_change"' in l]
    assert "FULLY_OPERATIONAL" in states
    i = states.index("CAMERA_UNAVAILABLE")
    assert "FULLY_OPERATIONAL" in states[i + 1:], states
    assert "MOCK" in r.stderr  # the replay warns that detections are mock
