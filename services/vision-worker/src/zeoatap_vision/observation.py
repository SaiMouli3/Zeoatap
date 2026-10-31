"""Observation builder (contract: packages/contracts/observation.schema.json)
and the detector interface. Detectors only report what they see."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from typing import Protocol, TextIO

import numpy as np

from .quality import QualityResult


class Detector(Protocol):
    name: str
    available: bool
    model_versions: dict[str, str]

    def detect(self, frame: np.ndarray) -> list[dict]: ...


class NoDetector:
    """No model loaded. Observations still flow so camera health is monitored,
    but ai_available=false makes core report AI_UNAVAILABLE, never green."""

    name = "none"
    available = False
    model_versions: dict[str, str] = {}

    def detect(self, frame: np.ndarray) -> list[dict]:
        return []


class MockDetector:
    """Returns no tracks and is labelled MOCK end to end. For pipeline tests
    only; it must never be presented as real detection."""

    name = "MOCK"
    available = True
    model_versions = {"detector": "mock-0"}

    def detect(self, frame: np.ndarray) -> list[dict]:
        return []


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_observation(camera_id: str, ts: float, quality: QualityResult, detector: Detector,
                      tracks: list[dict], ts_source: str = "receive") -> dict:
    return {
        "camera_id": camera_id,
        "capture_ts": iso_utc(ts),
        "ts_source": ts_source,
        "source": detector.name,
        "model_versions": dict(detector.model_versions),
        "ai_available": bool(detector.available),
        "quality": quality.as_dict(),
        "tracks": tracks,
    }


class JsonlSink:
    """Thread-safe JSON-lines writer (stdout or a file). The gRPC stream to
    core replaces this in a later task; the payload stays the same."""

    def __init__(self, out: TextIO | None = None):
        self._out = out or sys.stdout
        self._lock = threading.Lock()
        self.written = 0

    def emit(self, obs: dict) -> None:
        line = json.dumps(obs, separators=(",", ":"))
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()
            self.written += 1
