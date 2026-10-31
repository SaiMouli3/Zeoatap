"""Frame sampling and reconnect backoff."""

from __future__ import annotations

import random


class FrameSampler:
    """Picks frames at a target rate by timestamp, so variable or bursty input
    frame rates still produce an even analysis rate. burst() raises the rate
    temporarily while the event engine is watching a candidate."""

    def __init__(self, target_fps: float):
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        self.target_fps = target_fps
        self._next = None
        self._burst_fps = None
        self._burst_until = 0.0

    def burst(self, fps: float, until: float) -> None:
        self._burst_fps, self._burst_until = fps, until

    def should_sample(self, ts: float) -> bool:
        fps = self.target_fps
        if self._burst_fps and ts < self._burst_until:
            fps = max(fps, self._burst_fps)
        if self._next is not None and ts < self._next:
            return False
        interval = 1.0 / fps
        self._next = (self._next if self._next is not None else ts) + interval
        if self._next <= ts:  # after a gap, schedule from now instead of catching up
            self._next = ts + interval
        return True


class Backoff:
    """Exponential backoff with full jitter: 1 s, 2 s, 4 s … capped at 30 s."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0, rng: random.Random | None = None):
        self.initial, self.maximum = initial, maximum
        self._attempt = 0
        self._rng = rng or random.Random()

    def next_delay(self) -> float:
        cap = min(self.maximum, self.initial * (2 ** self._attempt))
        self._attempt += 1
        return self._rng.uniform(cap / 2, cap)

    def reset(self) -> None:
        self._attempt = 0
