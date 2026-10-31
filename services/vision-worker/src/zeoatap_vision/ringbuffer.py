"""Rolling buffer of *encoded* packets per camera. Clips are cut from it by
remuxing the original packets, so evidence is bit-exact and needs no encoder."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BufferedPacket:
    packet: Any  # av.Packet (or a test double with .size and .is_keyframe)
    wall_ts: float
    size: int
    keyframe: bool


class PacketRingBuffer:
    def __init__(self, max_seconds: float, max_bytes: int):
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self._items: deque[BufferedPacket] = deque()
        self._bytes = 0
        self._lock = threading.Lock()
        self.evicted_total = 0

    def append(self, packet: Any, wall_ts: float) -> None:
        item = BufferedPacket(packet, wall_ts, int(packet.size), bool(packet.is_keyframe))
        with self._lock:
            self._items.append(item)
            self._bytes += item.size
            while self._items and (
                wall_ts - self._items[0].wall_ts > self.max_seconds or self._bytes > self.max_bytes
            ):
                old = self._items.popleft()
                self._bytes -= old.size
                self.evicted_total += 1

    def clear(self) -> None:
        """Drop everything, e.g. after a reconnect when the codec may have changed."""
        with self._lock:
            self._items.clear()
            self._bytes = 0

    @property
    def newest_ts(self) -> float | None:
        with self._lock:
            return self._items[-1].wall_ts if self._items else None

    @property
    def oldest_ts(self) -> float | None:
        with self._lock:
            return self._items[0].wall_ts if self._items else None

    @property
    def bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._items)

    def snapshot(self, start: float, end: float) -> list[BufferedPacket]:
        """Packets covering [start, end], beginning at the last keyframe at or
        before start so the clip decodes from its first frame."""
        with self._lock:
            items = list(self._items)
        first = None
        for i, it in enumerate(items):
            if it.wall_ts > start:
                break
            if it.keyframe:
                first = i
        if first is None:
            first = next((i for i, it in enumerate(items) if it.keyframe), None)
            if first is None:
                return []
        return [it for it in items[first:] if it.wall_ts <= end]
