"""Detection smoke test on a real photo of ADULTS (NASA, public domain; see
scripts/test_media.py). No children and no footage. Skipped unless the
models and the photo have been fetched:

  uv run python scripts/fetch_models.py --test-media
  (FiDIP checks additionally need scripts/export_fidip.py)

This checks that the pipeline runs end to end and produces contract-valid
output. It is NOT an accuracy evaluation."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import numpy as np
import pytest

from zeoatap_vision.models import DEFAULT_DETECTOR, DEFAULT_POSE, available, default_model_dir

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_media import PHOTO  # noqa: E402

SCHEMA = json.loads((Path(__file__).parents[3] / "packages/contracts/observation.schema.json").read_text())
PHOTO_PATH = default_model_dir() / PHOTO["file"]

cv2 = pytest.importorskip("cv2", reason="needs the 'pose' extra (uv sync --extra pose)")
pytest.importorskip("onnxruntime", reason="needs the 'pose' extra")
pytestmark = pytest.mark.skipif(
    not (available([DEFAULT_DETECTOR, DEFAULT_POSE]) and PHOTO_PATH.exists()),
    reason="models/test photo not fetched (scripts/fetch_models.py --test-media)")


@pytest.fixture(scope="module")
def rgb():
    img = cv2.imread(str(PHOTO_PATH))[:, :, ::-1]
    h, w = img.shape[:2]
    return np.ascontiguousarray(cv2.resize(img, (640, round(640 * h / w / 2) * 2)))


@pytest.fixture(scope="module")
def pipeline():
    from zeoatap_vision.pipeline import PosePipeline

    return PosePipeline(threads=4)


def test_adults_detected_tracked_posed_and_schema_valid(rgb, pipeline):
    from zeoatap_vision.config import CameraConfig, WorkerConfig
    from zeoatap_vision.observation import JsonlSink
    from zeoatap_vision.worker import VisionWorker

    vw = VisionWorker(WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/x")]), pipeline, JsonlSink())
    rng = np.random.default_rng(0)
    obs = []
    for i in range(4):
        gray = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (160, 90))
        gray = np.clip(gray.astype(int) + rng.integers(-2, 3, gray.shape), 0, 255).astype(np.uint8)  # not frozen
        obs.append(vw.process(SimpleNamespace(camera_id="cam1", wall_ts=1000 + i * 0.2, gray=gray, rgb=rgb)))
    for o in obs:
        jsonschema.validate(o, SCHEMA)
        assert o["ai_available"] is True and o["source"] == "zeoatap-pose-p0"
        assert o["model_versions"]["detector"].startswith("yolox")
    tracks = obs[-1]["tracks"]
    assert len(tracks) >= PHOTO["min_adults"], tracks
    ids = {t["id"] for t in tracks}
    assert ids == {t["id"] for t in obs[-2]["tracks"]}  # static scene: stable ids
    assert all(t["role"] == "adult" and t["held"] is False for t in tracks), tracks
    big = sorted(tracks, key=lambda t: (t["box"][3] - t["box"][1]), reverse=True)[:4]
    for t in big:  # the four unoccluded astronauts in the foreground
        assert t["score"] > 0.5 and t["pose_quality"] > 0.5 and t["visibility"] > 0.8, t
        assert t["box"][3] - t["box"][1] > 0.3


@pytest.mark.skipif(not available(["fidip-hrnet-w48"]), reason="FiDIP not exported (scripts/export_fidip.py)")
def test_fidip_export_and_decoding_agree_with_rtmpose_on_adults(rgb):
    """FiDIP is trained on infants; on clearly visible adults its HRNet keypoints
    should still land close to RTMPose's. This guards the ONNX export and the
    heatmap decoding, not infant accuracy."""
    from zeoatap_vision.inference import InfantPoseEstimator, PersonDetector, PoseEstimator
    from zeoatap_vision.models import MODELS, resolve

    pr = ["CPUExecutionProvider"]
    boxes, scores = PersonDetector(resolve(DEFAULT_DETECTOR), MODELS[DEFAULT_DETECTOR].input_size, pr)(rgb)
    boxes = boxes[np.argsort(-(boxes[:, 3] - boxes[:, 1]))][:4]
    rk, _ = PoseEstimator(resolve(DEFAULT_POSE), MODELS[DEFAULT_POSE].input_size, pr)(rgb, boxes)
    fk, fs = InfantPoseEstimator(resolve("fidip-hrnet-w48"), MODELS["fidip-hrnet-w48"].input_size, pr,
                                 softmax_output=False)(rgb, boxes)
    rel = np.linalg.norm(fk - rk, axis=-1) / (boxes[:, 3] - boxes[:, 1])[:, None]
    assert np.median(rel) < 0.05, np.median(rel, axis=1)
    assert fs.min() >= 0 and fs.max() <= 1
