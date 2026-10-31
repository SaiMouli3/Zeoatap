# tools/eval: event-level safety evaluation

This harness scores system incidents against adjudicated ground truth, following [SAFETY_VALIDATION](../../docs/SAFETY_VALIDATION.md) §4 (metrics), §5 (goals) and §7 (go/no-go). **No validation has been run with it on real data.** The only inputs so far are synthetic test fixtures and a synthetic test pattern video.

```bash
cd tools/eval
uv sync
uv run pytest -q                       # 45 unit tests
uv run zeoatap-eval validate examples/ground_truth.example.json
uv run zeoatap-eval evaluate --ground-truth examples/ground_truth.example.json \
    --incidents examples/incidents.replay.example.jsonl --out-md report.md --out-json report.json
```

## What it computes

| Metric | How |
|---|---|
| Matching | Same `event_type`, an incident camera in the session's cameras, and either temporal overlap or incident start in [GT.start − 1 s, GT.end + `latency_budget`]. One-to-one via the Hungarian algorithm (scipy) on temporal IoU. Weights are `BIG + IoU`, so the solver first maximises the number of matched events, then total IoU. With pure IoU weights, one high-IoU pair could leave a second event unmatched (see `test_hungarian_maximises_matched_events_before_iou`). |
| Duplicates | Eligible incidents left unassigned. They are not TPs. They lower precision and dedup accuracy. They are not false alerts unless you pass `--count-duplicates-as-false-alerts`. |
| Recall, missed-event rate | Exact 95% Clopper–Pearson interval. When there are 0 misses, the rule-of-three bound 3/n is also shown. For 0/30 misses the exact two-sided upper bound is 0.1157, the one-sided bound is 0.0950, and the rule of three gives 0.100. |
| Precision | Matched conclusive incidents ÷ all conclusive in-scope incidents, with Clopper–Pearson. |
| INCONCLUSIVE | Kept separate. A GT event covered only by an `INCONCLUSIVE` incident gets the outcome `inconclusive`, not `detected`. Inconclusive incidents never count as FP or TP. `recall_including_inconclusive` is reported alongside. |
| False alerts / camera-hour | Unmatched incidents with an alert-severity status (`ALERTED,SHADOW` by default; `--alert-statuses`) ÷ FULLY_OPERATIONAL camera-hours, with an exact Poisson interval. With `--observations obs.jsonl`, operational time is rebuilt the way core's `cameraHealth.stateAt` does it (stale > 3 s → CAMERA_UNAVAILABLE, `ai_available=false` → AI_UNAVAILABLE, degraded → VIDEO_DEGRADED). Non-operational hours are reported separately. Without state data, all scheduled time counts as operational, and the report says so. |
| Latency | `created_at − GT.start` (median, p95, max). For replay output, `created_at` is the incident's `end` on its `incident_created` line, i.e. the moment the rule confirmed it. For API exports it is the `created_at` field. |
| Dedup accuracy | GT events with exactly one incident ÷ all GT events. Also reported over detected events only. |
| Confusion | Per GT type: detected / INCONCLUSIVE / missed / `wrong_type:X`. A miss where an incident of another type overlapped is counted under the type the system reported. |
| Strata | Recall, latency and false alerts per `lighting`, `occlusion` and `camera_angle`. Event-level `conditions` override the session's. |
| Hard negatives | Staged non-events (adult lifts infant, rocking) listed per session. An FP that overlaps one is labelled with it. |
| Camera health (D1/D2) | When GT contains `camera_offline` or `camera_obstructed` events and state data is given, each non-operational state interval becomes a pseudo-incident. Its `created_at` is when core would show it. |

### Go / no-go
Every event type in the GT that has a deployable, non-research goal must PASS for **GO**. The default goals come from §5 (`goals.py`, override with `--goals goals.json`). For each type the tool checks the following:

