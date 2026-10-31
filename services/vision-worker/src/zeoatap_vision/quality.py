"""Frame-quality checks on a small grayscale thumbnail. A camera that shows a
black, covered or frozen image is reported as degraded; it is never treated
as "nothing happening"."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

REASON_BLACK = "black"
REASON_OBSTRUCTED = "obstructed"
REASON_FROZEN = "frozen"


@dataclass
class QualityThresholds:
    black_mean: float = 16.0  # mean luma (0-255) below this is "black"
    obstructed_std: float = 6.0  # almost no texture, e.g. a covered lens
    black_after_s: float = 3.0
    obstructed_after_s: float = 5.0
    # Identical decoded frames for this long. A perfectly static scene can also
    # produce identical frames (encoder skip blocks), so this is deliberately
    # long and must be tuned on real cameras (HURDLES H-20).
    frozen_after_s: float = 30.0
    recover_after_s: float = 5.0


@dataclass(frozen=True)
class QualityResult:
    degraded: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"degraded": self.degraded, "reason": self.reason}


class QualityMonitor:
    def __init__(self, thresholds: QualityThresholds | None = None):
        self.t = thresholds or QualityThresholds()
        self._since: dict[str, float] = {}
        self._last_hash: bytes | None = None
        self._current = QualityResult(False)
        self._ok_since: float | None = None

    def update(self, gray: np.ndarray, ts: float) -> QualityResult:
        mean = float(gray.mean())
        std = float(gray.std())
        digest = hashlib.blake2b(gray.tobytes(), digest_size=16).digest()

        raw = {
            REASON_BLACK: mean < self.t.black_mean,
            REASON_OBSTRUCTED: mean >= self.t.black_mean and std < self.t.obstructed_std,
            REASON_FROZEN: digest == self._last_hash,
        }
        self._last_hash = digest
        for reason, active in raw.items():
            if active:
                self._since.setdefault(reason, ts)
            else:
                self._since.pop(reason, None)

        after = {
            REASON_BLACK: self.t.black_after_s,
            REASON_OBSTRUCTED: self.t.obstructed_after_s,
            REASON_FROZEN: self.t.frozen_after_s,
        }
        firing = [r for r in (REASON_BLACK, REASON_OBSTRUCTED, REASON_FROZEN)
                  if r in self._since and ts - self._since[r] >= after[r]]

        if firing:
            self._current = QualityResult(True, firing[0])
            self._ok_since = None
        elif self._current.degraded:
            # Hysteresis: stay degraded until the image has been good for a while.
            if any(raw.values()):
                self._ok_since = None
            elif self._ok_since is None:
                self._ok_since = ts
            elif ts - self._ok_since >= self.t.recover_after_s:
                self._current = QualityResult(False)
                self._ok_since = None
        return self._current
