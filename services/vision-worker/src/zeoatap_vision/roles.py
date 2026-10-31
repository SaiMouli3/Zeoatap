"""Infant vs adult role classification.

COCO / Body7 models only know "person". Until a detector fine-tuned on
{infant, adult} exists (AI_MODEL_STRATEGY §4, §10), roles come from a
pluggable RoleClassifier. The baseline here is a body-proportion heuristic:

  UNVALIDATED. It has never been measured on infants or on CCTV footage.
  It must not be relied on for safety decisions; it exists so the rest of the
  pipeline (tracker, held state, event engine) can be exercised end to end.

Cues (each mapped to [-1, +1], + = infant-like, then averaged):
  1. head width / shoulder width (ear-to-ear vs shoulder-to-shoulder).
     Adults ~0.35-0.45, infants ~0.6-0.8 (infant heads are large relative
     to the body). Ratio of two widths, so it is fairly robust to distance.
  2. head width / torso length (ears vs shoulder-to-hip). Adults ~0.3,
     infants ~0.6+.
  3. relative size: sqrt(box area) / sqrt(largest box area in the frame),
     only when 2+ people are visible. Infants are much smaller than adults
     at similar depth; fails when an adult is far from the camera.
Per track, the infant probability is smoothed with an EMA and the role has
hysteresis, so a single odd frame does not flip it. With no usable cue a new
track defaults to "adult" (documented limitation: an infant whose keypoints
are all hidden and who is alone in view is labelled adult).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

# COCO-17 keypoint indices
L_EAR, R_EAR, L_SHO, R_SHO, L_HIP, R_HIP = 3, 4, 5, 6, 11, 12
KPT_VISIBLE = 0.3

VERSION = "proportion-heuristic-v0-UNVALIDATED"


@dataclass
class PersonView:
    box: np.ndarray  # pixel xyxy
    keypoints: np.ndarray | None = None  # (17,2) pixels
    kpt_scores: np.ndarray | None = None  # (17,)


class RoleClassifier(Protocol):
    version: str

    def infant_probability(self, people: list[PersonView]) -> list[float | None]:
        """Per person, P(infant) in [0,1], or None when there is no evidence."""
        ...


def _dist(k: np.ndarray, s: np.ndarray, a: int, b: int) -> float | None:
    if s[a] < KPT_VISIBLE or s[b] < KPT_VISIBLE:
        return None
    return float(np.hypot(*(k[a] - k[b])))


def proportion_cues(p: PersonView) -> dict[str, float]:
    """Raw ratios that are available for this person."""
    out: dict[str, float] = {}
    if p.keypoints is None or p.kpt_scores is None:
        return out
    k, s = p.keypoints, p.kpt_scores
    head = _dist(k, s, L_EAR, R_EAR)
    sho = _dist(k, s, L_SHO, R_SHO)
    torsos = [d for d in (_dist(k, s, L_SHO, L_HIP), _dist(k, s, R_SHO, R_HIP)) if d]
    if head and sho and sho > 4:
        out["head_shoulder"] = head / sho
    if head and torsos and np.mean(torsos) > 4:
        out["head_torso"] = head / float(np.mean(torsos))
    return out


class ProportionRoleClassifier:
    version = VERSION

    def __init__(self, head_shoulder_mid: float = 0.52, head_torso_mid: float = 0.45,
                 rel_size_mid: float = 0.6, spread: float = 0.15):
        self.hs_mid, self.ht_mid, self.rel_mid, self.spread = head_shoulder_mid, head_torso_mid, rel_size_mid, spread

    def infant_probability(self, people: list[PersonView]) -> list[float | None]:
        sizes = [float(np.sqrt(max((p.box[2] - p.box[0]) * (p.box[3] - p.box[1]), 0.0))) for p in people]
        biggest = max(sizes, default=0.0)
        out: list[float | None] = []
        for p, size in zip(people, sizes):
            ev = []
            cues = proportion_cues(p)
            if "head_shoulder" in cues:
                ev.append(np.clip((cues["head_shoulder"] - self.hs_mid) / self.spread, -1, 1))
            if "head_torso" in cues:
                ev.append(np.clip((cues["head_torso"] - self.ht_mid) / self.spread, -1, 1))
            if len(people) >= 2 and biggest > 0:
                ev.append(np.clip((self.rel_mid - size / biggest) / (self.spread * 1.5), -1, 1))
            out.append(None if not ev else float(0.5 + 0.5 * np.mean(ev)))
        return out


class RoleSmoother:
    """Per-track EMA of P(infant) with hysteresis. State lives in track.data."""

    def __init__(self, alpha: float = 0.2, up: float = 0.6, down: float = 0.4):
        self.alpha, self.up, self.down = alpha, up, down

    def update(self, data: dict, p: float | None) -> str:
        if p is not None:
            prev = data.get("p_infant", 0.5)  # neutral prior: a new track needs 2+ frames of evidence
            data["p_infant"] = (1 - self.alpha) * prev + self.alpha * p
        role = data.get("role", "adult")
        ema = data.get("p_infant")
        if ema is not None:
            if role == "adult" and ema >= self.up:
                role = "infant"
            elif role == "infant" and ema <= self.down:
                role = "adult"
        data["role"] = role
        return role
