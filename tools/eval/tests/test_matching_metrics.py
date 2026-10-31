import json

import pytest

from helpers import T0, ev, gt, inc, iso, session, write_incidents
from zeoatap_eval.goals import Goal, load_goals
from zeoatap_eval.metrics import EvalConfig, evaluate
from zeoatap_eval.model import load_ground_truth, load_incidents, states_from_observations


def run(tmp_path, gtdoc, incs, states=None, **cfg):
    g = load_ground_truth(gtdoc)
    i = load_incidents(write_incidents(tmp_path, incs))
    c = EvalConfig(goals=cfg.pop("goals", load_goals(None)), **cfg)
    return evaluate(g, i, c, states)


def m(r, et="infant_off_surface"):
    return r["per_event_type"][et]


# --------------------------------------------------------------------------- frame vs event level

def test_one_long_event_detected_once_is_one_true_positive(tmp_path):
    # 10 s event at 15 fps = 150 frames; one incident covering it is ONE detection, not 150.
    r = run(tmp_path, gt(session([ev(100, 110)])), [inc(100.2, 110, created=100.7)])
    x = m(r)
    assert (x["n_gt"], x["detected"], x["true_positive_incidents"], x["duplicates"], x["false_positives"]) == (1, 1, 1, 0, 0)
    assert x["recall"] == 1.0 and x["precision"] == 1.0 and x["dedup_accuracy"] == 1.0


def test_three_incidents_on_one_event_are_one_tp_and_two_duplicates(tmp_path):
    incs = [inc(100.2, 103, iid="I1"), inc(104, 106, iid="I2"), inc(107, 110, iid="I3")]
    r = run(tmp_path, gt(session([ev(100, 110, eid="G1")])), incs)
    x = m(r)
    assert x["detected"] == 1 and x["duplicates"] == 2 and x["false_positives"] == 0
    assert x["recall"] == 1.0
    assert x["precision"] == pytest.approx(1 / 3)  # duplicates are in the denominator, not the numerator
    assert x["dedup_accuracy"] == 0.0               # the event did not produce exactly one incident
    assert x["false_alerts"] == 0                   # duplicates are not false alerts by default
    assert x["duplicate_alerts"] == 2
    # Hungarian on IoU picks the longest-overlap incident as the TP.
    assert r["matches"][0]["incident_id"] == "I3"
    assert {d["gt_id"] for d in r["duplicates"]} == {"G1"}


def test_duplicates_can_be_counted_as_false_alerts(tmp_path):
    incs = [inc(100.2, 103), inc(104, 106)]
    r = run(tmp_path, gt(session([ev(100, 110)])), incs, count_duplicates_as_false_alerts=True)
    assert m(r)["false_alerts"] == 1


def test_merged_incident_is_not_a_duplicate(tmp_path):
    # Replay emits incident_created then incident_merged for the same id: one incident.
    p = tmp_path / "replay.jsonl"
    base = {"id": "INC-1", "event_type": "infant_off_surface", "start": iso(100.0), "cameras": ["cam1"],
            "status": "SHADOW", "room_zone": "z", "source": "x"}
    p.write_text("\n".join(json.dumps(l) for l in [
        {"kind": "state_change", "camera_id": "cam1", "state": "FULLY_OPERATIONAL"},
        {"kind": "incident_created", "camera_id": "cam1", "incident": {**base, "end": iso(100.5)}},
        {"kind": "incident_merged", "camera_id": "cam2", "incident": {**base, "end": iso(104.0), "cameras": ["cam1", "cam2"]}},
    ]) + "\n")
    incs = load_incidents(p)
    assert len(incs) == 1
    assert incs[0].created_at == pytest.approx(load_ground_truth(gt(session([]))).sessions[0].span_start + 100.5)
    assert incs[0].end - incs[0].start == pytest.approx(4.0)
    r = evaluate(load_ground_truth(gt(session([ev(99.8, 101)]))), incs, EvalConfig(goals=load_goals(None)))
    assert m(r)["detected"] == 1 and m(r)["duplicates"] == 0
    assert m(r)["latency"]["median_s"] == pytest.approx(0.7)


# --------------------------------------------------------------------------- type, window, latency

