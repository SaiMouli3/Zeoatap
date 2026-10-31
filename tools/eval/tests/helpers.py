"""Builders for synthetic ground truth and incidents (no footage involved)."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 31, 9, 0, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


def iso(s: float) -> str:
    return (T0 + timedelta(seconds=s)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ev(start, end, event_type="infant_off_surface", eid=None, annotators=("a1", "a2"), **kw):
    return {"id": eid or f"E{next(_ids)}", "event_type": event_type, "start_s": start, "end_s": end,
            "visibility": "visible", "annotator_confidence": "certain", "annotators": list(annotators),
            "adjudicated": True, **kw}


def session(events, sid="S1", camera="cam1", span=(0, 3600), conditions=None, **kw):
    return {"session_id": sid, "facility_id": "F1", "camera_id": camera, "t0": iso(0),
            "conditions": conditions or {"lighting": "day", "occlusion": "none", "camera_angle": "high_oblique"},
            "operating_span": {"start_s": span[0], "end_s": span[1]}, "events": events, **kw}


def gt(*sessions, locked=True):
    return {"schema_version": "1", "dataset": {"id": "t", "split": "test", "locked": locked}, "sessions": list(sessions)}


def inc(start, end, event_type="infant_off_surface", created=None, status="SHADOW", cameras=("cam1",),
        iid=None, source="pipeline-x"):
    return {"id": iid or f"INC-{next(_ids)}", "event_type": event_type, "start": iso(start), "end": iso(end),
            "created_at": iso(created if created is not None else end), "cameras": list(cameras),
            "status": status, "room_id": "r1", "room_zone": "z1", "source": source}


def write_incidents(tmp_path, incs, name="incidents.json"):
    p = tmp_path / name
    p.write_text(json.dumps(incs))
    return p
