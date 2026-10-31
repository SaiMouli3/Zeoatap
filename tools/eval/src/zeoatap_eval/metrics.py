"""Event-level metrics (SAFETY_VALIDATION §4) and the evaluation driver."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .goals import DEFAULT_LATENCY_BUDGET_S, Goal, MANUAL_CHECKS, evaluate_goal
from .matching import assign, eligible, in_scope, match_all
from .model import (OPERATIONAL, STRATA_KEYS, GroundTruth, GTEvent, Incident, StateTimeline,
                    fault_incidents, fmt_time)
from .stats import clopper_pearson, percentile, poisson_rate_interval, rule_of_three

INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class EvalConfig:
    goals: dict[str, Goal] = field(default_factory=dict)
    latency_budget_override: float | None = None
    pre_tolerance_s: float = 1.0
    alert_statuses: tuple[str, ...] = ("ALERTED", "SHADOW")
    confidence: float = 0.95
    decision_sided: str = "two"  # "two": two-sided CP/Poisson bounds; "one": one-sided
    count_duplicates_as_false_alerts: bool = False
    release_types: list[str] | None = None  # event types that must PASS for GO; default all with goals

    def budget_for(self, event_type: str) -> float:
        if self.latency_budget_override is not None:
            return self.latency_budget_override
        g = self.goals.get(event_type)
        return g.latency_budget_s if g else DEFAULT_LATENCY_BUDGET_S


def _prop(k: int, n: int, conf: float) -> dict:
    iv = clopper_pearson(k, n, conf) if n else None
    return {"k": k, "n": n, "value": (k / n) if n else None,
            "ci95": iv.as_dict() if iv else None}


def _hours(gt: GroundTruth, states: StateTimeline | None) -> dict[str, dict[str, float]]:
    """Per session: scheduled, operational and degraded camera-hours."""
    out = {}
    for s in gt.sessions:
        sched = (s.span_end - s.span_start) * len(s.camera_ids) / 3600
        if states is None:
            op, deg = sched, 0.0
        else:
            op = sum(states.time_in(c, s.span_start, s.span_end, {OPERATIONAL}) for c in s.camera_ids) / 3600
            deg = sched - op
        out[s.session_id] = {"scheduled": sched, "operational": op, "non_operational": deg}
    return out


def evaluate(gt: GroundTruth, incidents: list[Incident], cfg: EvalConfig,
             states: StateTimeline | None = None) -> dict:
    gt_types = sorted({e.event_type for e in gt.events})
    incidents = list(incidents)
    if states is not None:
        # Camera-health faults are monitoring states, scored only when GT has them.
        incidents += [i for i in fault_incidents(states) if i.event_type in gt_types]
    conclusive = [i for i in incidents if i.status != INCONCLUSIVE]
    inconclusive = [i for i in incidents if i.status == INCONCLUSIVE]

    res, out_of_scope = match_all(gt.sessions, conclusive, cfg.budget_for, cfg.pre_tolerance_s)
    session_of = {s.session_id: s for s in gt.sessions}
    sess_of_inc: dict[str, str] = {}
    for s in gt.sessions:
        for i in incidents:
            if i.id not in sess_of_inc and in_scope(s, i):
                sess_of_inc[i.id] = s.session_id

    # GT events missed by conclusive incidents but covered by an INCONCLUSIVE one.
    gt_inconclusive: dict[str, Incident] = {}
    inc_on_detected, inc_no_gt = [], []
    detected_ids = {g.id for g, _, _ in res.pairs}
    for s in gt.sessions:
        miss = [g for g in res.unmatched_gt if g.session_id == s.session_id]
        cand = [i for i in inconclusive if sess_of_inc.get(i.id) == s.session_id]
        r = assign(miss, cand, cfg.budget_for, cfg.pre_tolerance_s)
        for g, i, _ in r.pairs:
            gt_inconclusive[g.id] = i
        for i in r.false_positives + [d for d, _ in r.duplicates]:
            if any(eligible(g, i, cfg.budget_for(g.event_type), cfg.pre_tolerance_s)
                   for g in s.events if g.id in detected_ids or g.id in gt_inconclusive):
                inc_on_detected.append(i)
            else:
                inc_no_gt.append(i)

    # Wrong-type confusions and hard negatives.
    confusion: dict[str, Counter] = defaultdict(Counter)
    fp_on_hard_negative: dict[str, str] = {}
    for g, _, _ in res.pairs:
        confusion[g.event_type][g.event_type] += 1
    for g in res.unmatched_gt:
        if g.id in gt_inconclusive:
            confusion[g.event_type]["INCONCLUSIVE"] += 1
            continue
        other = [i for i in res.false_positives if sess_of_inc.get(i.id) == g.session_id
                 and i.start <= g.end + cfg.budget_for(g.event_type) and i.end >= g.start - cfg.pre_tolerance_s]
        if other:
            # Columns sum to n: a wrong-type miss is counted under the type the system reported.
            t = min({i.event_type for i in other})
            confusion[g.event_type][f"wrong_type:{t}"] += 1
        else:
            confusion[g.event_type]["missed"] += 1
    for i in res.false_positives:
        s = session_of.get(sess_of_inc.get(i.id, ""))
        for h in (s.hard_negatives if s else []):
            if i.start <= h.end and i.end >= h.start:
                fp_on_hard_negative[i.id] = h.label
                break

    hours = _hours(gt, states)
    total_sched = sum(h["scheduled"] for h in hours.values())
    total_op = sum(h["operational"] for h in hours.values())

    dups_by_gt: Counter = Counter(g.id for _, g in res.duplicates)
    types = sorted(set(gt_types) | {i.event_type for i in conclusive if i.id in sess_of_inc})
    per_type = {}
    for et in types:
        per_type[et] = _type_metrics(et, gt, res, gt_inconclusive, dups_by_gt, hours, cfg,
                                     conclusive, sess_of_inc, fp_on_hard_negative, inc_on_detected, inc_no_gt,
                                     dict(confusion.get(et, {})))

    verdicts = {}
    for et, m in per_type.items():
        verdicts[et] = evaluate_goal(et, m, cfg.goals.get(et), cfg.confidence, cfg.decision_sided)
    release = cfg.release_types or [et for et in gt_types if cfg.goals.get(et) and
                                    not cfg.goals[et].report_only and cfg.goals[et].deployable]
    blockers = []
    if not gt.dataset.get("locked"):
        blockers.append("ground-truth set is not locked (dataset.locked != true)")
    unadj = [e.id for e in gt.events if not e.adjudicated or len(e.annotators) < 2]
    if unadj:
        blockers.append(f"{len(unadj)} GT events lack 2 annotators or adjudication")
    if any(i.source == "MOCK" for i in incidents) or (states is not None and "MOCK" in states.sources):
        blockers.append("input contains MOCK detector output; MOCK is never real detection")
    for et in release:
        v = verdicts.get(et, {"verdict": "NO_DATA"})["verdict"]
        if v != "PASS":
            blockers.append(f"{et}: {v}")
    decision = "GO" if release and not blockers else "NO-GO"

    return {
        "summary": {
            "sessions": len(gt.sessions),
            "gt_events": len(gt.events),
            "incidents_total": len(incidents),
            "incidents_inconclusive": len(inconclusive),
            "incidents_out_of_scope": len(out_of_scope),
            "camera_hours_scheduled": total_sched,
            "camera_hours_operational": total_op,
            "camera_hours_non_operational": total_sched - total_op,
            "state_data": states is not None,
            "mock_incidents": sum(1 for i in incidents if i.source == "MOCK"),
        },
        "config": {
            "pre_tolerance_s": cfg.pre_tolerance_s,
            "latency_budget_s": {et: cfg.budget_for(et) for et in types},
            "alert_statuses": list(cfg.alert_statuses),
            "confidence": cfg.confidence,
            "decision_sided": cfg.decision_sided,
            "count_duplicates_as_false_alerts": cfg.count_duplicates_as_false_alerts,
        },
        "per_event_type": per_type,
        "go_no_go": {"decision": decision, "release_types": release, "blockers": blockers,
                     "per_event_type": verdicts, "manual_checks_not_verified_by_tool": MANUAL_CHECKS},
        "matches": [{"gt_id": g.id, "incident_id": i.id, "event_type": g.event_type, "iou": round(iou, 4),
                     "latency_s": round(i.created_at - g.start, 3)} for g, i, iou in res.pairs],
        "duplicates": [{"incident_id": i.id, "gt_id": g.id, "event_type": g.event_type} for i, g in res.duplicates],
        "missed": [{"gt_id": g.id, "event_type": g.event_type, "start": fmt_time(g.start),
                    "outcome": "inconclusive" if g.id in gt_inconclusive else "missed"} for g in res.unmatched_gt],
        "false_positives": [{"incident_id": i.id, "event_type": i.event_type, "status": i.status,
                             "start": fmt_time(i.start), "hard_negative": fp_on_hard_negative.get(i.id)}
                            for i in res.false_positives],
        "out_of_scope": [i.id for i in out_of_scope],
        "warnings": gt.warnings,
    }


def _type_metrics(et, gt, res, gt_inconclusive, dups_by_gt, hours, cfg, conclusive, sess_of_inc,
                  fp_on_hn, inc_on_detected, inc_no_gt, confusion) -> dict:
    conf = cfg.confidence
    gts = [e for e in gt.events if e.event_type == et]
    pairs = [(g, i, iou) for g, i, iou in res.pairs if g.event_type == et]
    det = {g.id for g, _, _ in pairs}
    n = len(gts)
    tp = len(pairs)
    n_inc = sum(1 for g in gts if g.id in gt_inconclusive)
    missed = n - tp - n_inc
    incs = [i for i in conclusive if i.event_type == et and i.id in sess_of_inc]
    dups = [(i, g) for i, g in res.duplicates if g.event_type == et]
    fps = [i for i in res.false_positives if i.event_type == et]
    is_alert = lambda i: i.status in cfg.alert_statuses  # noqa: E731
    fa = [i for i in fps if is_alert(i)]
    dup_alerts = [i for i, _ in dups if is_alert(i)]
    fa_count = len(fa) + (len(dup_alerts) if cfg.count_duplicates_as_false_alerts else 0)
    op = sum(h["operational"] for h in hours.values())
    lat = [i.created_at - g.start for g, i, _ in pairs]
    misses_k = n - tp
    miss_iv = clopper_pearson(misses_k, n, conf) if n else None
    exactly_one = sum(1 for g in gts if g.id in det and dups_by_gt[g.id] == 0)

    strata: dict[str, dict] = {}
    for key in STRATA_KEYS:
        groups: dict[str, list[GTEvent]] = defaultdict(list)
        for g in gts:
            groups[str(g.conditions.get(key, "unknown"))].append(g)
        strata[key] = {}
        sess_vals = defaultdict(list)
        for s in gt.sessions:
            sess_vals[str(s.conditions.get(key, "unknown"))].append(s.session_id)
        for v in sorted(set(groups) | set(sess_vals)):
            gs = groups.get(v, [])
            k = sum(1 for g in gs if g.id in det)
            ids = {g.id for g in gs}
            sl = [i.created_at - g.start for g, i, _ in pairs if g.id in ids]
            sids = set(sess_vals.get(v, []))
            sfa = sum(1 for i in fa if sess_of_inc.get(i.id) in sids)
            sop = sum(hours[s]["operational"] for s in sids)
            iv = clopper_pearson(k, len(gs), conf) if gs else None
            strata[key][v] = {
                "n_gt": len(gs), "detected": k,
                "inconclusive": sum(1 for g in gs if g.id in gt_inconclusive),
                "recall": k / len(gs) if gs else None, "recall_ci95": iv.as_dict() if iv else None,
                "latency_median_s": statistics.median(sl) if sl else None,
                "false_alerts": sfa, "camera_hours_operational": sop,
                "false_alerts_per_camera_hour": sfa / sop if sop > 0 else None,
            }

    fa_iv = poisson_rate_interval(fa_count, op, conf)
    fa_up1 = poisson_rate_interval(fa_count, op, conf, "upper")
    return {
        "n_gt": n,
        "detected": tp,
        "inconclusive": n_inc,
        "missed": missed,
        "recall": tp / n if n else None,
        "recall_ci95": clopper_pearson(tp, n, conf).as_dict() if n else None,
        "recall_lower_one_sided95": clopper_pearson(tp, n, conf, "lower").lower if n else None,
        "recall_including_inconclusive": (tp + n_inc) / n if n else None,
        "missed_event_rate": misses_k / n if n else None,
        "missed_event_rate_ci95": miss_iv.as_dict() if miss_iv else None,
        "missed_event_rate_rule_of_three_upper": rule_of_three(n) if n and misses_k == 0 else None,
        "incidents": len(incs),
        "true_positive_incidents": tp,
        "duplicates": len(dups),
        "false_positives": len(fps),
        "false_positives_on_hard_negatives": sum(1 for i in fps if i.id in fp_on_hn),
        "precision": tp / len(incs) if incs else None,
        "precision_ci95": clopper_pearson(tp, len(incs), conf).as_dict() if incs else None,
        "false_alerts": fa_count,
        "duplicate_alerts": len(dup_alerts),
        "camera_hours_operational": op,
        "false_alerts_per_camera_hour": fa_count / op if op > 0 else None,
        "false_alerts_per_camera_hour_ci95": fa_iv.as_dict() if fa_iv else None,
        "false_alerts_per_camera_hour_upper_one_sided95": fa_up1.upper if fa_up1 else None,
        "latency": {
            "n": len(lat),
            "median_s": statistics.median(lat) if lat else None,
            "p95_s": percentile(lat, 95),
            "max_s": max(lat) if lat else None,
        },
        "dedup_accuracy": exactly_one / n if n else None,
        "dedup_accuracy_among_detected": exactly_one / tp if tp else None,
        "inconclusive_incidents_on_detected_events": sum(1 for i in inc_on_detected if i.event_type == et),
        "inconclusive_incidents_without_gt": sum(1 for i in inc_no_gt if i.event_type == et),
        "confusion": confusion,
        "strata": strata,
        "mock_incidents": sum(1 for i in incs if i.source == "MOCK"),
    }
