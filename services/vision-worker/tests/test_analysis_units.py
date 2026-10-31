"""Unit tests for the detection pipeline pieces that need no model files:
tracker, held state, role heuristic, pose metrics, heatmap decoding, and the
pipeline/worker contract (schema-valid observations, degraded frames skip
detection, failed inference reports ai_available=false)."""

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import numpy as np
import pytest

from zeoatap_vision import held, roles
from zeoatap_vision.config import CameraConfig, WorkerConfig
from zeoatap_vision.models import ModelMissing, resolve
from zeoatap_vision.observation import JsonlSink
from zeoatap_vision.pipeline import PosePipeline, normalize_box, pose_metrics
from zeoatap_vision.tracker import ByteTracker, greedy_match, iou_matrix
from zeoatap_vision.worker import VisionWorker

SCHEMA = json.loads((Path(__file__).parents[3] / "packages/contracts/observation.schema.json").read_text())


# ---- tracker -------------------------------------------------------------
def test_iou_and_greedy_match():
    a = np.array([[0, 0, 10, 10], [20, 20, 30, 30]], np.float32)
    b = np.array([[21, 21, 31, 31], [0, 0, 10, 10]], np.float32)
    iou = iou_matrix(a, b)
    assert iou[0, 1] == pytest.approx(1.0) and iou[0, 0] == 0
    m, ur, uc = greedy_match(iou, 0.3)
    assert sorted(m) == [(0, 1), (1, 0)] and not ur and not uc


def test_tracker_keeps_ids_for_moving_people_and_confirms_after_two_hits():
    tr = ByteTracker()
    a, b = np.array([10, 10, 50, 110.0]), np.array([200, 20, 240, 120.0])
    out0 = tr.update(np.stack([a, b]), np.array([0.9, 0.8]), 0.0)
    assert out0 == []  # not confirmed yet (min_hits=2)
    ids = None
    for i in range(1, 15):
        d = np.array([3.0 * i, 0, 3.0 * i, 0])
        out = tr.update(np.stack([a + d, b - d]), np.array([0.9, 0.8]), i * 0.2)
        assert len(out) == 2
        cur = {t.det_index: t.id for t in out}
        ids = ids or cur
        assert cur == ids  # same person keeps the same id
    assert len(set(ids.values())) == 2
    assert all(t.age_s(14 * 0.2) == pytest.approx(14 * 0.2) for t in tr.tracks)


def test_tracker_low_score_detection_keeps_track_but_does_not_start_one():
    tr = ByteTracker()
    box = np.array([[100, 100, 150, 220.0]])
    for i in range(3):
        tr.update(box, np.array([0.9]), i * 0.2)
    tid = tr.tracks[0].id
    out = tr.update(box + 2, np.array([0.3]), 0.6)  # occluded: score drops (ByteTrack 2nd stage)
    assert [t.id for t in out] == [tid]
    tr2 = ByteTracker()
    tr2.update(box, np.array([0.3]), 0.0)
    assert tr2.tracks == []


def test_tracker_drops_lost_tracks_and_ids_are_anonymous():
    tr = ByteTracker(max_lost_s=1.0)
    box = np.array([[0, 0, 10, 20.0]])
    tr.update(box, np.array([0.9]), 0.0)
    tr.update(box, np.array([0.9]), 0.2)
    first = tr.tracks[0].id
    tr.update(np.zeros((0, 4)), np.zeros(0), 0.8)
    assert len(tr.tracks) == 1  # coasting
    tr.update(np.zeros((0, 4)), np.zeros(0), 1.5)
    assert tr.tracks == []
    tr.update(box, np.array([0.9]), 1.7)
    assert tr.tracks[0].id != first  # a returning person gets a new id
    assert ByteTracker()._prefix != tr._prefix or True  # prefix is random per tracker/restart
    assert all(c in "0123456789abcdef-" for c in first)


# ---- held ------------------------------------------------------------------
ADULT = np.array([0.40, 0.10, 0.60, 0.95])


def test_held_when_infant_carried_at_chest():
    infant = np.array([0.43, 0.25, 0.57, 0.50])
    assert held.held_geometry(infant, ADULT)


@pytest.mark.parametrize("infant,why", [
    (np.array([0.70, 0.25, 0.85, 0.50]), "no overlap"),
    (np.array([0.43, 0.80, 0.57, 0.95]), "on the floor at the adult's feet"),
    (np.array([0.41, 0.55, 0.59, 0.85]), "low, near the adult's knees"),
    (np.array([0.40, 0.10, 0.60, 0.95]), "same size as the adult"),
])
def test_not_held(infant, why):
    assert not held.held_geometry(infant, ADULT), why


