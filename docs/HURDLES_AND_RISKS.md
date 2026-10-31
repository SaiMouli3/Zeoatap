# Hurdle & Risk Register

**Date:** 2026-10-27. Every obstacle we expect between this plan and a validated product, with how hard it is to solve and how we solve it.

**Solvability scale:**
- 🟢 **Easy:** known engineering, days to weeks
- 🟡 **Moderate:** known approach, needs effort or data, weeks to months
- 🟠 **Hard:** uncertain outcome, research or negotiation, months
- 🔴 **Blocking / may be unsolvable:** could stop a feature entirely, so a fallback is defined

---

## 1. Data & ML hurdles

| ID | Hurdle | Why it matters | Solvability | Solution / fallback | Phase |
|---|---|---|---|---|---|
| H-01 | **No public infant-safety-incident dataset.** Real positives cannot ethically be staged | No training or validation data for falls/handling | 🔴 | Manikin protocols (weighted, articulated), synthetic rendering (licensed body models), consented staged *non-harmful* proxies. Validation claims are limited to "on manikin protocol X". Handling features stay research-only unless feasibility is shown | 0–3 |
| H-02 | Infant-specific models (FiDIP, InfAct) and **all ACLab datasets are non-commercial** | Can't ship the best-known infant models | 🟠 | Commercial-license request to ACLab (OQ-L1). Fallback: Apache RTMPose fine-tuned on own data. Use FiDIP only as a research benchmark | 0 |
| H-03 | Adult pose → infant domain gap (proportions, lying poses, blankets) | Poor keypoints → rule failures | 🟡 | Fine-tune on 2–5k labelled infant frames. Pose-quality gating → INCONCLUSIVE instead of a wrong answer | 2 |
| H-04 | Rough handling vs. rocking/bouncing are visually similar | False accusations or missed events | 🔴 | Review-only signal. Hard-negative-heavy dataset. Never use accusatory wording. Drop the feature if precision is inadequate (Experiment E3) | 3+ |
| H-05 | Prolonged stillness is normal (sleep) | Constant false alerts | 🟡 | Sleep zones/periods from facility schedule. Low-priority review only. Validate visibility, not wellbeing | 2 |
| H-06 | Breathing estimation is not validated for CCTV, the data is NC, and it carries medical-device exposure | Legal and safety risk | 🔴 | **Not a product feature.** Separate research track with IRB/ethics and clinical partner, if ever pursued | Later |
| H-07 | **Small pixel size:** a 0.6 m infant ≈ 97 px at 5 m on 1080p/100° FOV | Detection/pose unreliable | 🟡 | Camera placement guidelines (≤4 m, angled at cribs/tables). Main-stream ROI crops on infant tracks. Per-camera feasibility check at install | 1 |
| H-08 | Fall lasts 0.35–0.45 s | Missed at low fps | 🟢 | State-change logic (on surface → below surface) + ≥15 fps on raised-surface cameras | 2 |
| H-09 | Night/IR footage domain shift | Accuracy drops at nap time | 🟡 | IR in training + test strata. Separate thresholds. Report IR metrics separately | 2 |
| H-10 | Occlusion by crib bars, blankets, caregivers' bodies | Lost tracks, false "no movement" | 🟡 | Visibility estimation. INCONCLUSIVE state. Multi-camera coverage of key cribs | 2–3 |
| H-11 | Caregiver holding infant → infant "leaves surface" | Massive false-alert source | 🟢 | Explicit `held` state suppresses A1/D4 | 2 |
| H-12 | Few positives → wide confidence intervals | Can't prove safety claims | 🟡 | Rule of three: ≥30–60 positives per stratum. Report CIs, never point estimates alone | 2–4 |
| H-13 | Leakage between train/test (adjacent clips) | Inflated metrics | 🟢 | Session/camera/facility-level splits. Held-out facility | 2 |
| H-14 | Pretrained weights' data provenance (COCO, Kinetics/YouTube) unclear for commercial use | Legal risk on shipped weights | 🟠 | Counsel review (OQ-L3). Retrain from scratch on licensed data if needed (cost: compute + labelling) | 0–3 |
| H-15 | Model drift (new furniture, seasons, staff) | Gradual degradation | 🟡 | Drift monitors (score distributions, alert rate vs. baseline), periodic re-calibration, view-change detection | 4+ |
| H-16 | VLM hallucinations / over-claiming | Misleading reviewers | 🟡 | Advisory only, schema + banned terms, frame citations, A/B evaluation before ship | 4+ |

## 2. Video & infrastructure hurdles

