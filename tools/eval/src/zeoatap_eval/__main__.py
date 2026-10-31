"""zeoatap-eval: event-level safety evaluation.

  zeoatap-eval evaluate --ground-truth gt.json --incidents replay.jsonl \
      [--observations obs.jsonl | --states states.jsonl] [--goals goals.json] \
      --out-md report.md --out-json report.json
  zeoatap-eval validate gt.json
  zeoatap-eval cvat-import --xml a.xml --annotator A [--xml b.xml --annotator B] --fps 15 \
      --session-id S1 ... --out gt.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .goals import load_goals
from .metrics import EvalConfig, evaluate
from .model import load_ground_truth, load_incidents, states_from_jsonl, states_from_observations
from .report import render_markdown


def _clean(o):
    """JSON-safe copy: infinities become the string "inf", sets become lists."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in (sorted(o) if isinstance(o, set) else o)]
    if isinstance(o, float) and o in (float("inf"), float("-inf")):
        return "inf" if o > 0 else "-inf"
    return o


def cmd_evaluate(a) -> int:
    gt = load_ground_truth(a.ground_truth)
    incidents = []
    for p in a.incidents or []:
        incidents += load_incidents(p)
    states = None
    if a.observations:
        states = states_from_observations(a.observations)
    elif a.states:
        states = states_from_jsonl(a.states)
    cfg = EvalConfig(
        goals=load_goals(a.goals),
        latency_budget_override=a.latency_budget,
        pre_tolerance_s=a.pre_tolerance,
        alert_statuses=tuple(a.alert_statuses.split(",")),
        decision_sided=a.decision_sided,
        count_duplicates_as_false_alerts=a.count_duplicates_as_false_alerts,
        release_types=a.release_types.split(",") if a.release_types else None,
    )
    r = evaluate(gt, incidents, cfg, states)
    md = render_markdown(r, a.title)
    js = json.dumps(_clean(r), indent=2, allow_nan=False)
    if a.out_json:
        Path(a.out_json).write_text(js + "\n")
    if a.out_md:
        Path(a.out_md).write_text(md + "\n")
    if not a.out_md and not a.out_json:
        print(md)
    else:
        print(f"decision: {r['go_no_go']['decision']}", file=sys.stderr)
    return 0


def cmd_validate(a) -> int:
    gt = load_ground_truth(a.ground_truth)
    print(f"ok: {len(gt.sessions)} sessions, {len(gt.events)} events")
    for w in gt.warnings:
        print("warning:", w)
    return 0


