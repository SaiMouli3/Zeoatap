# Safety Validation Framework

**Date:** 2026-10-27 · **Status:** Protocol defined. **No validation has been executed.** All numbers below are *engineering goals*, not achieved performance.

---

## 1. Principles

- **Accuracy is not a safety metric.** In a nursery, >99.9% of frames are "normal", so a model that never alerts is 99.9% accurate and useless.
- **Measure at the event level,** per event type, per condition stratum.
- **Pre-register** acceptance criteria before looking at test results.
- **Human review is mandatory** for every serious incident. The system is a review aid.

---

## 2. Event definitions (annotation contract)

Each event type has a written definition with **start**, **end**, **inclusion** and **exclusion** criteria. Example:

**A1 `infant_off_surface`**
- *Start:* last frame in which any part of the infant's body is supported by the raised surface.
- *End:* first frame in which the infant is supported by the floor or an object below surface level, or is held by an adult.
- *Include:* rolling off, sliding off, climbing over a rail and landing.
- *Exclude:* the adult lifts the infant (held throughout), or the infant is placed on the floor by an adult.
- *Visibility:* annotate `visible`, `partial` or `not_visible` for the surface and floor regions.

---

## 3. Annotation methodology

### 3.1 Schema (per recording session)

| Layer | Fields |
|---|---|
| Recording | session_id, facility_id (pseudonymous), camera_id, resolution, fps, codec, IR mode, lighting, camera height/angle, consent_ids |
| Tracks | track_id, role ∈ {infant, adult}, bbox per frame (keyframes + interpolation), held_by |
| Pose | 17 keypoints + visibility flag per keypoint (sampled frames), pose_quality_annotator ∈ {good, partial, unusable} |
| Activities | segments of routine care: lift, carry, place, feed, change, rock, bounce, soothe, play, sleep-settle |
| Candidate safety events | event_type, start, end, severity_label, visibility, annotator_confidence ∈ {certain, likely, unsure} |
| Conditions | occlusion (blanket, crib bars, adult body), blur, low light, IR, obstruction |
| Adjudication | ≥2 independent annotators for safety events. A third, senior reviewer adjudicates disagreements. Inter-annotator agreement reported (Cohen's κ for labels, temporal IoU for boundaries) |

### 3.2 Splits and leakage prevention
- Split by **recording session**, and where possible by **camera**, **facility** and **subject/manikin**.
- **Never** place clips from the same session in different splits. Neighbouring windows are near-duplicates.
- Hold out ≥1 facility (or ≥1 room layout) as an **out-of-distribution test** set.
- The test set is locked and versioned. Only the validation owner has access, and evaluation runs are logged.

---

## 4. Metrics

| Metric | Definition |
|---|---|
| **Event-level recall** | (# ground-truth events matched by ≥1 system incident) / (# GT events) |
| **Event-level precision** | (# system incidents matched to a GT event) / (# system incidents) |
| **Matching rule** | An incident matches a GT event of the *same type* if their time intervals overlap, or the incident start falls within [GT.start − 1 s, GT.end + `latency_budget`]. Each GT event matches at most one incident (Hungarian on temporal IoU). Extra incidents on the same GT event count as **duplicates**, not TPs |
| **False alerts per camera-hour** | Unmatched *alert-severity* incidents ÷ analyzed camera-hours (only hours with FULLY_OPERATIONAL status count; degraded hours are reported separately) |
| Missed-event rate | 1 − recall, reported with a 95% Clopper–Pearson interval |
| Detection latency | incident.created_at − GT.start (median, p95) |
| Alert delivery latency | first delivery receipt − incident.created_at |
| Staff acknowledgement latency | ack_at − first delivery (pilot) |
| Dedup accuracy | Fraction of GT events producing exactly one incident |
| Camera uptime | Fraction of scheduled time with stream OK |
| Processing uptime | Fraction of scheduled time with state = FULLY_OPERATIONAL |
| Frame drop rate | Dropped ÷ sampled frames |
| Performance under occlusion | All metrics stratified by occlusion, IR, lighting, camera angle |
| Per-event confusion matrices | Including `inconclusive` as a separate outcome |
| Calibration | Reliability diagram + expected calibration error for each learned score |
| Recovery time | Fault injected → state back to FULLY_OPERATIONAL |

### 4.1 Frame-level vs. event-level
A 10-second event at 15 fps has 150 frames. A frame classifier that is right on 140 of them is not "140 true positives". At event level it is **one** detected event. Conversely, three separate incidents for that one event are 1 TP + 2 duplicates (alert fatigue). **All reported results are event-level.** Frame-level metrics are diagnostic only.

### 4.2 Rare events
- With *n* positive events and **0 misses**, the 95% upper bound on the miss rate is ≈ 3/*n* ("rule of three"). **To claim a miss rate below 10% with 95% confidence you need ≥30 positives with zero misses. For below 5%, you need ≥60.**
- Therefore staged positives (manikin protocol) must reach these counts **per event type and per major stratum** (day/IR).
- False-alert rate needs **camera-hours**. For example, to bound high-priority false alerts below 0.01/camera-hour with ~95% confidence and 0 observed, you need ≈300 camera-hours (3/0.01).

---

## 5. Pre-registered acceptance goals (proposed, per event type)

| Event | Stage | Recall goal (lower 95% CI bound) | False alerts / camera-hour goal | Latency p95 goal |
|---|---|---|---|---|
| D1/D2 camera health | Pilot alert | ≥ 0.99 on injected faults (n≥100) | ≤ 0.01 | ≤ 10 s |
| D3 adult in restricted zone | Pilot alert | ≥ 0.90 (n≥60) | ≤ 0.02 | ≤ 5 s |
| A1 infant off surface | Shadow → alert | ≥ 0.90 (n≥60 manikin, both lighting modes) | ≤ 0.005 | ≤ 5 s |
| D4 infant in hazard zone | Review | ≥ 0.85 | ≤ 0.05 (review) | ≤ 30 s |
| C1/C2 immobility / covered | Review only | ≥ 0.80 | ≤ 0.05 (review) | n/a |
| B1/B2 handling | **Research only** | Report only. No deployment goal until feasibility proven | — | — |
| E1 respiration | **Not deployed** | — | — | — |

Facility-level sanity budget [E]: 16 cameras × 10 operating hours = 160 camera-hours/day. 0.005 FA/cam-hr ≈ **0.8 false high-priority alerts per day**, which is roughly the upper limit that staff will tolerate before alert fatigue sets in. The tolerable level must be confirmed with staff interviews (OQ-P3).

---

## 6. Validation phases

| Phase | What | Exit criteria |
|---|---|---|
| **1. Offline replay** | Replay authorized recordings through the full pipeline (file → RTSP simulator) | Pipeline is deterministic (same input → same incidents). No crashes over 100 h of replay |
| **2. Annotated test set** | Locked test set, pre-registered metrics | Goals in §5 met on the validation set first. Test set run once per release candidate |
| **3. Shadow mode** | Live in facility. Incidents go to a hidden queue reviewed by the project team. **Staff see nothing, and no caregiving decisions change** | ≥4 weeks or ≥ required camera-hours. False alert rate within goal. All misses root-caused |
| **4. Supervised pilot** | Alerts visible to staff, with human review mandatory | Stop conditions (§8) never triggered. Staff survey on alert burden. Independent review of results |
| **5. Controlled expansion** | More rooms, then more facilities, after an independent review | Per-facility metrics within goal. No unresolved serious incidents |

---

## 7. Go/no-go checklist (per event type, per release)

- [ ] Event definition approved by the safety lead + childcare domain expert
- [ ] Test set locked. Positives ≥ required n per stratum
- [ ] Recall lower CI bound ≥ goal; FA/camera-hour ≤ goal; latency p95 ≤ goal
- [ ] Performance reported per stratum (IR, occlusion, angle). No stratum below 80% of overall recall without a documented mitigation
- [ ] Shadow mode completed with required camera-hours
- [ ] Failure-injection suite passes (TESTING_STRATEGY §7)
- [ ] Monitoring-unavailable states verified to display correctly
- [ ] Rollback tested on the target hardware
- [ ] Privacy review sign-off for any new data flows
- [ ] Release manifest signed with `validated_for_alerting=true` referencing this report

## 8. Pilot stop conditions (pre-defined)
- Any missed GT event in a severity class where the system had claimed coverage *and* staff relied on it. Pause and run a root-cause review.
- High-priority false alerts above 3× goal for 3 consecutive days.
- Any monitoring-status display error (green shown while not analyzing).
- Any privacy incident (unauthorized clip access).
- Staff report reduced supervision due to reliance on the system.

## 9. Rollback process
1. Automatic: canary metric breach → revert to the previous signed release (≤5 min).
2. Manual: ops command, with a reason logged to audit.
3. Post-rollback: incidents created under the bad release are flagged for re-review.
4. Root-cause analysis before re-release.

---

## 10. Per-event validation notes

- **Handling (B1/B2):** positives can only come from manikin protocols performed by trained staff, with a weighted manikin with an articulated neck. **Hard negatives** are the core of the dataset: rocking, bouncing, burping pats, quick lifts, play. If a classifier cannot achieve high precision against hard negatives, the feature **does not ship**. Any output is labelled "movement pattern flagged for review", never "rough handling".
- **Prolonged immobility (C1):** the ground truth is about *visibility and motion*, not health. Validate that the system correctly reports "no visible movement for T" and **"cannot see the infant"**. Never validate or claim anything about breathing or wellbeing.
- **Falls (A1):** manikin drop protocol across heights, surfaces, camera angles, lighting, with and without an adult nearby. Plus hard negatives: placing infants on the floor and lifting from the crib.