| ID | Hurdle | Solvability | Solution |
|---|---|---|---|
| H-20 | Heterogeneous cameras/NVRs (RTSP quirks, ONVIF variance, H.265 profiles) | 🟡 | Compatibility test matrix. Prefer direct camera RTSP. NVR re-stream fallback. Pre-install site survey tool |
| H-21 | Camera clocks drift. RTCP not always sent | 🟢 | Edge as NTP server, receive-time fallback flagged, skew alarms, disable cross-cam merge when skewed |
| H-22 | Wi-Fi cameras drop frames | 🟢 | Require wired PoE for monitored cameras, or mark them DEGRADED |
| H-23 | GPU/driver/CUDA/TensorRT/DeepStream version lock (DS 8.0 lacks Orin, CUDA 13 + Triton issues). **Seen in the benchmark:** a kernel auto-upgrade unloaded a `.run`-installed driver, onnxruntime-gpu 1.31 needs CUDA 13, TensorRT 11 dropped the FP16 builder flag ([BENCHMARK_RESULTS](BENCHMARK_RESULTS.md)) | 🟡 | Pin the full stack in one container image per hardware SKU. Pin the kernel or use DKMS drivers. Build TRT engines on device. Avoid Triton in MVP |
| H-24 | OpenMMLab stack in maintenance mode | 🟢 | Train in pinned env, export ONNX, infer with ONNX Runtime/TensorRT (rtmlib) |
| H-25 | Browser playback of H.265 clips | 🟢 | Transmux/transcode H.265 → H.264 for review copies, or require H.264 at install |
| H-26 | GPU overload during bursts | 🟢 | Bounded queues, budget scheduler, protected cameras, visible DEGRADED state |
| H-27 | Frozen pipeline appears healthy | 🟢 | Heartbeat-age check ("dead-man's switch"). Green requires an observation < 5 s old |
| H-28 | Disk fills with clips | 🟢 | Quotas, retention purge first, alerts at 80/90% |
| H-29 | Facility internet unreliable | 🟢 | Edge-first design. Local siren/dashboard alerts. Store-and-forward |
| H-30 | Remote fleet updates break sites | 🟡 | Signed A/B updates, staged rollout (canary sites), automatic rollback |
| H-31 | Hardware failure of the single edge box | 🟡 | Health heartbeat to cloud → vendor alert. Spare-unit SLA. Facility notified "monitoring unavailable". Optional 2-node HA for large sites |

## 3. Legal, privacy & ethics hurdles

| ID | Hurdle | Solvability | Solution |
|---|---|---|---|
| H-40 | Jurisdiction-specific childcare camera, consent and biometric laws | 🟠 | Counsel per launch state/country (Phase 0 gate). Start in one jurisdiction |
| H-41 | Parent and staff consent and acceptance | 🟡 | Clear notices, no biometrics, no training use by default, staff consultation, opt-out handling (room-level) |
| H-42 | Could pose data be "biometric"? | 🟠 | Don't persist keypoints outside incidents. No identification. Counsel opinion (OQ-L6) |
| H-43 | Perceived as staff surveillance / union objection | 🟡 | No performance scoring. Aggregate response metrics only. Staff involvement in design |
| H-44 | Liability if the system misses an event | 🟠 | Contractual positioning as a support tool, clear "not monitored" displays, insurance, validated claims only |
| H-45 | False accusation harm to caregivers | 🟡 | Neutral language, mandatory human review, disposition workflow, no automatic external reporting |
| H-46 | Medical-device classification risk (immobility/breathing) | 🟢 if avoided | No health claims. Immobility is framed as "no visible movement — please check" |
| H-47 | Evidence chain of custody | 🟡 | Hash-chained audit, original-packet clips (no re-encode), export with manifest + hashes |

## 4. Product & operational hurdles

| ID | Hurdle | Solvability | Solution |
|---|---|---|---|
| H-50 | Alert fatigue | 🟡 | Per-event goals (≤ ~1 high-priority false alert/facility/day), shadow mode first, review vs. alert tiers, dedup |
| H-51 | Over-reliance / reduced supervision | 🟡 | Training, contract terms, UI messaging, pilot stop condition |
| H-52 | Camera placement doesn't cover the hazards | 🟡 | Site survey + coverage map + blind-spot report. A rule is only enabled when its zone is visible |
| H-53 | Install and calibration effort per site | 🟡 | Guided zone editor, calibration snapshots, remote assistance |
| H-54 | Staff turnover → untrained users | 🟢 | In-app onboarding, simple UI, role defaults |
| H-55 | Commercial viability (hardware cost per site) | 🟡 | Right-size the GPU by measured benchmark. Sub-stream inference. Subscription model covering hardware |

## 5. Top 10 by risk (impact × likelihood)

1. H-01 No incident data → 2. H-04 Handling ambiguity → 3. H-40 Legal variance → 4. H-50 Alert fatigue → 5. H-02 NC licensing → 6. H-07 Pixel size / camera placement → 7. H-44 Liability → 8. H-14 Weight provenance → 9. H-51 Over-reliance → 10. H-31 Single box failure

**The overall picture:** the infrastructure hurdles (§2) are almost all 🟢/🟡 and well understood. **The real difficulty is data, validation and law (§1, §3).** The plan therefore ships deterministic, verifiable features first and gates every learned feature behind shadow-mode evidence.