- **Recall:** the 95% Clopper–Pearson lower bound must be ≥ the goal. The default is two-sided (conservative). `--decision-sided one` uses one-sided bounds, which match the doc's rule-of-three arithmetic.
- **Small-n refusal:** the verdict is `INSUFFICIENT_DATA` (never PASS) when n < max(pre-registered n, rule-of-three n = ⌈3/(1−goal)⌉, exact zero-miss n). For a 0.90 goal that is max(60, 30, 36) = 60 for A1/D3. If even the upper bound is below the goal, the verdict is FAIL whatever the n.
- **Required strata:** for A1, each of `lighting=day` and `lighting=ir` needs the same n and bound.
- **Stratum floor:** no stratum with n ≥ 5 may fall below 80% of overall recall.
- **False alerts:** FA/camera-hour upper bound ≤ goal. With fewer camera-hours than needed to bound the rate with 0 observed (3.69/goal two-sided, 3.00/goal one-sided), the verdict is `INSUFFICIENT_DATA`.
- **Latency:** p95 ≤ goal.
- **Source:** any `MOCK` source gives `INVALID_SOURCE`.

Global blockers: the dataset is not `locked`; any GT event has fewer than 2 annotators or is not adjudicated; MOCK observations or incidents in the input. B1/B2 are `REPORT_ONLY` and E1 is `NOT_DEPLOYABLE`. The manual checklist items in §7 are listed in the report but not verified.

## Ground-truth format
The schema is in [`src/zeoatap_eval/ground_truth.schema.json`](src/zeoatap_eval/ground_truth.schema.json) and an example is in [`examples/ground_truth.example.json`](examples/ground_truth.example.json). There is one entry per recording session:

- Pseudonymous `session_id`, `facility_id` and `camera_id` (`extra_camera_ids` for other cameras on the same room zones). Optional `room_id`, `subject` (e.g. `manikin-M2`, never a child's name) and `consent_ids`.
- `conditions`: `lighting` ∈ day/ir/low_light/mixed, `occlusion` ∈ none/partial/heavy, `camera_angle` ∈ overhead/high_oblique/low_oblique/eye_level.
- `operating_span`: the fully annotated time. Only this span counts as analyzed, and incidents outside it are out of scope.
- `events`: `{id, event_type, room_zone, start, end, visibility, annotator_confidence, annotators, adjudicated}`. `event_type` uses core's rule names (`infant_off_surface`, `adult_in_restricted_zone`, `camera_offline`, …).
- `hard_negatives`.

Times are either RFC 3339 (`start`/`end`) or seconds from the video's frame 0 (`start_s`/`end_s`, which need the session's `t0`). Annotating in video seconds and letting the replay script fill in `t0` is the normal path.

## How to annotate

### Manikin recording protocol (summary of SAFETY_VALIDATION §2, §3, §10)
- **No real infants as positives.** Staged positives use a weighted manikin. Handling protocols (B1/B2) need one with an articulated neck, performed by trained staff. Adults in the scene are consenting staff or actors.
- **A1 falls:** run the drop protocol across surface heights (changing table, crib, sofa), surfaces, camera angles and lighting (day and IR), with and without an adult nearby. The definition (§2): *start* is the last frame in which any part of the body is supported by the raised surface. *End* is the first frame supported by the floor or an object below surface level, or held by an adult. Include roll-off, slide-off and climb-over. Exclude the adult lifting the infant or placing it on the floor.
- **Hard negatives matter as much as positives.** Record lifting from the crib, placing on the floor, rocking, bouncing, burping pats, quick lifts and play. Add them as `hard_negatives`.
- **Counts per event type and per major stratum (day/IR):** ≥ 30 positives with zero misses to claim a miss rate below 10%, and ≥ 60 for below 5% or for the §5 A1/D3 goals. The harness enforces this.
- **C1/C2:** ground truth is visibility and motion ("no visible movement for T", "cannot see the infant"). It is never breathing or wellbeing.
- **Splits:** split by recording session (and by camera, facility and manikin where possible). Hold out ≥ 1 facility or room layout. Lock and version the test set (`dataset.locked: true`).

