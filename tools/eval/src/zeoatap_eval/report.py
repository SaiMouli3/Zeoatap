"""Markdown rendering of an evaluation result."""

from __future__ import annotations


def _f(v, nd=3, pct=False):
    if v is None:
        return "–"
    if isinstance(v, float) and v == float("inf"):
        return "∞"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _ci(ci, nd=3):
    return "–" if not ci else f"[{_f(ci['lower'], nd)}, {_f(ci['upper'], nd)}]"


def render_markdown(r: dict, title: str = "Safety evaluation report") -> str:
    s, cfg, gng = r["summary"], r["config"], r["go_no_go"]
    out = [f"# {title}", ""]
    out.append(f"**Decision: {gng['decision']}**" + (" (MOCK detector input: not a real evaluation)"
                                                     if s["mock_incidents"] else ""))
    out.append("")
    out.append("All metrics are event-level (SAFETY_VALIDATION §4). Intervals are exact 95% "
               "Clopper–Pearson (proportions) or exact Poisson (rates). Goals are the pre-registered "
               "goals in use; meeting them on this data is not a claim about real-world performance "
               "unless the data is the locked test set.")
    out.append("")
    out += ["## Inputs", "",
            "| | |", "|---|---|",
            f"| Sessions | {s['sessions']} |",
            f"| Ground-truth events | {s['gt_events']} |",
            f"| System incidents (total / INCONCLUSIVE / out of scope) | {s['incidents_total']} / "
            f"{s['incidents_inconclusive']} / {s['incidents_out_of_scope']} |",
            f"| Camera-hours scheduled | {_f(s['camera_hours_scheduled'], 3)} |",
            f"| Camera-hours FULLY_OPERATIONAL | {_f(s['camera_hours_operational'], 3)}"
            + ("" if s["state_data"] else " (no state data: all scheduled time assumed operational)") + " |",
            f"| Camera-hours not operational (reported separately) | {_f(s['camera_hours_non_operational'], 3)} |",
            f"| MOCK incidents | {s['mock_incidents']} |",
            f"| Matching | same type; overlap, or incident start in [GT.start − {cfg['pre_tolerance_s']} s, GT.end + budget]; "
            f"one-to-one, Hungarian on temporal IoU |",
            f"| Latency budgets (s) | {', '.join(f'{k}={v:g}' for k, v in cfg['latency_budget_s'].items())} |",
            f"| Alert-severity statuses (for false alerts) | {', '.join(cfg['alert_statuses'])} |",
            f"| Decision bounds | {cfg['decision_sided']}-sided {cfg['confidence']:.0%} |", ""]

    out += ["## Per event type", "",
            "| Event type | GT n | Detected | Inconcl. | Missed | Recall [95% CI] | Missed rate [95% CI] | "
            "Precision [95% CI] | Dups | FP | FA/cam-h [95% CI] | Latency median / p95 (s) | Dedup acc. |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for et, m in r["per_event_type"].items():
        miss = _f(m["missed_event_rate"]) + " " + _ci(m["missed_event_rate_ci95"])
        if m["missed_event_rate_rule_of_three_upper"] is not None:
            miss += f" (rule of three ≤ {_f(m['missed_event_rate_rule_of_three_upper'])})"
        out.append(
            f"| {et} | {m['n_gt']} | {m['detected']} | {m['inconclusive']} | {m['missed']} | "
            f"{_f(m['recall'])} {_ci(m['recall_ci95'])} | {miss} | "
            f"{_f(m['precision'])} {_ci(m['precision_ci95'])} | {m['duplicates']} | {m['false_positives']} | "
            f"{_f(m['false_alerts_per_camera_hour'], 4)} {_ci(m['false_alerts_per_camera_hour_ci95'], 4)} | "
            f"{_f(m['latency']['median_s'], 2)} / {_f(m['latency']['p95_s'], 2)} | {_f(m['dedup_accuracy'])} |")
    out += ["", "Detected = matched by a conclusive incident. Inconclusive = no conclusive incident, but an "
            "INCONCLUSIVE incident covered the event (counted separately, not as detection). Duplicates are "
            "extra incidents on an already-matched event: they lower precision but are not false alerts"
            + (" (here they ARE counted as false alerts)" if cfg["count_duplicates_as_false_alerts"] else "")
            + ". FA/cam-h counts unmatched alert-severity incidents per FULLY_OPERATIONAL camera-hour.", ""]

    out += ["## Confusion (GT type → outcome)", "", "| GT type | Outcomes |", "|---|---|"]
    for et, m in r["per_event_type"].items():
        if m["confusion"]:
            out.append(f"| {et} | " + ", ".join(f"{k}: {v}" for k, v in sorted(m["confusion"].items())) + " |")
    out.append("")

    out += ["## Strata", ""]
    for et, m in r["per_event_type"].items():
        if not m["n_gt"]:
            continue
        out += [f"### {et}", "", "| Stratum | GT n | Detected | Inconcl. | Recall [95% CI] | Latency median (s) | FA / cam-h |",
                "|---|---|---|---|---|---|---|"]
        for key, vals in m["strata"].items():
            for v, st in vals.items():
                out.append(f"| {key}={v} | {st['n_gt']} | {st['detected']} | {st['inconclusive']} | "
                           f"{_f(st['recall'])} {_ci(st['recall_ci95'])} | {_f(st['latency_median_s'], 2)} | "
                           f"{_f(st['false_alerts_per_camera_hour'], 4)} |")
        out.append("")

    out += ["## Go / no-go (SAFETY_VALIDATION §7)", "", f"**{gng['decision']}** for release types: "
            + (", ".join(gng["release_types"]) or "(none)"), ""]
    if gng["blockers"]:
        out += ["Blockers:", ""] + [f"- {b}" for b in gng["blockers"]] + [""]
    for et, v in gng["per_event_type"].items():
        g = v.get("goal")
        out.append(f"### {et}: {v['verdict']}" + (f" ({g['code']}, {g['stage']})" if g else ""))
        out.append("")
        if v["checks"]:
            out += ["| Check | Status | Detail |", "|---|---|---|"]
            out += [f"| {c['check']} | {c['status']} | {c['detail']} |" for c in v["checks"]]
            out.append("")
    out += ["Not verified by this tool (manual checklist items):", ""]
    out += [f"- [ ] {c}" for c in gng["manual_checks_not_verified_by_tool"]] + [""]

    if r["missed"]:
        out += ["## Missed / inconclusive events", "", "| GT id | Type | Start | Outcome |", "|---|---|---|---|"]
        out += [f"| {x['gt_id']} | {x['event_type']} | {x['start']} | {x['outcome']} |" for x in r["missed"]] + [""]
    if r["false_positives"]:
        out += ["## False positives", "", "| Incident | Type | Status | Start | On hard negative |", "|---|---|---|---|---|"]
        out += [f"| {x['incident_id']} | {x['event_type']} | {x['status']} | {x['start']} | {x['hard_negative'] or ''} |"
                for x in r["false_positives"]] + [""]
    if r["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    return "\n".join(out)
