import json
from pathlib import Path

import jsonschema
import pytest

from helpers import ev, gt, session
from zeoatap_eval.__main__ import main
from zeoatap_eval.cvat import cohens_kappa_bins, match_annotators, parse_cvat_video_xml
from zeoatap_eval.model import load_ground_truth, load_incidents, parse_time, states_from_jsonl

EX = Path(__file__).parents[1] / "examples"


def test_parse_time_handles_go_nanoseconds_and_offsets():
    assert parse_time("2026-10-31T09:00:00.123456789Z") == pytest.approx(parse_time("2026-10-31T09:00:00.123457Z"), abs=1e-6)
    assert parse_time("2026-10-31T11:00:00+02:00") == parse_time("2026-10-31T09:00:00Z")
    with pytest.raises(ValueError):
        parse_time("2026-10-31T09:00:00")


def test_example_ground_truth_is_valid():
    g = load_ground_truth(EX / "ground_truth.example.json")
    assert len(g.events) == 3
    assert g.events[1].conditions["occlusion"] == "partial"  # event override of session conditions
    assert g.sessions[0].hard_negatives[0].label == "adult_lifts_infant"


def test_schema_rejects_bad_ground_truth():
    bad = gt(session([ev(1, 2)]))
    bad["sessions"][0]["events"][0]["visibility"] = "sort of"
    with pytest.raises(jsonschema.ValidationError):
        load_ground_truth(bad)
    both = gt(session([ev(1, 2)]))
    both["sessions"][0]["events"][0]["start"] = "2026-10-31T09:00:01Z"
    with pytest.raises(jsonschema.ValidationError):
        load_ground_truth(both)
    no_t0 = gt(session([ev(1, 2)]))
    del no_t0["sessions"][0]["t0"]
    with pytest.raises(ValueError, match="t0"):
        load_ground_truth(no_t0)
    dup = gt(session([ev(1, 2, eid="X"), ev(3, 4, eid="X")]))
    with pytest.raises(ValueError, match="duplicate"):
        load_ground_truth(dup)


def test_ground_truth_warnings():
    g = load_ground_truth(gt(session([ev(1, 2, annotators=("solo",), adjudicated=False)]), locked=False))
    text = " ".join(g.warnings)
    assert "locked" in text and "2 independent annotators" in text and "not adjudicated" in text


def test_api_incident_shapes(tmp_path):
    inc = {"id": "INC-1", "room_id": "r", "room_zone": "z", "event_type": "infant_off_surface", "family": "A",
           "start": "2026-10-31T09:00:01Z", "end": "2026-10-31T09:00:04Z", "cameras": ["cam1"],
           "status": "SHADOW", "created_at": "2026-10-31T09:00:02Z", "source": "p"}
    for name, text in [("a.json", json.dumps([inc])), ("b.json", json.dumps({"incidents": [inc]})),
                       ("c.jsonl", json.dumps(inc) + "\n" + json.dumps({**inc, "id": "INC-2"}) + "\n")]:
        p = tmp_path / name
        p.write_text(text)
        got = load_incidents(p)
        assert got[0].created_at == parse_time("2026-10-31T09:00:02Z")
        assert got[0].status == "SHADOW"


def test_state_jsonl_timeline(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [
        {"camera_id": "cam1", "state": "FULLY_OPERATIONAL", "since": "2026-10-31T09:00:00Z"},
        {"camera_id": "cam1", "state": "CAMERA_UNAVAILABLE", "since": "2026-10-31T09:30:00Z"},
        {"camera_id": "cam1", "state": "FULLY_OPERATIONAL", "since": "2026-10-31T09:40:00Z"},
        {"camera_id": "cam1", "state": "NOT_CONNECTED", "since": "2026-10-31T10:00:00Z"},
        {"kind": "state_change", "camera_id": "cam1", "state": "AI_UNAVAILABLE"},  # replay line, no time: ignored
    ]))
    tl = states_from_jsonl(p)
    a, b = parse_time("2026-10-31T09:00:00Z"), parse_time("2026-10-31T10:00:00Z")
    assert tl.time_in("cam1", a, b, {"FULLY_OPERATIONAL"}) == pytest.approx(50 * 60)


CVAT_XML = """<?xml version="1.0" encoding="utf-8"?>
<annotations><version>1.1</version>
 <track id="0" label="infant_off_surface" source="manual">
  <box frame="150" outside="0" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0">
   <attribute name="visibility">partial</attribute><attribute name="annotator_confidence">likely</attribute>
   <attribute name="room_zone">changing-table-1</attribute></box>
  <box frame="165" outside="0" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0"/>
  <box frame="171" outside="1" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0"/>
  <box frame="300" outside="0" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0"/>
  <box frame="330" outside="1" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0"/>
 </track>
 <track id="1" label="adult" source="manual">
  <box frame="0" outside="0" occluded="0" keyframe="1" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0"/>
 </track>
</annotations>"""


def test_cvat_tracks_become_events(tmp_path):
    p = tmp_path / "a.xml"
    p.write_text(CVAT_XML)
    evs = parse_cvat_video_xml(str(p), fps=15, labels={"infant_off_surface"})
    assert [(e.start_s, round(e.end_s, 3)) for e in evs] == [(10.0, round(170 / 15, 3)), (20.0, round(329 / 15, 3))]
    assert evs[0].attrs["visibility"] == "partial"
    pairs, oa, ob = match_annotators(evs, evs[:1])
    assert len(pairs) == 1 and pairs[0][2] == pytest.approx(1.0) and len(oa) == 1 and not ob
    k = cohens_kappa_bins(evs, evs, 60)
    assert k["infant_off_surface"] == pytest.approx(1.0)


def test_cli_cvat_import_two_annotators(tmp_path, capsys):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    a.write_text(CVAT_XML)
    b.write_text(CVAT_XML.replace('frame="300" outside="0"', 'frame="900" outside="0"').replace('frame="330" outside="1"', 'frame="930" outside="1"'))
    out = tmp_path / "gt.json"
    assert main(["cvat-import", "--xml", str(a), "--annotator", "ann-1", "--xml", str(b), "--annotator", "ann-2",
                 "--fps", "15", "--labels", "infant_off_surface", "--session-id", "S9", "--facility-id", "F1",
                 "--camera-id", "cam1", "--t0", "2026-10-31T09:00:00Z", "--duration-s", "120", "--out", str(out)]) == 0
    g = load_ground_truth(out)
    assert len(g.events) == 1 and g.events[0].annotators == ["ann-1", "ann-2"] and not g.events[0].adjudicated
    q = json.loads(out.with_suffix(".adjudication.json").read_text())
    assert len(q["disagreements"]) == 2  # one event each annotator marked alone
    assert "cohens_kappa_1s_bins" in capsys.readouterr().out


def test_cli_evaluate_writes_markdown_and_json(tmp_path):
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    assert main(["evaluate", "--ground-truth", str(EX / "ground_truth.example.json"),
                 "--incidents", str(EX / "incidents.replay.example.jsonl"),
                 "--out-md", str(md), "--out-json", str(js)]) == 0
    r = json.loads(js.read_text())
    a1 = r["per_event_type"]["infant_off_surface"]
    assert (a1["detected"], a1["inconclusive"], a1["false_positives"]) == (1, 1, 1)
    assert r["go_no_go"]["decision"] == "NO-GO"
    text = md.read_text()
    assert "Decision: NO-GO" in text and "Clopper" in text and "adult_lifts_infant" in text