### CVAT workflow
1. Create a CVAT task per recording session from the video file. Keep the frame rate (the import needs `--fps`).
2. Add one label per event type, named exactly like core's `event_type` (e.g. `infant_off_surface`). Give it these attributes: `visibility` (select: visible, partial, not_visible), `annotator_confidence` (select: certain, likely, unsure) and `room_zone` (text).
3. For each event, draw a **track** (a box around the subject is fine). Its first visible keyframe is the event start frame. Set **outside** on the first frame after the event ends. A track that leaves and re-enters becomes two events.
4. At least **two annotators** label each session independently in separate jobs or tasks. Export each as **"CVAT for video 1.1"** XML.
5. Import, match and compute agreement:
   ```bash
   uv run zeoatap-eval cvat-import --xml ann1.xml --annotator ann-03 --xml ann2.xml --annotator ann-07 \
       --fps 15 --labels infant_off_surface,adult_in_restricted_zone \
       --session-id S-0001 --facility-id F-7c2e --camera-id cam1 --duration-s 600 \
       --lighting ir --occlusion none --camera-angle high_oblique --out S-0001.gt.json
   ```
   Events both annotators marked (same label, overlapping, Hungarian on IoU) become GT events with `adjudicated: false`, spanning the earliest start to the latest end. Events only one annotator marked go to `S-0001.gt.adjudication.json`. The agreement (matched counts, mean temporal IoU, Cohen's κ on 1 s bins per label) is printed.
6. A senior reviewer resolves the adjudication queue, narrows boundaries and sets `adjudicated: true` (plus `adjudicator`). Then merge the sessions into one file, set `dataset.locked: true` for a test set, and run `zeoatap-eval validate`.

## End to end: video → incidents → report
[`scripts/run_pipeline.py`](scripts/run_pipeline.py) runs the whole chain for one local video file:

```
video ──ffmpeg -re──▶ MediaMTX (RTSP, runOnDemand) ──▶ vision-worker --detector X ──▶ observations.jsonl
      ──▶ go replay (services/core/cmd/replay) ──▶ incidents.jsonl ──▶ zeoatap-eval ──▶ report.md / report.json
```

```bash
cd tools/eval
uv run python scripts/run_pipeline.py --video S-0001.mp4 --ground-truth S-0001.gt.json --session-id S-0001 \
    --core-cameras core-cameras.json --protected --detector pose \
    --worker-arg=--det-model=yolox-tiny-humanart --out out/S-0001
```

- `--detector` is passed to `zeoatap-vision` and checked against its own `--help` at run time. Today it accepts `none`, `mock` and `pose`. Pass any other worker flags with repeated `--worker-arg=...`.
- `--core-cameras` is core's camera/zone config (`{"cameras":[{"id","room_id","zones":[{"id","room_zone","kind":"surface|restricted","polygon":[{"X","Y"}…]}]}]}`). Without it the replay has no zones, so the zone rules (A1, D3) cannot fire.
- MediaMTX starts the publisher only when vision-worker connects. By default the first frame is held for 3 s (`--lead-in-s`), so the worker is decoding before video frame 0. This forces a libx264 re-encode. `--lead-in-s 0 --transcode copy` publishes the original bitstream, but the first ~1.5 s are then not analyzed. The wall time of frame 0 becomes the session's `t0`. Expect roughly 0.1–0.5 s of alignment error (`run.json` records the first-observation offset).
- Requirements: MediaMTX at `.tools/mediamtx` (or `$MEDIAMTX_BIN`), ffmpeg/ffprobe with libx264, Go, uv. Every process it starts is stopped at the end, including the publisher, which MediaMTX starts in its own process group.

Smoke run (executed 2026-10-31): a 20 s `testsrc2` 640×360 clip, `--detector mock --protected`, with a GT containing one placeholder `infant_off_surface` event. Result: 286 observations, first observation 2.83 s before frame 0. The replay produced 3 state changes and 0 incidents. The report was **NO-GO**: the event was missed (correct, since there is no infant in a test pattern), and the blockers were MOCK input, unlocked GT, a single annotator and insufficient n. This exercises the plumbing only and says nothing about accuracy.
