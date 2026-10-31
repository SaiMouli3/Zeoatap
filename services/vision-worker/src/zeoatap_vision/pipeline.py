"""Real detection pipeline (--detector pose):

  RGB frame (~640 px wide) -> YOLOX person detector (ONNX Runtime)
    -> ByteTrack-style tracker (per camera, anonymous short-lived ids)
    -> RTMPose on person crops (batched) -> pose_quality, visibility
    -> role classifier (infant/adult; UNVALIDATED heuristic baseline)
    -> FiDIP infant pose on crops classified as infant (replaces RTMPose
       keypoints for those tracks; RTMPose is the fallback if FiDIP is absent)
    -> held state (AI_MODEL_STRATEGY §3.1)
    -> Observation tracks with boxes normalized to [0,1].

All models are public pretrained weights, NOT trained or validated on infants
or on CCTV. Observations carry the pipeline id in `source` and every model's
version in `model_versions`, so core and reviewers can always tell what ran.
The detector only reports what it sees; safety decisions are made in core.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from . import held as held_mod
from . import roles as roles_mod
from .inference import InfantPoseEstimator, PersonDetector, PoseEstimator, pick_providers
from .models import DEFAULT_DETECTOR, DEFAULT_INFANT_POSE, DEFAULT_POSE, MODELS, ModelMissing, resolve
from .tracker import ByteTracker

log = logging.getLogger(__name__)

PIPELINE_ID = "zeoatap-pose-p0"
TRACKER_VERSION = "bytetrack-greedy-v1"
KPT_VISIBLE = 0.3


def pose_metrics(scores: np.ndarray | None) -> tuple[float, float]:
    """pose_quality = mean confidence over visible keypoints (score >= 0.3);
    visibility = fraction of the 17 keypoints that are visible. Both 0 when
    no pose was estimated."""
    if scores is None or len(scores) == 0:
        return 0.0, 0.0
    vis = scores >= KPT_VISIBLE
    if not vis.any():
        return 0.0, 0.0
    return float(np.clip(scores[vis].mean(), 0, 1)), float(vis.mean())


def normalize_box(box: np.ndarray, w: int, h: int) -> list[float]:
    x1, y1, x2, y2 = (float(v) for v in box)
    b = [x1 / w, y1 / h, x2 / w, y2 / h]
    return [round(min(max(v, 0.0), 1.0), 4) for v in b]


class PosePipeline:
    name = PIPELINE_ID

    def __init__(self, detector_model: str = DEFAULT_DETECTOR, pose_model: str = DEFAULT_POSE,
                 model_dir: str | None = None, device: str = "auto", threads: int | None = None,
                 frame_width: int = 640,
                 role_classifier: roles_mod.RoleClassifier | None = None,
                 held_params: held_mod.HeldParams | None = None, max_people: int = 16,
                 components: tuple | None = None, infant_pose_model: str | None = DEFAULT_INFANT_POSE):
        """components=(detector, pose) injects ready callables (tests); otherwise
        the ONNX models are resolved, hash-verified and loaded."""
        dspec, pspec = MODELS[detector_model], MODELS[pose_model]
        self.infant_pose = None
        ipspec = None
        if components is not None:
            self.detector, self.pose = components[:2]
            if len(components) > 2 and components[2] is not None:
                self.infant_pose, ipspec = components[2], MODELS[infant_pose_model or DEFAULT_INFANT_POSE]
        else:
            providers = pick_providers(device)
            # score_thr 0.1: low-score detections feed ByteTrack's second association stage.
            self.detector = PersonDetector(resolve(detector_model, model_dir), dspec.input_size, providers,
                                           score_thr=0.1, threads=threads)
            self.pose = PoseEstimator(resolve(pose_model, model_dir), pspec.input_size, providers,
                                      threads=threads, max_batch=max_people)
            if infant_pose_model:
                ipspec = MODELS[infant_pose_model]
                try:
                    self.infant_pose = InfantPoseEstimator(
                        resolve(infant_pose_model, model_dir), ipspec.input_size, providers,
                        softmax_output=infant_pose_model == "fidip-mobile", threads=threads, max_batch=max_people)
                except ModelMissing as e:
                    log.warning("infant pose model unavailable (%s); RTMPose is used for infants too", e)
                    ipspec = None
        self.frame_width = frame_width
        self.roles = role_classifier or roles_mod.ProportionRoleClassifier()
        self.role_smoother = roles_mod.RoleSmoother()
        self.held = held_mod.HeldSmoother(held_params)
        self.trackers: dict[str, ByteTracker] = {}
        self.provider = getattr(self.detector, "provider", "injected")
        self.model_versions = {
            "detector": dspec.version,
            "pose": pspec.version,
            "tracker": TRACKER_VERSION,
            "role": self.roles.version,
            "held": held_mod.VERSION,
            "infant_pose": ipspec.version if (ipspec and self.infant_pose is not None) else "none(rtmpose-fallback)",
            "runtime": f"onnxruntime/{self.provider}",
            "validation": "UNVALIDATED-pretrained-not-infant-not-cctv",
        }
        self._lock = threading.Lock()
        self.loaded = True
        self.frames_total = 0
        self.errors_total = 0
        self.consecutive_errors = 0
        self.last_error_t = 0.0
        self.last_ms = {"detect": 0.0, "track": 0.0, "pose": 0.0, "infant_pose": 0.0, "total": 0.0}
        self.total_ms_sum = 0.0

    RETRY_AFTER_S = 5.0

    @property
    def available(self) -> bool:
        """False after 3 consecutive failures (core shows AI_UNAVAILABLE); a
        new attempt is allowed every RETRY_AFTER_S and one success restores it."""
        if not self.loaded:
            return False
        return self.consecutive_errors < 3 or time.monotonic() - self.last_error_t >= self.RETRY_AFTER_S

    def detect(self, frame: np.ndarray) -> list[dict]:  # Detector protocol compatibility
        raise RuntimeError("PosePipeline needs analyze() with an RGB frame")

    def analyze(self, camera_id: str, ts: float, gray: np.ndarray, rgb: np.ndarray | None) -> list[dict]:
        if rgb is None:
            raise ValueError("no RGB frame (camera not configured for analysis frames)")
        with self._lock:  # one analysis thread today; the lock keeps state safe if that changes
            try:
                tracks = self._analyze(camera_id, ts, rgb)
            except Exception:
                self.errors_total += 1
                self.consecutive_errors += 1
                self.last_error_t = time.monotonic()
                raise
            self.consecutive_errors = 0
            return tracks

    def _analyze(self, camera_id: str, ts: float, rgb: np.ndarray) -> list[dict]:
        t0 = time.perf_counter()
        h, w = rgb.shape[:2]
        boxes, scores = self.detector(rgb)
        t1 = time.perf_counter()
        tracker = self.trackers.setdefault(camera_id, ByteTracker())
        confirmed = tracker.update(boxes, scores, ts)
        t2 = time.perf_counter()
        det_boxes = np.array([boxes[t.det_index] for t in confirmed], dtype=np.float32).reshape(-1, 4)
        kpts, kscores = self.pose(rgb, det_boxes)
        t3 = time.perf_counter()

        views = []
        for i, t in enumerate(confirmed):
            has = i < len(kpts)
            views.append(roles_mod.PersonView(det_boxes[i], kpts[i] if has else None, kscores[i] if has else None))
        probs = self.roles.infant_probability(views)

        people = []
        for i, t in enumerate(confirmed):
            role = self.role_smoother.update(t.data, probs[i])
            pq, vis = pose_metrics(kscores[i] if i < len(kscores) else None)
            people.append({"track": t, "role": role, "box": det_boxes[i], "data": t.data,
                           "kpts": kpts[i] if i < len(kpts) else None,
                           "kpt_scores": kscores[i] if i < len(kscores) else None,
                           "pose_quality": pq, "visibility": vis})
        t3b = time.perf_counter()
        infants = [p for p in people if p["role"] == "infant"]
        if infants and self.infant_pose is not None:
            ik, isc = self.infant_pose(rgb, np.stack([p["box"] for p in infants]))
            for p, k, sc in zip(infants, ik, isc):
                p["kpts"], p["kpt_scores"] = k, sc
                p["pose_quality"], p["visibility"] = pose_metrics(sc)
        t3c = time.perf_counter()
        held_mod.compute_held(people, self.held)

        out = [{
            "id": p["track"].id,
            "role": p["role"],
            "score": round(float(np.clip(p["track"].score, 0, 1)), 4),
            "box": normalize_box(p["box"], w, h),
            "pose_quality": round(p["pose_quality"], 4),
            "visibility": round(p["visibility"], 4),
            "held": bool(p["held"]),
        } for p in people]
        t4 = time.perf_counter()
        self.frames_total += 1
        self.last_ms = {"detect": (t1 - t0) * 1e3, "track": (t2 - t1) * 1e3,
                        "pose": (t3 - t2) * 1e3, "infant_pose": (t3c - t3b) * 1e3, "total": (t4 - t0) * 1e3}
        self.total_ms_sum += self.last_ms["total"]
        return out

    def status(self) -> dict:
        return {
            "pipeline": self.name,
            "provider": self.provider,
            "model_versions": self.model_versions,
            "frames_analyzed_total": self.frames_total,
            "inference_errors_total": self.errors_total,
            "last_ms": {k: round(v, 2) for k, v in self.last_ms.items()},
            "mean_total_ms": round(self.total_ms_sum / self.frames_total, 2) if self.frames_total else 0.0,
            "active_tracks": {cid: len(t.tracks) for cid, t in self.trackers.items()},
        }