def test_wrong_type_is_a_miss_and_a_false_positive(tmp_path):
    r = run(tmp_path, gt(session([ev(100, 102)])), [inc(100, 102, event_type="adult_in_restricted_zone")])
    a1, d3 = m(r), m(r, "adult_in_restricted_zone")
    assert a1["detected"] == 0 and a1["missed"] == 1 and a1["recall"] == 0.0
    assert a1["confusion"] == {"wrong_type:adult_in_restricted_zone": 1}
    assert d3["false_positives"] == 1 and d3["n_gt"] == 0


def test_matching_window(tmp_path):
    g = gt(session([ev(100, 102, eid="G1")]))
    # Starts 0.9 s before GT.start without overlapping: inside the 1 s pre-tolerance.
    assert m(run(tmp_path, g, [inc(99.1, 99.5)]))["detected"] == 1
    # Starts 1.5 s before and ends before GT.start: no match.
    assert m(run(tmp_path, g, [inc(98.5, 99.5)]))["detected"] == 0
    # Starts after GT.end but within latency_budget (A1 default 5 s): match.
    assert m(run(tmp_path, g, [inc(106.5, 107)]))["detected"] == 1
    # Starts after GT.end + budget: no match, and it is a false alert.
    r = run(tmp_path, g, [inc(107.5, 108)])
    assert m(r)["detected"] == 0 and m(r)["false_alerts"] == 1
    # Budget override.
    assert m(run(tmp_path, g, [inc(107.5, 108)], latency_budget_override=6.0))["detected"] == 1


def test_detection_latency_uses_created_at_minus_gt_start(tmp_path):
    gts = [ev(100 * k, 100 * k + 2) for k in range(1, 21)]
    incs = [inc(100 * k + 0.1, 100 * k + 2, created=100 * k + k * 0.25) for k in range(1, 21)]  # 0.25 .. 5.0 s
    x = m(run(tmp_path, gt(session(gts)), incs))
    assert x["latency"]["n"] == 20
    assert x["latency"]["median_s"] == pytest.approx(2.625)
    assert x["latency"]["p95_s"] == pytest.approx(4.7625)
    assert x["latency"]["max_s"] == pytest.approx(5.0)


def test_hungarian_maximises_matched_events_before_iou(tmp_path):
    # I1 overlaps both events; I2 only G1. Pure-IoU assignment (G1-I1, IoU 0.51) beats
    # G1-I2 + G2-I1 (0.15 + 0.10) and would leave G2 unmatched. We match both.
    gts = [ev(100, 102, eid="G1"), ev(103, 105, eid="G2")]
    incs = [inc(100.2, 103.5, iid="I1"), inc(101.5, 101.8, iid="I2")]
    r = run(tmp_path, gt(session(gts)), incs)
    assert m(r)["detected"] == 2
    assert {(x["gt_id"], x["incident_id"]) for x in r["matches"]} == {("G1", "I2"), ("G2", "I1")}


def test_incidents_from_other_cameras_or_outside_the_span_are_out_of_scope(tmp_path):
    g = gt(session([ev(100, 102)], span=(0, 600)))
    r = run(tmp_path, g, [inc(100, 102, cameras=("camX",)), inc(700, 701)])
    assert m(r)["detected"] == 0 and r["summary"]["incidents_out_of_scope"] == 2
    assert m(r)["false_positives"] == 0


def test_sessions_are_matched_separately(tmp_path):
    g = gt(session([ev(100, 102, eid="A")], sid="S1", camera="cam1"),
           session([ev(100, 102, eid="B")], sid="S2", camera="cam2"))
    r = run(tmp_path, g, [inc(100, 102, cameras=("cam2",), iid="I")])
    assert [(x["gt_id"], x["incident_id"]) for x in r["matches"]] == [("B", "I")]


# --------------------------------------------------------------------------- inconclusive, FA, strata

def test_inconclusive_is_counted_separately(tmp_path):
    gts = [ev(100, 102, eid="G1"), ev(200, 202, eid="G2"), ev(300, 302, eid="G3")]
    incs = [inc(100, 102), inc(200, 202, status="INCONCLUSIVE"), inc(500, 501, status="INCONCLUSIVE"),
            inc(300, 301, status="INCONCLUSIVE"), inc(300.2, 302)]
    x = m(run(tmp_path, gt(session(gts)), incs))
    assert (x["detected"], x["inconclusive"], x["missed"]) == (2, 1, 0)
    assert x["recall"] == pytest.approx(2 / 3)
    assert x["recall_including_inconclusive"] == 1.0
    assert x["inconclusive_incidents_without_gt"] == 1
    assert x["inconclusive_incidents_on_detected_events"] == 1
    assert x["false_positives"] == 0 and x["incidents"] == 2  # INCONCLUSIVE never scored as FP or TP
    assert x["confusion"] == {"infant_off_surface": 2, "INCONCLUSIVE": 1}


