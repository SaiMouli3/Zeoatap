"""Pre-registered acceptance goals (SAFETY_VALIDATION §5) and the go/no-go
check (§7). Goals are configurable with a JSON file; the defaults below are
the proposed goals from §5 and are engineering goals, not achieved results.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .stats import clopper_pearson, poisson_rate_interval, rule_of_three_n


@dataclass
class Goal:
    code: str
    stage: str
    recall_lower: float | None = None       # goal on the lower confidence bound of recall
    n_min: int = 0                           # pre-registered minimum positives
    fa_per_camera_hour: float | None = None  # goal on false alerts per camera-hour
    latency_p95_s: float | None = None
    latency_budget_s: float = 5.0            # matching window after GT.end
    required_strata: dict[str, list[str]] = field(default_factory=dict)  # e.g. {"lighting": ["day","ir"]}
    report_only: bool = False                # research signal: never passes
    deployable: bool = True                  # False: not a product feature at all


DEFAULT_GOALS: dict[str, Goal] = {
    "camera_offline": Goal("D1", "Pilot alert", 0.99, 100, 0.01, 10.0, 10.0),
    "camera_obstructed": Goal("D2", "Pilot alert", 0.99, 100, 0.01, 10.0, 10.0),
    "adult_in_restricted_zone": Goal("D3", "Pilot alert", 0.90, 60, 0.02, 5.0, 5.0),
    "infant_off_surface": Goal("A1", "Shadow -> alert", 0.90, 60, 0.005, 5.0, 5.0,
                               required_strata={"lighting": ["day", "ir"]}),
    "infant_in_hazard_zone": Goal("D4", "Review", 0.85, 0, 0.05, 30.0, 30.0),
    "no_visible_movement": Goal("C1", "Review only", 0.80, 0, 0.05, None, 60.0),
    "infant_not_visible": Goal("C2", "Review only", 0.80, 0, 0.05, None, 60.0),
    "handling_abrupt": Goal("B1", "Research only", report_only=True, latency_budget_s=5.0),
    "handling_repetitive": Goal("B2", "Research only", report_only=True, latency_budget_s=5.0),
    "respiration_estimate": Goal("E1", "Not deployed", report_only=True, deployable=False),
}

DEFAULT_LATENCY_BUDGET_S = 5.0


def load_goals(path: str | Path | None) -> dict[str, Goal]:
    goals = {k: Goal(**asdict(v)) for k, v in DEFAULT_GOALS.items()}
    if path:
        raw = json.loads(Path(path).read_text())
        for et, g in raw.get("goals", raw).items():
            base = asdict(goals[et]) if et in goals else asdict(Goal(code=et, stage="custom"))
            base.update(g)
            goals[et] = Goal(**base)
    return goals


def min_n_exact(goal: float, confidence: float, sided: str) -> int:
    """Smallest n for which n/n successes gives a Clopper-Pearson lower bound
    >= goal (lower = alpha' ** (1/n))."""
    a = (1 - confidence) / (2 if sided == "two" else 1)
    return math.ceil(math.log(a) / math.log(goal) - 1e-12)


def required_n(g: Goal, confidence: float, sided: str) -> dict:
    if g.recall_lower is None:
        return {"n_required": None}
    r3 = rule_of_three_n(1 - g.recall_lower)
    ex = min_n_exact(g.recall_lower, confidence, sided)
    return {"n_required": max(g.n_min, r3, ex), "n_min_preregistered": g.n_min,
            "n_rule_of_three": r3, "n_exact_zero_miss": ex}


def required_hours(fa_goal: float, confidence: float, sided: str) -> float:
    """Camera-hours needed to bound FA/camera-hour <= goal with 0 observed."""
    iv = poisson_rate_interval(0, 1.0, confidence, "upper" if sided != "two" else "two")
    return iv.upper / fa_goal


def _check(name: str, status: str, detail: str, **kw) -> dict:
    return {"check": name, "status": status, "detail": detail, **kw}


def evaluate_goal(et: str, m: dict, g: Goal | None, confidence: float, sided: str,
                  stratum_ratio: float = 0.8, stratum_min_n: int = 5) -> dict:
    """Return {verdict, checks[]} for one event type. Verdicts: PASS, FAIL,
    INSUFFICIENT_DATA, INVALID_SOURCE, REPORT_ONLY, NOT_DEPLOYABLE, NO_GOAL."""
    if g is None:
        return {"verdict": "NO_GOAL", "checks": [], "goal": None}
    gd = asdict(g)
    if not g.deployable:
        return {"verdict": "NOT_DEPLOYABLE", "goal": gd,
                "checks": [_check("deployable", "FAIL", f"{g.code} is not a product feature")]}
    if g.report_only:
        return {"verdict": "REPORT_ONLY", "goal": gd,
                "checks": [_check("report_only", "INFO", f"{g.code} is research only: metrics are reported, never passed")]}
    checks = []
    side = "lower" if sided == "one" else "two"
    n, tp = m["n_gt"], m["detected"]
    if g.recall_lower is not None:
        req = required_n(g, confidence, sided)
        lo = clopper_pearson(tp, n, confidence, side).lower
        if n < req["n_required"]:
            checks.append(_check("recall", "INSUFFICIENT_DATA",
                                 f"n={n} positives < {req['n_required']} required to claim recall >= {g.recall_lower} "
                                 f"(pre-registered {req['n_min_preregistered']}, rule of three {req['n_rule_of_three']}, "
                                 f"exact zero-miss {req['n_exact_zero_miss']})", lower_bound=lo, **req))
        else:
            checks.append(_check("recall", "PASS" if lo >= g.recall_lower else "FAIL",
                                 f"recall {tp}/{n}, {sided}-sided {confidence:.0%} lower bound {lo:.4f} vs goal {g.recall_lower}",
                                 lower_bound=lo, **req))
        for key, values in g.required_strata.items():
            for v in values:
                s = m["strata"].get(key, {}).get(v, {"n_gt": 0, "detected": 0})
                slo = clopper_pearson(s["detected"], s["n_gt"], confidence, side).lower
                if s["n_gt"] < req["n_required"]:
                    st = "INSUFFICIENT_DATA"
                else:
                    st = "PASS" if slo >= g.recall_lower else "FAIL"
                checks.append(_check(f"stratum {key}={v}", st,
                                     f"{s['detected']}/{s['n_gt']}, lower bound {slo:.4f}; needs n >= {req['n_required']} "
                                     f"and bound >= {g.recall_lower}", lower_bound=slo))
    overall = m["recall"]
    if overall is not None:
        low = []
        for key, vals in m["strata"].items():
            for v, s in vals.items():
                if s["n_gt"] >= stratum_min_n and s["recall"] is not None and s["recall"] < stratum_ratio * overall:
                    low.append(f"{key}={v} ({s['recall']:.3f})")
        checks.append(_check("strata >= 80% of overall recall", "FAIL" if low else "PASS",
                             ("below %.0f%% of overall recall %.3f: %s; needs a documented mitigation"
                              % (stratum_ratio * 100, overall, ", ".join(low))) if low else
                             f"every stratum with n >= {stratum_min_n} is >= {stratum_ratio:.0%} of overall recall {overall:.3f}"))
    if g.fa_per_camera_hour is not None:
        hrs = m["camera_hours_operational"]
        need = required_hours(g.fa_per_camera_hour, confidence, sided)
        k = m["false_alerts"]
        up = poisson_rate_interval(k, hrs, confidence, "upper" if sided == "one" else "two") if hrs > 0 else None
        ub = up.upper if up else math.inf
        if hrs < need:
            checks.append(_check("false alerts / camera-hour", "INSUFFICIENT_DATA",
                                 f"{hrs:.2f} operational camera-hours < {need:.1f} needed to bound the rate below "
                                 f"{g.fa_per_camera_hour} (observed {k})", upper_bound=ub, hours_required=need))
        else:
            checks.append(_check("false alerts / camera-hour", "PASS" if ub <= g.fa_per_camera_hour else "FAIL",
                                 f"{k} false alerts in {hrs:.1f} h, upper bound {ub:.5f} vs goal {g.fa_per_camera_hour}",
                                 upper_bound=ub, hours_required=need))
    if g.latency_p95_s is not None:
        p95 = m["latency"]["p95_s"]
        if p95 is None:
            checks.append(_check("latency p95", "INSUFFICIENT_DATA", "no matched events"))
        else:
            checks.append(_check("latency p95", "PASS" if p95 <= g.latency_p95_s else "FAIL",
                                 f"p95 {p95:.2f} s (n={m['latency']['n']}) vs goal {g.latency_p95_s} s"))
    if m["mock_incidents"]:
        checks.append(_check("real detector", "INVALID_SOURCE",
                             f"{m['mock_incidents']} incidents come from a MOCK detector; MOCK output is never real detection"))
    statuses = {c["status"] for c in checks}
    for v in ("INVALID_SOURCE", "FAIL", "INSUFFICIENT_DATA"):
        if v in statuses:
            return {"verdict": v, "checks": checks, "goal": gd}
    return {"verdict": "PASS" if checks else "NO_GOAL", "checks": checks, "goal": gd}


MANUAL_CHECKS = [
    "Event definition approved by the safety lead + childcare domain expert",
    "Shadow mode completed with required camera-hours",
    "Failure-injection suite passes (TESTING_STRATEGY §7)",
    "Monitoring-unavailable states verified to display correctly",
    "Rollback tested on the target hardware",
    "Privacy review sign-off for any new data flows",
    "Release manifest signed with validated_for_alerting=true referencing this report",
]
