# Feature Roadmap

**Date:** 2026-10-27 · Effort is in engineer-weeks (ew) for a team of ~4–6 (CV/ML ×2, backend ×1–2, frontend ×1, + part-time security/QA/PM). These are estimates [E].

| Phase | Name | Duration (calendar) | Effort | Gate to next phase |
|---|---|---|---|---|
| 0 | Research & feasibility | 4–6 wk | ~12 ew | Legal + licensing sign-off. Data-access agreements. Site survey shows the hazards are observable |
| 1 | Video infrastructure | 6–8 wk | ~20 ew | 72 h soak with 4 cams, 0 crashes, every fault visible in UI, benchmark report |
| 2 | Initial detection pipeline | 10–14 wk | ~40 ew | Offline benchmark goals met on validation set for D1–D3, A1 |
| 3 | Multi-camera intelligence | 6–8 wk | ~18 ew | Dedup accuracy ≥ 0.95 on multi-cam test set |
| 4 | Safety & reliability (shadow mode) | 8–12 wk | ~25 ew | Shadow metrics within goals. Pen test passed. Go/no-go signed |
| 5 | Controlled pilot | 12 wk | ~20 ew | Independent assessment. No stop conditions triggered |
| 6 | Production readiness | 8–12 wk | ~30 ew | SOC2-style controls, fleet ops, commercial licenses verified |

Total ≈ **13–18 months, ~165 ew** to a controlled multi-site deployment [E].

## Phase 0: Research and feasibility
- Deliverables: hardware inventory, licensing register signed by counsel, event taxonomy approved, camera feasibility survey at ≥1 partner facility, benchmark design, data-collection consent forms, manikin protocol, ACLab license enquiry.
- Risks: H-01, H-02, H-40.
- **Exit:** counsel opinion for the pilot jurisdiction. Written facility partnership. Manikin protocol approved by ethics/safety advisor.

## Phase 1: Video infrastructure
- RTSP ingest + reconnect, ring buffer, clip cutting, camera health states, file-replay RTSP simulator, Prometheus/Grafana, Postgres schema + migrations, minimal dashboard (health only).
- **Exit:** soak test, fault-injection suite for D1/D2 passes, GPU benchmark of candidate detector/pose models on target hardware (fills COST model variables).

## Phase 2: Initial detection pipeline
- Detector + ByteTrack + RTMPose fine-tuned. Zones. Rules D3, D4, A1, C1 (review). Incident inbox + review screen. Annotation tooling (CVAT, self-hosted). Offline benchmark harness.
- **Exit:** validation-set goals per SAFETY_VALIDATION §5.

## Phase 3: Multi-camera intelligence
- Room topology, zone projection, cross-camera correlation, dedup, synced multi-clip evidence.
- **Exit:** dedup ≥ 0.95. No mis-merge across rooms in tests.

## Phase 4: Safety & reliability
- Shadow mode at partner site, false-alert analysis, failure injection, security testing, reviewer workflows, go/no-go.
- Optional experiments: PoseC3D window classifier (E3), X3D verifier (E4), VLM summaries (E5).

## Phase 5: Controlled pilot
- Staff training, alert delivery (local siren + push), operational monitoring, incident feedback loop, independent assessment.

## Phase 6: Production readiness
- Deployment automation, A/B OTA updates + rollback, tenant isolation in the control plane, backup/recovery, audit/retention controls, support runbooks, commercial licensing verification for every shipped component.

## Release mapping
| Release | Contents |
|---|---|
| **MVP (Pilot)** | Health monitoring (D1/D2), restricted zone (D3), infant-off-surface (A1, after shadow), review workflows, RBAC, audit, retention |
| **R2** | Multi-camera correlation, infant hazard zone (D4), no-adult-present (D5) review, false-alert feedback, drift monitoring, on-prem air-gapped mode |
| **R3** | Facility reports, NL metadata search, multi-facility admin, roster integration, VLM summaries if E5 passes |
| **Research track** | Handling (B1–B3), posture anomalies (A3, C3), anomaly prioritization, respiration (E1) |
