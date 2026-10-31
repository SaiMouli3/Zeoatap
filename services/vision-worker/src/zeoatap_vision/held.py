"""Infant-held state (AI_MODEL_STRATEGY §3.1): an infant box that substantially
overlaps an adult box with an elevated position relative to that adult.

Geometry (normalized or pixel boxes, y down), for infant I and adult A:
  overlap   = area(I ∩ A) / area(I)            >= min_overlap      (0.5)
  elevated  = centroid_y(I) <= A.y1 + max_center_frac * height(A)   (0.65)
              and I.y2 <= A.y2 - min_bottom_clearance * height(A)  (0.15)
              (the infant is off the floor, carried in the adult's upper body)
  smaller   = height(I) <= max_height_ratio * height(A)             (0.9)
If the adult's wrists are confidently detected, at least one must lie within
the infant box expanded by `wrist_margin` (a carrying hand); if wrists are not
visible the geometric test alone decides.

Temporal hysteresis per infant track: held turns on after `on_frames`
consecutive positive frames and off after `off_frames` consecutive negative
frames, so a single bad detection does not flip suppression rules in core.

Known confusion (UNVALIDATED, Phase 2 test): an infant lying on a changing
table directly in front of a standing adult is geometrically similar to
being carried at waist height; the clearance test reduces but does not remove
this. The event engine must treat `held` as evidence, not truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

L_WRIST, R_WRIST = 9, 10
VERSION = "held-geom-v0-UNVALIDATED"


@dataclass
class HeldParams:
    min_overlap: float = 0.5
    max_center_frac: float = 0.65
    min_bottom_clearance: float = 0.15
    max_height_ratio: float = 0.9
    wrist_conf: float = 0.4
    wrist_margin: float = 0.25
    on_frames: int = 2
    off_frames: int = 3


def _area(b) -> float:
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _inter(a, b) -> float:
    return max(min(a[2], b[2]) - max(a[0], b[0]), 0.0) * max(min(a[3], b[3]) - max(a[1], b[1]), 0.0)


def held_geometry(infant_box, adult_box, adult_kpts=None, adult_scores=None, p: HeldParams | None = None) -> bool:
    """Instantaneous (single-frame) held test for one infant/adult pair."""
    p = p or HeldParams()
    ia = _area(infant_box)
    if ia <= 0 or _area(adult_box) <= 0:
        return False
    ah = adult_box[3] - adult_box[1]
    ih = infant_box[3] - infant_box[1]
    if _inter(infant_box, adult_box) / ia < p.min_overlap:
        return False
    if ih > p.max_height_ratio * ah:
        return False
    cy = (infant_box[1] + infant_box[3]) / 2
    if cy > adult_box[1] + p.max_center_frac * ah:
        return False
    if infant_box[3] > adult_box[3] - p.min_bottom_clearance * ah:
        return False
    if adult_kpts is not None and adult_scores is not None:
        wrists = [adult_kpts[i] for i in (L_WRIST, R_WRIST) if adult_scores[i] >= p.wrist_conf]
        if wrists:
            mx = p.wrist_margin * (infant_box[2] - infant_box[0])
            my = p.wrist_margin * ih
            if not any(infant_box[0] - mx <= w[0] <= infant_box[2] + mx and
                       infant_box[1] - my <= w[1] <= infant_box[3] + my for w in wrists):
                return False
    return True


def raw_held(infant_box, adults: list[tuple], p: HeldParams | None = None) -> bool:
    """adults: list of (box, kpts|None, scores|None)."""
    return any(held_geometry(infant_box, b, k, s, p) for b, k, s in adults)


class HeldSmoother:
    def __init__(self, p: HeldParams | None = None):
        self.p = p or HeldParams()

    def update(self, data: dict, raw: bool) -> bool:
        held = data.get("held", False)
        if raw:
            data["held_pos"] = data.get("held_pos", 0) + 1
            data["held_neg"] = 0
            if not held and data["held_pos"] >= self.p.on_frames:
                held = True
        else:
            data["held_neg"] = data.get("held_neg", 0) + 1
            data["held_pos"] = 0
            if held and data["held_neg"] >= self.p.off_frames:
                held = False
        data["held"] = held
        return held


def compute_held(people: list[dict], smoother: HeldSmoother) -> None:
    """people: dicts with 'role', 'box', optional 'kpts'/'kpt_scores' and
    'data' (the track's state dict). Sets 'held' on every person: infants by
    the smoothed test, adults always False."""
    adults = [(np.asarray(q["box"]), q.get("kpts"), q.get("kpt_scores")) for q in people if q["role"] == "adult"]
    for q in people:
        if q["role"] == "infant":
            q["held"] = smoother.update(q["data"], raw_held(np.asarray(q["box"]), adults, smoother.p))
        else:
            q["data"].pop("held", None)
            q["held"] = False
