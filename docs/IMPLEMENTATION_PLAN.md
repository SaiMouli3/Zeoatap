# Implementation Plan

**Date:** 2026-10-27 · Ordered tasks with dependencies, deliverables, acceptance criteria and effort (engineer-days, ed) [E].

## Repository structure (target)
```
apps/web/                     Next.js dashboard                (not yet created)
services/core/                Go: event engine, incidents, API (slice exists: eventengine + replay CLI)
services/vision-worker/       Python: ingest, inference         (not yet created)
packages/contracts/           Observation / incident schemas    (exists: JSON Schema)
infra/                        compose, systemd, migrations      (exists: initial SQL migration)
tests/fixtures/               synthetic observation streams     (exists: MOCK data)
docs/                         this documentation
```
Services are created only when their phase starts. No empty placeholders.

## Phase 0 (feasibility)
| # | Task | Depends | Deliverable | Acceptance | ed |
|---|---|---|---|---|---|
| 0.1 | Counsel engagement for pilot jurisdiction | — | Legal memo | Answers OQ-L2, L6, L7, L8 | 10 (external) |
| 0.2 | Licensing review of shipped components | 0.1 | Signed LICENSING_REGISTER | Every P0 component ✅ or replaced | 5 |
| 0.3 | ACLab + ICVD licensing enquiries | — | Responses | Decision recorded | 2 |
| 0.4 | Partner facility + site survey | — | Survey report, camera inventory | Hazard zones visible on ≥1 camera each | 5 |
| 0.5 | Event taxonomy sign-off | — | Approved definitions | Safety lead + domain expert sign | 3 |
| 0.6 | Manikin protocol + consent forms | 0.1 | Protocol doc | Ethics/safety advisor approval | 5 |
| 0.7 | Dev GPU procurement (approval) | — | Workstation | CUDA/TensorRT smoke test | 2 |

## Phase 1 (video infrastructure)
| # | Task | Depends | Deliverable | Acceptance | ed |
|---|---|---|---|---|---|
| 1.1 | Observation contract v1 (protobuf + JSON Schema) | — | `packages/contracts` | Contract tests pass | 2 (**started**) |
| 1.2 | RTSP simulator from files (MediaMTX/GStreamer) | — | `infra/sim` | Streams 16 files in loop | 2 |
| 1.3 | vision-worker ingest + reconnect + health | 1.1, 1.2 | Python service | Fault tests for disconnect/freeze pass | 8 |
| 1.4 | Ring buffer + clip cutting (remux, no transcode) | 1.3 | Clip API | Clip hash stable. Pre-event 30 s available | 5 |
| 1.5 | core: health aggregation + monitoring states | 1.1 | Go package | Unit tests: green only when fresh | 4 |
| 1.6 | Postgres schema + migrations | — | `infra/migrations` | Migrate up/down in CI | 2 (**started**) |
| 1.7 | Prometheus metrics + Grafana dashboards | 1.3, 1.5 | Dashboards | All metrics in OBSERVABILITY list present | 3 |
| 1.8 | Minimal web: login + health page | 1.5 | `apps/web` | US-01, US-10 pass | 8 |
| 1.9 | GPU benchmark harness | 0.7 | Benchmark report | t_det, t_pose measured for 3 candidates | 5 |
| 1.10 | 72 h soak | all | Soak report | 0 crashes, faults visible | 3 |

## Phase 2 (detection pipeline)
| # | Task | Depends | Acceptance | ed |
|---|---|---|---|---|
| 2.1 | CVAT self-hosted + annotation schema | 0.6 | Schema per SAFETY_VALIDATION §3 | 3 |
| 2.2 | Data collection (manikin + consented) | 0.6, 2.1 | ≥5k labelled frames, session-split | 20 |
| 2.3 | Detector fine-tune (infant class, IR) + TRT export | 2.2, 1.9 | Val AP reported per stratum | 10 |
| 2.4 | ByteTrack integration + `held` state | 2.3 | ID-switch rate measured | 5 |
| 2.5 | Pose fine-tune + pose-quality score | 2.2 | PCK per stratum. Quality calibrated | 10 |
| 2.6 | Zone editor (web) + zone config in core | 1.8 | US-06 passes | 6 |
| 2.7 | Rules D3, D4, A1, C1 in event engine | 1.5 | Unit + replay tests | 6 (**slice started**) |
| 2.8 | Incident inbox + review screen + dispositions | 2.7 | US-02..04 | 10 |
| 2.9 | Offline benchmark harness (event-level metrics) | 2.7 | Report with CIs | 5 |

## Phases 3–6
See FEATURE_ROADMAP. Tasks are broken down at each phase start.

## Next ten tasks (priority order)
1. Engage counsel for the pilot jurisdiction (0.1)
2. Recruit partner facility + site survey (0.4)
3. Send ACLab/ICVD licensing enquiries (0.3)
4. Procure a dev GPU workstation (0.7)
5. Finalize the Observation contract as protobuf (1.1)
6. Build the RTSP file simulator (1.2)
7. vision-worker ingest + health (1.3)
8. Ring buffer + clip cutting (1.4)
9. Integrate the existing Go event-engine health states with real observations (1.5)
10. GPU benchmark harness for YOLOX/RT-DETR + RTMPose/RTMO (1.9)