def test_false_alerts_only_count_alert_severity(tmp_path):
    incs = [inc(1000, 1001, status="SHADOW"), inc(1500, 1501, status="ALERTED"),
            inc(2000, 2001, status="REVIEW_QUEUED")]
    x = m(run(tmp_path, gt(session([], span=(0, 7200))), incs))
    assert x["false_positives"] == 3 and x["false_alerts"] == 2
    assert x["camera_hours_operational"] == pytest.approx(2.0)
    assert x["false_alerts_per_camera_hour"] == pytest.approx(1.0)


def test_false_alerts_on_hard_negatives_are_labelled(tmp_path):
    s = session([], hard_negatives=[{"id": "H1", "label": "adult_lifts_infant", "start_s": 50, "end_s": 55}])
    r = run(tmp_path, gt(s), [inc(52, 53)])
    assert m(r)["false_positives_on_hard_negatives"] == 1
    assert r["false_positives"][0]["hard_negative"] == "adult_lifts_infant"


def _obs_file(tmp_path, cam, times, ai=True, degraded_at=()):
    p = tmp_path / f"obs-{cam}.jsonl"
    with open(p, "w") as f:
        for t in times:
            f.write(json.dumps({"camera_id": cam, "capture_ts": iso(t), "source": "x", "ai_available": ai,
                                "quality": {"degraded": t in degraded_at, "reason": "black" if t in degraded_at else ""},
                                "tracks": []}) + "\n")
    return p


def test_only_fully_operational_hours_count(tmp_path):
    # Observations at 5 fps for 0..1800 s, then a 600 s gap (camera down), then 2400..3600 s.
    times = [k / 5 for k in range(0, 1800 * 5)] + [2400 + k / 5 for k in range(0, 1200 * 5)]
    st = states_from_observations(_obs_file(tmp_path, "cam1", times))
    kinds = [iv[2] for iv in st.intervals["cam1"]]
    assert kinds == ["FULLY_OPERATIONAL", "CAMERA_UNAVAILABLE", "FULLY_OPERATIONAL"]
    t0 = T0.timestamp()
    down = st.intervals["cam1"][1]
    assert down[0] - t0 == pytest.approx(1799.8 + 3.0)
    assert down[1] - t0 == pytest.approx(2400.0)  # core marks it unavailable 3 s after the last observation
    r = run(tmp_path, gt(session([], span=(0, 3600))), [inc(1000, 1001)], states=st)
    x = m(r)
    # Healthy until 3 s after the last frame (1802.8 s), then again 2400..3600 s (span end).
    op_h = (1802.8 + 1200) / 3600
    assert r["summary"]["camera_hours_operational"] == pytest.approx(op_h, abs=1e-6)
    assert r["summary"]["camera_hours_non_operational"] == pytest.approx(1 - op_h, abs=1e-6)
    assert x["false_alerts_per_camera_hour"] == pytest.approx(1 / op_h, rel=1e-6)


def test_ai_unavailable_hours_do_not_count(tmp_path):
    st = states_from_observations(_obs_file(tmp_path, "cam1", [k / 5 for k in range(0, 3600 * 5)], ai=False))
    r = run(tmp_path, gt(session([], span=(0, 3600))), [], states=st)
    assert r["summary"]["camera_hours_operational"] == 0.0
    assert r["per_event_type"] == {}


def test_camera_faults_from_states_are_scored_as_d1(tmp_path):
    times = [k / 5 for k in range(0, 100 * 5)] + [130 + k / 5 for k in range(0, 100 * 5)]
    st = states_from_observations(_obs_file(tmp_path, "cam1", times))
    g = gt(session([ev(100, 130, event_type="camera_offline", eid="F1")], span=(0, 230)))
    r = run(tmp_path, g, [], states=st)
    x = m(r, "camera_offline")
    assert x["detected"] == 1
    assert x["latency"]["median_s"] == pytest.approx(2.8 + 0.0, abs=0.01)  # last frame 99.8 + 3 s - 100


