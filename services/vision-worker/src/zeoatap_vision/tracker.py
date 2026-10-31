"""ByteTrack-style multi-object tracker (Zhang et al., ECCV 2022), implemented
here without external dependencies.

- Constant-velocity Kalman filter on (cx, cy, aspect, h), as in ByteTrack.
- Two-stage association: high-score detections first against all tracks, then
  low-score detections against the tracks still unmatched (this is what lets
  ByteTrack keep partially occluded people, e.g. an infant held against an
  adult, whose detection score drops).
- Matching is greedy on IoU (highest first) instead of the Hungarian
  algorithm: identical for the few, well-separated people in a nursery room,
  and avoids a SciPy/lap dependency.
- IDs are short-lived and anonymous: a random per-tracker prefix plus a
  counter ("k3f9-7"). They never persist across restarts and are never linked
  to identity (ADR-003).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import numpy as np


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def greedy_match(iou: np.ndarray, thresh: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pairs (row, col) with IoU >= thresh, best first."""
    matches = []
    if iou.size:
        order = np.dstack(np.unravel_index(np.argsort(-iou, axis=None), iou.shape))[0]
        used_r, used_c = set(), set()
        for r, c in order:
            if iou[r, c] < thresh:
                break
            if r in used_r or c in used_c:
                continue
            used_r.add(int(r))
            used_c.add(int(c))
            matches.append((int(r), int(c)))
    mr = {m[0] for m in matches}
    mc = {m[1] for m in matches}
    return matches, [r for r in range(iou.shape[0]) if r not in mr], [c for c in range(iou.shape[1]) if c not in mc]


class KalmanBox:
    """State x = [cx, cy, a, h, vcx, vcy, va, vh] (ByteTrack's parameters)."""

    _std_pos, _std_vel = 1.0 / 20, 1.0 / 160

    def __init__(self, box: np.ndarray):
        z = self._to_z(box)
        self.x = np.r_[z, np.zeros(4)]
        h = z[3]
        std = [2 * self._std_pos * h, 2 * self._std_pos * h, 1e-2, 2 * self._std_pos * h,
               10 * self._std_vel * h, 10 * self._std_vel * h, 1e-5, 10 * self._std_vel * h]
        self.P = np.diag(np.square(std))
        self.F = np.eye(8)
        self.F[:4, 4:] = np.eye(4)
        self.H = np.eye(4, 8)

    @staticmethod
    def _to_z(b: np.ndarray) -> np.ndarray:
        w, h = b[2] - b[0], b[3] - b[1]
        return np.array([b[0] + w / 2, b[1] + h / 2, w / max(h, 1e-6), h], dtype=np.float64)

    def box(self) -> np.ndarray:
        cx, cy, a, h = self.x[:4]
        w = a * h
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], dtype=np.float32)

    def predict(self) -> None:
        h = self.x[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-2, self._std_pos * h,
               self._std_vel * h, self._std_vel * h, 1e-5, self._std_vel * h]
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + np.diag(np.square(std))

    def update(self, box: np.ndarray) -> None:
        z = self._to_z(box)
        h = self.x[3]
        R = np.diag(np.square([self._std_pos * h, self._std_pos * h, 1e-1, self._std_pos * h]))
        S = self.H @ self.P @ self.H.T + R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - self.H @ self.x)
        self.P = (np.eye(8) - K @ self.H) @ self.P


@dataclass
class Track:
    id: str
    kf: KalmanBox
    score: float
    first_ts: float
    last_ts: float
    hits: int = 1
    misses: int = 0
    det_index: int | None = None  # index into the current frame's detections, None if coasting
    data: dict = field(default_factory=dict)  # per-track state for later stages (role, held)

    @property
    def box(self) -> np.ndarray:
        return self.kf.box()

    def age_s(self, now: float) -> float:
        return now - self.first_ts


class ByteTracker:
    def __init__(self, high_thresh: float = 0.5, low_thresh: float = 0.1, new_track_thresh: float = 0.6,
                 match_iou: float = 0.2, low_match_iou: float = 0.5, max_lost_s: float = 2.0,
                 min_hits: int = 2):
        self.high, self.low, self.new = high_thresh, low_thresh, new_track_thresh
        self.match_iou, self.low_match_iou = match_iou, low_match_iou
        self.max_lost_s = max_lost_s
        self.min_hits = min_hits
        self.tracks: list[Track] = []
        self._prefix = secrets.token_hex(2)
        self._next = 1

    def _new_id(self) -> str:
        tid = f"{self._prefix}-{self._next}"
        self._next += 1
        return tid

    def update(self, boxes: np.ndarray, scores: np.ndarray, ts: float) -> list[Track]:
        """Feed one frame's detections (pixel xyxy). Returns the confirmed
        tracks seen in this frame (hits >= min_hits), each with det_index set."""
        boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
        scores = np.asarray(scores, dtype=np.float32).reshape(-1)
        for t in self.tracks:
            t.kf.predict()
            t.det_index = None
        hi = np.where(scores >= self.high)[0]
        lo = np.where((scores >= self.low) & (scores < self.high))[0]

        pred = np.array([t.box for t in self.tracks], dtype=np.float32).reshape(-1, 4)
        m1, un_t, un_hi = greedy_match(iou_matrix(pred, boxes[hi]), self.match_iou)
        for r, c in m1:
            self._hit(self.tracks[r], boxes[hi[c]], scores[hi[c]], int(hi[c]), ts)

        rest = [self.tracks[i] for i in un_t]
        pred2 = np.array([t.box for t in rest], dtype=np.float32).reshape(-1, 4)
        m2, un_t2, _ = greedy_match(iou_matrix(pred2, boxes[lo]), self.low_match_iou)
        for r, c in m2:
            self._hit(rest[r], boxes[lo[c]], scores[lo[c]], int(lo[c]), ts)
        for i in un_t2:
            rest[i].misses += 1

        for c in un_hi:
            d = int(hi[c])
            if scores[d] >= self.new:
                self.tracks.append(Track(self._new_id(), KalmanBox(boxes[d]), float(scores[d]), ts, ts, det_index=d))

        self.tracks = [t for t in self.tracks if ts - t.last_ts <= self.max_lost_s]
        return [t for t in self.tracks if t.det_index is not None and t.hits >= self.min_hits]

    @staticmethod
    def _hit(t: Track, box: np.ndarray, score: float, idx: int, ts: float) -> None:
        t.kf.update(box)
        t.score, t.last_ts, t.det_index = float(score), ts, idx
        t.hits += 1
        t.misses = 0
