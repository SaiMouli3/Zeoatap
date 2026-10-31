"""CVAT import and inter-annotator agreement.

Annotators label each safety event in CVAT as a *track* (any shape, usually
a box around the subject) whose label is the event_type. The track is
visible (outside=0) from the event's start frame and gets an outside=1
keyframe on the first frame after the event ends. Per-track attributes:
visibility (visible|partial|not_visible), annotator_confidence
(certain|likely|unsure), room_zone (text). Export as "CVAT for video 1.1".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .matching import temporal_iou


@dataclass
class CvatEvent:
    label: str
    start_s: float
    end_s: float
    attrs: dict
    track_id: str


def parse_cvat_video_xml(path: str, fps: float, labels: set[str] | None = None) -> list[CvatEvent]:
    root = ET.parse(path).getroot()
    out: list[CvatEvent] = []
    for tr in root.iter("track"):
        label = tr.get("label", "")
        if labels and label not in labels:
            continue
        shapes = sorted((s for s in tr if s.get("frame") is not None), key=lambda s: int(s.get("frame")))
        if not shapes:
            continue
        # A track can leave and re-enter the frame; every visible run is one event.
        run_start = None
        attrs: dict = {}
        last_visible = None
        for s in shapes:
            f = int(s.get("frame"))
            if s.get("outside", "0") == "0":
                if run_start is None:
                    run_start = f
                    attrs = {a.get("name"): (a.text or "").strip() for a in s.findall("attribute")}
                last_visible = f
            elif run_start is not None:
                out.append(CvatEvent(label, run_start / fps, (f - 1) / fps, attrs, tr.get("id", "")))
                run_start = None
        if run_start is not None:
            out.append(CvatEvent(label, run_start / fps, last_visible / fps, attrs, tr.get("id", "")))
    return sorted(out, key=lambda e: (e.start_s, e.label))


def match_annotators(a: list[CvatEvent], b: list[CvatEvent], min_iou: float = 0.0):
    """One-to-one match of two annotators' events with the same label
    (Hungarian on temporal IoU, overlapping pairs only)."""
    pairs, ua, ub = [], set(range(len(a))), set(range(len(b)))
    if a and b:
        w = np.zeros((len(a), len(b)))
        for i, x in enumerate(a):
            for j, y in enumerate(b):
                if x.label == y.label:
                    iou = temporal_iou(x.start_s, x.end_s, y.start_s, y.end_s)
                    if iou > min_iou:
                        w[i, j] = iou
        for i, j in zip(*linear_sum_assignment(w, maximize=True)):
            if w[i, j] > 0:
                pairs.append((a[i], b[j], float(w[i, j])))
                ua.discard(i)
                ub.discard(j)
    return pairs, [a[i] for i in sorted(ua)], [b[j] for j in sorted(ub)]


def cohens_kappa_bins(a: list[CvatEvent], b: list[CvatEvent], duration_s: float, bin_s: float = 1.0) -> dict:
    """Cohen's kappa per label on "event active in this bin" over the session."""
    nb = max(1, int(np.ceil(duration_s / bin_s)))
    out = {}
    for label in sorted({e.label for e in a + b}):
        def mask(evs):
            m = np.zeros(nb, dtype=bool)
            for e in evs:
                if e.label == label:
                    m[int(e.start_s // bin_s): int(e.end_s // bin_s) + 1] = True
            return m
        x, y = mask(a), mask(b)
        po = float(np.mean(x == y))
        pe = float(x.mean() * y.mean() + (1 - x.mean()) * (1 - y.mean()))
        out[label] = None if pe == 1 else (po - pe) / (1 - pe)
    return out


def to_gt_events(pairs, only_a, only_b, ann_a: str, ann_b: str, prefix: str) -> tuple[list[dict], list[dict]]:
    """Agreed events (both annotators) become GT events awaiting adjudication;
    events only one annotator marked go to the adjudication queue."""
    agreed, queue = [], []
    for k, (x, y, iou) in enumerate(pairs):
        agreed.append({
            "id": f"{prefix}-{k + 1:03d}", "event_type": x.label,
            **({"room_zone": x.attrs["room_zone"]} if x.attrs.get("room_zone") else {}),
            # Earliest start, latest end: the adjudicator narrows these if needed.
            "start_s": round(min(x.start_s, y.start_s), 3), "end_s": round(max(x.end_s, y.end_s), 3),
            "visibility": x.attrs.get("visibility") or "visible",
            "annotator_confidence": _weaker(x.attrs.get("annotator_confidence"), y.attrs.get("annotator_confidence")),
            "annotators": [ann_a, ann_b], "adjudicated": False,
            "notes": f"temporal IoU between annotators {iou:.3f}",
        })
    for who, evs in ((ann_a, only_a), (ann_b, only_b)):
        for e in evs:
            queue.append({"annotator": who, "event_type": e.label, "start_s": round(e.start_s, 3),
                          "end_s": round(e.end_s, 3), "attrs": e.attrs, "track_id": e.track_id})
    return agreed, queue


_CONF = ["certain", "likely", "unsure"]


def _weaker(a, b):
    vals = [v for v in (a, b) if v in _CONF] or ["likely"]
    return max(vals, key=_CONF.index)


def group_by_label(evs: list[CvatEvent]) -> dict[str, int]:
    d: dict[str, int] = defaultdict(int)
    for e in evs:
        d[e.label] += 1
    return dict(d)