def cmd_cvat_import(a) -> int:
    from .cvat import cohens_kappa_bins, match_annotators, parse_cvat_video_xml, to_gt_events

    if len(a.xml) != len(a.annotator) or not 1 <= len(a.xml) <= 2:
        print("give one or two --xml, each with an --annotator", file=sys.stderr)
        return 2
    labels = set(a.labels.split(",")) if a.labels else None
    evs = [parse_cvat_video_xml(x, a.fps, labels) for x in a.xml]
    if len(evs) == 1:
        events = [{"id": f"{a.session_id}-{k + 1:03d}", "event_type": e.label,
                   **({"room_zone": e.attrs["room_zone"]} if e.attrs.get("room_zone") else {}),
                   "start_s": round(e.start_s, 3), "end_s": round(e.end_s, 3),
                   "visibility": e.attrs.get("visibility") or "visible",
                   "annotator_confidence": e.attrs.get("annotator_confidence") or "likely",
                   "annotators": [a.annotator[0]], "adjudicated": False} for k, e in enumerate(evs[0])]
        queue, agreement = [], None
    else:
        pairs, oa, ob = match_annotators(evs[0], evs[1])
        events, queue = to_gt_events(pairs, oa, ob, a.annotator[0], a.annotator[1], a.session_id)
        dur = a.duration_s or max([e.end_s for e in evs[0] + evs[1]] + [1.0])
        ious = [p[2] for p in pairs]
        agreement = {"matched": len(pairs), "only_" + a.annotator[0]: len(oa), "only_" + a.annotator[1]: len(ob),
                     "mean_temporal_iou": sum(ious) / len(ious) if ious else None,
                     "cohens_kappa_1s_bins": cohens_kappa_bins(evs[0], evs[1], dur)}
    span_end = a.duration_s or max([e["end_s"] for e in events] + [0.0])
    session = {
        "session_id": a.session_id, "facility_id": a.facility_id, "camera_id": a.camera_id,
        **({"t0": a.t0} if a.t0 else {}),
        "recording": {"fps": a.fps, **({"source_file": a.source_file} if a.source_file else {})},
        "conditions": {"lighting": a.lighting, "occlusion": a.occlusion, "camera_angle": a.camera_angle},
        "operating_span": {"start_s": 0, "end_s": span_end},
        "events": events,
    }
    doc = {"schema_version": "1", "dataset": {"id": a.dataset_id, "split": a.split, "locked": False},
           "sessions": [session]}
    Path(a.out).write_text(json.dumps(doc, indent=2) + "\n")
    if queue:
        qp = Path(a.out).with_suffix(".adjudication.json")
        qp.write_text(json.dumps({"agreement": agreement, "disagreements": queue}, indent=2) + "\n")
        print(f"{len(queue)} events need adjudication: {qp}", file=sys.stderr)
    if agreement:
        print(json.dumps({"agreement": agreement}, indent=2))
    print(f"wrote {a.out}: {len(events)} events", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zeoatap-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("evaluate", help="score incidents against ground truth")
    e.add_argument("--ground-truth", required=True)
    e.add_argument("--incidents", action="append", help="replay JSONL or API JSON; repeatable")
    src = e.add_mutually_exclusive_group()
    src.add_argument("--observations", help="observations JSONL: rebuild FULLY_OPERATIONAL time like core does")
    src.add_argument("--states", help="state timeline JSONL {camera_id, state, at|since}")
    e.add_argument("--goals", help="JSON overriding the default §5 goals per event type")
    e.add_argument("--latency-budget", type=float, help="override every event type's matching budget (s)")
    e.add_argument("--pre-tolerance", type=float, default=1.0, help="seconds before GT.start (default 1)")
    e.add_argument("--alert-statuses", default="ALERTED,SHADOW",
                   help="incident statuses that count as alert severity (default ALERTED,SHADOW)")
    e.add_argument("--decision-sided", choices=["two", "one"], default="two",
                   help="bounds used for go/no-go: two-sided 95%% (default, conservative) or one-sided 95%%")
    e.add_argument("--count-duplicates-as-false-alerts", action="store_true")
    e.add_argument("--release-types", help="comma list of event types that must PASS for GO")
    e.add_argument("--title", default="Safety evaluation report")
    e.add_argument("--out-md")
    e.add_argument("--out-json")
    e.set_defaults(fn=cmd_evaluate)

    v = sub.add_parser("validate", help="validate a ground-truth file")
    v.add_argument("ground_truth")
    v.set_defaults(fn=cmd_validate)

    c = sub.add_parser("cvat-import", help="CVAT for video 1.1 XML -> ground-truth session")
    c.add_argument("--xml", action="append", required=True)
    c.add_argument("--annotator", action="append", required=True)
    c.add_argument("--fps", type=float, required=True, help="video fps used in CVAT (frame -> seconds)")
    c.add_argument("--labels", help="comma list of labels that are safety events (default all tracks)")
    c.add_argument("--session-id", required=True)
    c.add_argument("--facility-id", required=True)
    c.add_argument("--camera-id", required=True)
    c.add_argument("--t0", help="RFC 3339 wall time of frame 0 (else fill it in later)")
    c.add_argument("--duration-s", type=float, help="annotated span (default: last event end)")
    c.add_argument("--source-file")
    c.add_argument("--lighting", default="day")
    c.add_argument("--occlusion", default="none")
    c.add_argument("--camera-angle", default="high_oblique")
    c.add_argument("--dataset-id", default="unnamed")
    c.add_argument("--split", default="validation")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_cvat_import)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