def test_strata_breakdown_and_80_percent_rule(tmp_path):
    day = [ev(100 * k, 100 * k + 2, conditions={"lighting": "day"}) for k in range(1, 11)]
    ir = [ev(5000 + 100 * k, 5000 + 100 * k + 2, conditions={"lighting": "ir"}) for k in range(1, 11)]
    incs = [inc(e["start_s"], e["end_s"]) for e in day] + [inc(e["start_s"], e["end_s"]) for e in ir[:5]]
    r = run(tmp_path, gt(session(day + ir, span=(0, 7200))), incs)
    x = m(r)
    assert x["strata"]["lighting"]["day"]["recall"] == 1.0
    assert x["strata"]["lighting"]["ir"]["recall"] == 0.5
    assert x["recall"] == 0.75
    chk = {c["check"]: c for c in r["go_no_go"]["per_event_type"]["infant_off_surface"]["checks"]}
    assert chk["strata >= 80% of overall recall"]["status"] == "FAIL"  # 0.5 < 0.8 * 0.75
    assert "lighting=ir" in chk["strata >= 80% of overall recall"]["detail"]


# --------------------------------------------------------------------------- go / no-go

def _goal(**kw):
    base = dict(code="T", stage="test", recall_lower=0.90, n_min=0, fa_per_camera_hour=None,
                latency_p95_s=None, latency_budget_s=5.0)
    base.update(kw)
    return {"infant_off_surface": Goal(**base)}


def _perfect(n, spacing=100):
    gts = [ev(spacing * k, spacing * k + 2) for k in range(1, n + 1)]
    incs = [inc(spacing * k + 0.1, spacing * k + 2, created=spacing * k + 1) for k in range(1, n + 1)]
    return gts, incs


def test_zero_misses_in_30_is_not_enough_for_a_90_percent_two_sided_claim(tmp_path):
    gts, incs = _perfect(30)
    r = run(tmp_path, gt(session(gts, span=(0, 4000))), incs, goals=_goal())
    x = m(r)
    assert x["missed_event_rate_ci95"]["upper"] == pytest.approx(0.1157, abs=5e-5)
    assert x["missed_event_rate_rule_of_three_upper"] == pytest.approx(0.1)
    v = r["go_no_go"]["per_event_type"]["infant_off_surface"]
    assert v["verdict"] == "INSUFFICIENT_DATA"
    assert v["checks"][0]["n_required"] == 36  # the exact two-sided bound needs 36
    assert r["go_no_go"]["decision"] == "NO-GO"


def test_one_sided_decision_needs_rule_of_three_n(tmp_path):
    gts, incs = _perfect(30)
    r = run(tmp_path, gt(session(gts, span=(0, 4000))), incs, goals=_goal(), decision_sided="one")
    v = r["go_no_go"]["per_event_type"]["infant_off_surface"]
    assert v["verdict"] == "PASS"  # 0.05**(1/30) = 0.905 >= 0.90 with n = 30 = rule of three
    assert r["go_no_go"]["decision"] == "GO"
    gts, incs = _perfect(29)
    r = run(tmp_path, gt(session(gts, span=(0, 4000))), incs, goals=_goal(), decision_sided="one")
    # 29 is enough for the exact one-sided bound (0.9018) but below the rule of three: refuse.
    assert r["go_no_go"]["per_event_type"]["infant_off_surface"]["verdict"] == "INSUFFICIENT_DATA"


def test_small_n_never_passes_even_with_perfect_results(tmp_path):
    gts, incs = _perfect(3)
    r = run(tmp_path, gt(session(gts)), incs, goals=_goal(recall_lower=0.5))
    v = r["go_no_go"]["per_event_type"]["infant_off_surface"]
    assert m(r)["recall"] == 1.0
    assert v["verdict"] == "INSUFFICIENT_DATA"  # rule of three: 3/(1-0.5) = 6 positives needed
    assert v["checks"][0]["n_rule_of_three"] == 6


def test_preregistered_n_and_pass(tmp_path):
    gts, incs = _perfect(60)
    r = run(tmp_path, gt(session(gts, span=(0, 7000))), incs, goals=_goal(n_min=60, latency_p95_s=5.0))
    assert r["go_no_go"]["per_event_type"]["infant_off_surface"]["verdict"] == "PASS"
    assert r["go_no_go"]["decision"] == "GO"
    r = run(tmp_path, gt(session(gts[:59], span=(0, 7000))), incs[:59], goals=_goal(n_min=60))
    assert r["go_no_go"]["per_event_type"]["infant_off_surface"]["verdict"] == "INSUFFICIENT_DATA"