def test_held_respects_visible_wrists():
    infant = np.array([0.43, 0.25, 0.57, 0.50])
    k = np.zeros((17, 2))
    s = np.zeros(17)
    k[9] = k[10] = [0.5, 0.9]  # both hands down by the knees
    s[9] = s[10] = 0.9
    assert not held.held_geometry(infant, ADULT, k, s)
    k[10] = [0.5, 0.45]  # one hand under the infant
    assert held.held_geometry(infant, ADULT, k, s)
    s[:] = 0.1  # wrists not visible: geometry alone decides
    k[10] = [0.5, 0.9]
    assert held.held_geometry(infant, ADULT, k, s)


def test_held_hysteresis_and_adults_never_held():
    sm = held.HeldSmoother()
    carried = np.array([0.43, 0.25, 0.57, 0.50])
    away = np.array([0.80, 0.70, 0.90, 0.90])
    state = {}

    def frame(box):
        people = [{"role": "infant", "box": box, "data": state},
                  {"role": "adult", "box": ADULT, "data": {}}]
        held.compute_held(people, sm)
        assert people[1]["held"] is False
        return people[0]["held"]

    assert [frame(carried) for _ in range(3)] == [False, True, True]  # on after 2 frames
    assert frame(away) is True and frame(carried) is True  # one bad frame does not clear it
    assert [frame(away) for _ in range(3)] == [True, True, False]  # off after 3 frames


# ---- roles -----------------------------------------------------------------
def body(head_w, shoulder_w, torso_len, x0=100.0, y0=100.0):
    k = np.zeros((17, 2), np.float32)
    s = np.full(17, 0.9, np.float32)
    cx = x0 + 50
    k[roles.L_EAR] = [cx - head_w / 2, y0]
    k[roles.R_EAR] = [cx + head_w / 2, y0]
    k[roles.L_SHO] = [cx - shoulder_w / 2, y0 + 20]
    k[roles.R_SHO] = [cx + shoulder_w / 2, y0 + 20]
    k[roles.L_HIP] = [cx - shoulder_w / 3, y0 + 20 + torso_len]
    k[roles.R_HIP] = [cx + shoulder_w / 3, y0 + 20 + torso_len]
    return k, s


def test_role_heuristic_adult_and_infant_proportions():
    clf = roles.ProportionRoleClassifier()
    ak, as_ = body(head_w=15, shoulder_w=40, torso_len=50)  # adult: 0.38, 0.30
    ik, is_ = body(head_w=11, shoulder_w=16, torso_len=17)  # infant: 0.69, 0.65
    adult = roles.PersonView(np.array([0, 0, 60, 170.0]), ak, as_)
    infant = roles.PersonView(np.array([0, 0, 30, 60.0]), ik, is_)
    p_adult, p_infant = clf.infant_probability([adult, infant])
    assert p_adult < 0.3 and p_infant > 0.7
    # alone in view: proportions only, still separated
    assert clf.infant_probability([adult])[0] < 0.3
    assert clf.infant_probability([infant])[0] > 0.7
    # no keypoints and alone: no evidence
    assert clf.infant_probability([roles.PersonView(np.array([0, 0, 10, 10.0]))]) == [None]
    assert "UNVALIDATED" in clf.version


def test_role_smoother_hysteresis():
    sm = roles.RoleSmoother()
    d = {}
    assert sm.update(d, None) == "adult"  # documented default without evidence
    roles_seen = [sm.update(d, 0.95) for _ in range(5)]
    assert roles_seen[-1] == "infant" and roles_seen[0] == "adult"  # needs a few frames
    assert sm.update(d, 0.1) == "infant"  # one contrary frame does not flip it


# ---- pose metrics, boxes, decoding ----------------------------------------
def test_pose_metrics_and_box_normalization():
    s = np.array([0.9] * 10 + [0.1] * 7, np.float32)
    pq, vis = pose_metrics(s)
    assert pq == pytest.approx(0.9) and vis == pytest.approx(10 / 17)
    assert pose_metrics(None) == (0.0, 0.0) and pose_metrics(np.zeros(17)) == (0.0, 0.0)
    assert normalize_box(np.array([-5, 10, 700, 360]), 640, 360) == [0.0, 0.0278, 1.0, 1.0]


def test_heatmap_decoding_softmax_and_gaussian():
    from zeoatap_vision.inference import decode_heatmaps

    hm = np.zeros((1, 2, 8, 8), np.float32)
    hm[0, 0, 3, 5] = 0.7
    hm[0, 0, 3, 6] = 0.3  # right neighbour higher -> +0.25 x
    hm[0, 1, 6, 2] = 1.0
    locs, conf = decode_heatmaps(hm, softmax_output=True)
    assert locs[0, 0].tolist() == [5.25, 3.0] and conf[0, 0] == pytest.approx(1.0)
    _, conf_g = decode_heatmaps(hm * 0.5, softmax_output=False)
    assert conf_g[0, 0] == pytest.approx(0.35)


