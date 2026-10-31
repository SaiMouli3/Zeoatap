"""Data model and loaders: ground truth, system incidents, monitoring states.

Times are held as float unix seconds internally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import jsonschema

_FRAC = re.compile(r"(\.\d+)")


def parse_time(s: str) -> float:
    """RFC 3339 -> unix seconds. Accepts Z or offsets and up to nanosecond
    fractions (Go emits 9 digits)."""
    if not isinstance(s, str):
        raise ValueError(f"expected an RFC 3339 string, got {s!r}")
    t = s.strip().replace("z", "Z")
    t = t[:-1] + "+00:00" if t.endswith("Z") else t
    m = _FRAC.search(t)
    if m:
        frac = m.group(1)
        t = t.replace(frac, frac[:7].ljust(7, "0"), 1)
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        raise ValueError(f"time without a zone: {s!r}")
    return dt.timestamp()


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- ground truth

STRATA_KEYS = ("lighting", "occlusion", "camera_angle")


@dataclass
class GTEvent:
    id: str
    session_id: str
    event_type: str
    start: float
    end: float
    room_zone: str | None
    visibility: str
    annotator_confidence: str
    annotators: list[str]
    adjudicated: bool
    conditions: dict


@dataclass
class HardNegative:
    id: str
    session_id: str
    label: str
    start: float
    end: float


@dataclass
class Session:
    session_id: str
    facility_id: str
    camera_ids: list[str]
    room_id: str | None
    conditions: dict
    span_start: float
    span_end: float
    events: list[GTEvent] = field(default_factory=list)
    hard_negatives: list[HardNegative] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return max(0.0, self.span_end - self.span_start) / 3600.0


@dataclass
class GroundTruth:
    sessions: list[Session]
    dataset: dict
    warnings: list[str]

    @property
    def events(self) -> list[GTEvent]:
        return [e for s in self.sessions for e in s.events]


def schema() -> dict:
    return json.loads(resources.files("zeoatap_eval").joinpath("ground_truth.schema.json").read_text())


def _span(obj: dict, t0: float | None, what: str) -> tuple[float, float]:
    if "start" in obj:
        a, b = parse_time(obj["start"]), parse_time(obj["end"])
    else:
        if t0 is None:
            raise ValueError(f"{what}: start_s/end_s need the session's t0")
        a, b = t0 + float(obj["start_s"]), t0 + float(obj["end_s"])
    if b < a:
        raise ValueError(f"{what}: end before start")
    return a, b


def load_ground_truth(data: dict | str | Path) -> GroundTruth:
    if not isinstance(data, dict):
        data = json.loads(Path(data).read_text())
    jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker()).validate(data)
    warnings: list[str] = []
    sessions: list[Session] = []
    seen_ev: set[str] = set()
    seen_sess: set[str] = set()
    if data.get("dataset", {}).get("split") in ("test", "ood_test") and not data["dataset"].get("locked"):
        warnings.append("dataset split is a test set but 'locked' is not true (SAFETY_VALIDATION §3.2)")
    for s in data["sessions"]:
        sid = s["session_id"]
        if sid in seen_sess:
            raise ValueError(f"duplicate session_id {sid}")
        seen_sess.add(sid)
        t0 = parse_time(s["t0"]) if "t0" in s else None
        a, b = _span(s["operating_span"], t0, f"session {sid} operating_span")
        sess = Session(sid, s["facility_id"], [s["camera_id"], *s.get("extra_camera_ids", [])],
                       s.get("room_id"), dict(s["conditions"]), a, b)
        for e in s["events"]:
            if e["id"] in seen_ev:
                raise ValueError(f"duplicate event id {e['id']}")
            seen_ev.add(e["id"])
            ea, eb = _span(e, t0, f"event {e['id']}")
            if ea < a or eb > b:
                warnings.append(f"event {e['id']} extends outside session {sid} operating span")
            if len(e["annotators"]) < 2:
                warnings.append(f"event {e['id']}: fewer than 2 independent annotators (SAFETY_VALIDATION §3.1)")
            if not e["adjudicated"]:
                warnings.append(f"event {e['id']}: not adjudicated")
            cond = {**sess.conditions, **e.get("conditions", {})}
            sess.events.append(GTEvent(e["id"], sid, e["event_type"], ea, eb, e.get("room_zone"),
                                       e["visibility"], e["annotator_confidence"], list(e["annotators"]),
                                       bool(e["adjudicated"]), cond))
        for h in s.get("hard_negatives", []):
            ha, hb = _span(h, t0, f"hard negative {h['id']}")
            sess.hard_negatives.append(HardNegative(h["id"], sid, h["label"], ha, hb))
        sessions.append(sess)
    return GroundTruth(sessions, dict(data.get("dataset", {})), warnings)


# --------------------------------------------------------------------------- incidents

@dataclass
class Incident:
    id: str
    event_type: str
    start: float
    end: float
    created_at: float
    cameras: list[str]
    status: str
    room_zone: str | None = None
    room_id: str | None = None
    source: str | None = None


def _incident_from(obj: dict, created_hint: float | None) -> Incident:
    created = obj.get("created_at")
    created_at = parse_time(created) if created else created_hint
    if created_at is None:
        created_at = parse_time(obj["end"])
    return Incident(
        id=obj["id"], event_type=obj["event_type"], start=parse_time(obj["start"]), end=parse_time(obj["end"]),
        created_at=created_at, cameras=list(obj.get("cameras") or []), status=obj.get("status", ""),
        room_zone=obj.get("room_zone"), room_id=obj.get("room_id"), source=obj.get("source"))


def load_incidents(path: str | Path) -> list[Incident]:
    """Load system incidents from either

    * Go replay output (JSON lines; kinds incident_created / incident_merged,
      state_change lines are ignored). The final version of each incident id
      wins; created_at is the incident's `end` on its incident_created line,
      i.e. the moment the rule confirmed it.
    * core's API: a JSON array of Incident (GET /api/v1/incidents), a single
      object with an "incidents" array, or JSON lines of Incident. created_at
      is taken from the API field.
    """
    text = Path(path).read_text()
    stripped = text.lstrip()
    records: list[dict]
    if stripped.startswith("["):
        records = json.loads(stripped)
    elif stripped.startswith("{") and "\n" not in stripped.strip():
        one = json.loads(stripped)
        records = one.get("incidents", [one]) if isinstance(one, dict) else one
    else:
        records = [json.loads(l) for l in text.splitlines() if l.strip()]
    created: dict[str, float] = {}
    final: dict[str, dict] = {}
    order: list[str] = []
    for r in records:
        if "kind" in r:
            if r["kind"] not in ("incident_created", "incident_merged", "incident_updated"):
                continue
            inc = r["incident"]
            if r["kind"] == "incident_created" and inc["id"] not in created:
                created[inc["id"]] = parse_time(inc["end"])
        else:
            inc = r
        if inc["id"] not in final:
            order.append(inc["id"])
        final[inc["id"]] = inc
    return [_incident_from(final[i], created.get(i)) for i in order]


# --------------------------------------------------------------------------- monitoring states

OPERATIONAL = "FULLY_OPERATIONAL"


@dataclass
class StateTimeline:
    """Per camera, a sorted list of (start, end, state, reason)."""
    intervals: dict[str, list[tuple[float, float, str, str]]]
    sources: set[str] = field(default_factory=set)

    def time_in(self, camera: str, a: float, b: float, states: set[str]) -> float:
        tot = 0.0
        for s, e, st, _ in self.intervals.get(camera, []):
            if st in states:
                tot += max(0.0, min(b, e) - max(a, s))
        return tot


def states_from_observations(path: str | Path, stale_after: float = 3.0) -> StateTimeline:
    """Rebuild core's monitoring-state timeline from an observations JSONL,
    using the same rule as eventengine.cameraHealth.stateAt: a camera is
    CAMERA_UNAVAILABLE once its last observation is older than stale_after,
    else AI_UNAVAILABLE if ai_available=false, else VIDEO_DEGRADED if the
    frame was degraded, else FULLY_OPERATIONAL. (The replay's state_change
    lines carry no timestamps, so they cannot be used directly.)"""
    per: dict[str, list[tuple[float, str, str]]] = {}
    sources: set[str] = set()
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            q = o.get("quality") or {}
            sources.add(o.get("source", ""))
            if not o.get("ai_available"):
                st = "AI_UNAVAILABLE"
            elif q.get("degraded"):
                st = "VIDEO_DEGRADED"
            else:
                st = OPERATIONAL
            per.setdefault(o["camera_id"], []).append((parse_time(o["capture_ts"]), st, q.get("reason", "")))
    out: dict[str, list[tuple[float, float, str, str]]] = {}
    for cam, obs in per.items():
        obs.sort()
        iv: list[tuple[float, float, str, str]] = []
        for i, (t, st, reason) in enumerate(obs):
            nxt = obs[i + 1][0] if i + 1 < len(obs) else t + stale_after
            healthy_until = min(nxt, t + stale_after)
            iv.append((t, healthy_until, st, reason))
            if nxt > t + stale_after:
                iv.append((t + stale_after, nxt, "CAMERA_UNAVAILABLE", ""))
        out[cam] = _merge(iv)
    return StateTimeline(out, sources)


def states_from_jsonl(path: str | Path) -> StateTimeline:
    """Timeline from JSON lines {camera_id, state, at|since|ts[, reason]} (e.g.
    core's camera_state SSE events). Each state lasts until the next one for
    the camera; the last one has zero length unless an 'end' line follows."""
    per: dict[str, list[tuple[float, str, str]]] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        ts = r.get("at") or r.get("since") or r.get("ts")
        if ts is None or "state" not in r:
            continue
        per.setdefault(r["camera_id"], []).append((parse_time(ts), r["state"], r.get("reason", "")))
    out = {}
    for cam, pts in per.items():
        pts.sort()
        out[cam] = _merge([(t, pts[i + 1][0] if i + 1 < len(pts) else t, st, rs) for i, (t, st, rs) in enumerate(pts)])
    return StateTimeline(out)


def _merge(iv: list[tuple[float, float, str, str]]) -> list[tuple[float, float, str, str]]:
    out: list[tuple[float, float, str, str]] = []
    for s, e, st, r in iv:
        if e <= s:
            continue
        if out and out[-1][2] == st and out[-1][3] == r and abs(out[-1][1] - s) < 1e-9:
            out[-1] = (out[-1][0], e, st, r)
        else:
            out.append((s, e, st, r))
    return out


# Camera-health faults (D1/D2) are not incidents in core; they are monitoring
# states. To score them against injected faults, each non-operational state
# interval becomes a pseudo-incident whose created_at is the interval start
# (the moment core would show it).
FAULT_TYPES = {
    "CAMERA_UNAVAILABLE": "camera_offline",
    ("VIDEO_DEGRADED", "black"): "camera_offline",
    ("VIDEO_DEGRADED", "frozen"): "camera_offline",
    ("VIDEO_DEGRADED", "obstructed"): "camera_obstructed",
    ("VIDEO_DEGRADED", "view_changed"): "camera_obstructed",
    ("VIDEO_DEGRADED", "blur"): "camera_obstructed",
}


def fault_incidents(tl: StateTimeline) -> list[Incident]:
    out = []
    for cam, iv in tl.intervals.items():
        for s, e, st, reason in iv:
            et = FAULT_TYPES.get(st) or FAULT_TYPES.get((st, reason))
            if et:
                out.append(Incident(f"STATE-{cam}-{len(out)}", et, s, e, s, [cam], "ALERTED", source="state"))
    return out