def test_recall_far_below_goal_fails_regardless_of_n(tmp_path):
    gts, incs = _perfect(10)
    r = run(tmp_path, gt(session(gts)), incs[:2], goals=_goal(n_min=60))
    assert r["go_no_go"]["per_event_type"]["infant_off_surface"]["verdict"] == "FAIL"


def test_false_alert_goal_needs_enough_camera_hours(tmp_path):
    gts, incs = _perfect(60)
    goals = _goal(fa_per_camera_hour=0.01)
    r = run(tmp_path, gt(session(gts, span=(0, 7000))), incs, goals=goals)
    chk = {c["check"]: c for c in r["go_no_go"]["per_event_type"]["infant_off_surface"]["checks"]}
    assert chk["false alerts / camera-hour"]["status"] == "INSUFFICIENT_DATA"  # 1.9 h << 369 h
    # 400 camera-hours (10 sessions x 40 h), 0 false alerts: the two-sided bound 3.69/400 < 0.01.
    sess = [session(gts if k == 0 else [], sid=f"S{k}", camera="cam1" if k == 0 else f"c{k}", span=(0, 40 * 3600))
            for k in range(10)]
    r = run(tmp_path, gt(*sess), incs, goals=goals)
    chk = {c["check"]: c for c in r["go_no_go"]["per_event_type"]["infant_off_surface"]["checks"]}
    assert chk["false alerts / camera-hour"]["status"] == "PASS"
    # Two false alerts in the same hours: upper bound 7.22/400 = 0.018 > 0.01 -> FAIL.
    r = run(tmp_path, gt(*sess), incs + [inc(9000, 9001), inc(9500, 9501)], goals=goals)
    chk = {c["check"]: c for c in r["go_no_go"]["per_event_type"]["infant_off_surface"]["checks"]}
    assert chk["false alerts / camera-hour"]["status"] == "FAIL"


def test_required_lighting_strata(tmp_path):
    gts, incs = _perfect(60)
    r = run(tmp_path, gt(session(gts, span=(0, 7000))), incs,
            goals=_goal(required_strata={"lighting": ["day", "ir"]}))
    v = r["go_no_go"]["per_event_type"]["infant_off_surface"]
    st = {c["check"]: c["status"] for c in v["checks"]}
    assert st["stratum lighting=day"] == "PASS" and st["stratum lighting=ir"] == "INSUFFICIENT_DATA"
    assert v["verdict"] == "INSUFFICIENT_DATA"


def test_mock_source_can_never_pass(tmp_path):
    gts, _ = _perfect(60)
    incs = [inc(100 * k + 0.1, 100 * k + 2, created=100 * k + 1, source="MOCK") for k in range(1, 61)]
    r = run(tmp_path, gt(session(gts, span=(0, 7000))), incs, goals=_goal())
    assert r["go_no_go"]["per_event_type"]["infant_off_surface"]["verdict"] == "INVALID_SOURCE"
    assert r["go_no_go"]["decision"] == "NO-GO"


def test_unlocked_or_unadjudicated_ground_truth_blocks_go(tmp_path):
    gts, incs = _perfect(60)
    r = run(tmp_path, gt(session(gts, span=(0, 7000)), locked=False), incs, goals=_goal())
    assert r["go_no_go"]["decision"] == "NO-GO"
    assert any("not locked" in b for b in r["go_no_go"]["blockers"])
    gts[0]["annotators"] = ["only-one"]
    r = run(tmp_path, gt(session(gts, span=(0, 7000))), incs, goals=_goal())
    assert any("annotators" in b for b in r["go_no_go"]["blockers"])


def test_research_only_types_are_report_only(tmp_path):
    r = run(tmp_path, gt(session([ev(100, 103, event_type="handling_abrupt")])),
            [inc(100, 103, event_type="handling_abrupt")])
    assert r["go_no_go"]["per_event_type"]["handling_abrupt"]["verdict"] == "REPORT_ONLY"
    assert r["go_no_go"]["release_types"] == []
    assert r["go_no_go"]["decision"] == "NO-GO"


def test_goals_file_overrides_defaults(tmp_path):
    p = tmp_path / "goals.json"
    p.write_text(json.dumps({"goals": {"infant_off_surface": {"recall_lower": 0.95},
                                       "custom_event": {"recall_lower": 0.5, "latency_budget_s": 2}}}))
    g = load_goals(p)
    assert g["infant_off_surface"].recall_lower == 0.95 and g["infant_off_surface"].n_min == 60
    assert g["custom_event"].latency_budget_s == 2