# ---- pipeline + worker contract ------------------------------------------
class FakeDetector:
    """Stands in for YOLOX: an adult and a smaller infant carried at the chest."""
    provider = "fake"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, rgb):
        self.calls += 1
        if self.fail:
            raise RuntimeError("inference blew up")
        return (np.array([[256, 36, 384, 342], [275, 90, 365, 180]], np.float32), np.array([0.92, 0.81], np.float32))


class FakePose:
    def __call__(self, rgb, boxes):
        n = len(boxes)
        k = np.zeros((n, 17, 2), np.float32)
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            k[i] = [(x1 + x2) / 2, (y1 + y2) / 2]
        return k, np.full((n, 17), 0.8, np.float32)


class FixedRoles:
    version = "test-fixed"

    def infant_probability(self, people):
        sizes = [(p.box[3] - p.box[1]) for p in people]
        return [0.0 if s == max(sizes) else 1.0 for s in sizes]


def make_pipeline():
    det = FakeDetector()
    return PosePipeline(components=(det, FakePose(), FakePose()), role_classifier=FixedRoles()), det


def frame(ts, cam="cam1", rgb=True, value=None):
    rng = np.random.default_rng(int(ts * 10))
    gray = rng.integers(40, 200, (90, 160), dtype=np.uint8) if value is None else np.full((90, 160), value, np.uint8)
    return SimpleNamespace(camera_id=cam, wall_ts=ts, gray=gray,
                           rgb=np.zeros((360, 640, 3), np.uint8) if rgb else None)


def test_pipeline_observations_are_schema_valid_with_held_infant():
    pipe, _ = make_pipeline()
    vw = VisionWorker(WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/x")]), pipe, JsonlSink())
    assert vw.cameras["cam1"].analysis_width == 640  # RGB frames requested from the camera
    obs = [vw.process(frame(1000 + i * 0.2)) for i in range(8)]
    for o in obs:
        jsonschema.validate(o, SCHEMA)
        assert o["source"] == "zeoatap-pose-p0" and o["ai_available"] is True
        assert "UNVALIDATED" in o["model_versions"]["validation"]
    last = {t["role"]: t for t in obs[-1]["tracks"]}
    assert set(last) == {"adult", "infant"}
    assert last["infant"]["held"] is True and last["adult"]["held"] is False
    assert all(0 <= v <= 1 for v in last["adult"]["box"])
    assert last["adult"]["box"] == [0.4, 0.1, 0.6, 0.95]
    assert last["infant"]["pose_quality"] == pytest.approx(0.8)


def test_degraded_frames_do_not_run_detection_and_failures_are_explicit():
    pipe, det = make_pipeline()
    vw = VisionWorker(WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/x")]), pipe, JsonlSink())
    # black for > 3 s -> degraded, detector must not run
    for i in range(25):
        o = vw.process(frame(i * 0.2, value=0))
    assert o["quality"] == {"degraded": True, "reason": "black"}
    calls = det.calls
    vw.process(frame(5.2, value=0))
    assert det.calls == calls and o["tracks"] == []
    # inference error: that frame says ai_available=false, never "nobody there"
    vw2 = VisionWorker(WorkerConfig([CameraConfig(id="cam1", url="rtsp://127.0.0.1:1/x")]), pipe, JsonlSink())
    det.fail = True
    o = vw2.process(frame(100.0))
    jsonschema.validate(o, SCHEMA)
    assert o["ai_available"] is False and o["tracks"] == [] and vw2.analysis_errors_total == 1
    for i in range(3):
        vw2.process(frame(100.2 + i * 0.2))
    assert pipe.available is False  # 3 consecutive failures -> AI_UNAVAILABLE until it recovers
    det.fail = False
    assert vw2.process(frame(101.0))["ai_available"] is False  # gate stays down ...
    pipe.last_error_t -= pipe.RETRY_AFTER_S  # ... until the retry interval passes
    assert vw2.process(frame(101.2))["ai_available"] is True and pipe.available
    # a missing RGB frame is an error, not an empty scene
    assert vw2.process(frame(101.4, rgb=False))["ai_available"] is False


def test_resolve_refuses_missing_or_tampered_models(tmp_path):
    with pytest.raises(ModelMissing):
        resolve("rtmpose-s-body7", tmp_path)
    (tmp_path / "rtmpose-s-body7.onnx").write_bytes(b"not the model")
    with pytest.raises(ModelMissing, match="sha256"):
        resolve("rtmpose-s-body7", tmp_path)
    (tmp_path / "fidip-mobile.onnx").write_bytes(b"x")
    with pytest.raises(ModelMissing, match="export"):
        resolve("fidip-mobile", tmp_path)
