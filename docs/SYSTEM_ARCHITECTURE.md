# System Architecture

**Version:** 0.1 (design, not yet implemented beyond the event-engine slice) · **Date:** 2026-10-27
Related: [ADRs](architecture/adr/) · [Diagrams](architecture/diagrams/) · [AI_MODEL_STRATEGY](AI_MODEL_STRATEGY.md) · [COST_AND_DEPLOYMENT](COST_AND_DEPLOYMENT.md)

---

## 1. Executive summary

**Recommended default: Edge-first hybrid** ([ADR-001](architecture/adr/ADR-001-edge-first-hybrid.md)).

- **All video decoding, inference, event decisions, alerting, evidence storage and the staff dashboard run on one GPU edge appliance inside the facility.** Essential detection and on-site alerting keep working with no internet connection.
- **The cloud is a control plane only.** It handles fleet registry, signed config and model distribution, metadata-only telemetry, remote admin access and backups of *metadata*. **Video and clips do not leave the facility by default.**
- **Three processes on the appliance, not a dozen microservices** ([ADR-006](architecture/adr/ADR-006-service-boundaries.md)):
  1. `vision-worker` (Python): RTSP ingest → decode → detect → track → pose → **Observations**
  2. `core` (Go): event engine, incidents, alerts, API, health, audit and retention
  3. `web` (Next.js): dashboard
  + PostgreSQL. Redis, Kubernetes and a message broker are **not** in the MVP.
- **Models produce observations. Rules produce decisions. The alerter delivers** ([ADR-002](architecture/adr/ADR-002-observation-decision-separation.md)).

---

## 2. Deployment model comparison

| Criterion | A. Fully local GPU | B. Cloud GPU | C. Hybrid edge + cloud (**recommended**) |
|---|---|---|---|
| Alert latency | Lowest (LAN only) | WAN RTT + upload + queueing. Worse on congested uplinks | Same as A for detection. Cloud only for remote notifications |
| Works offline | ✅ | ❌ **Fails the core requirement (#9)** | ✅ Essential detection and local alerts |
| Privacy | ✅ Video never leaves the site | ❌ Continuous child video in third-party cloud | ✅ Video local by default. Opt-in clip escrow |
| Uplink bandwidth | ~0 | 16 cams × 2–4 Mbps = **32–64 Mbps sustained upstream** [E]. Many facilities lack this | Metadata only (KB/s) |
| GPU utilization | Dedicated, often idle at night | Shared, so higher utilization | Dedicated. Night-time capacity can be used for batch re-analysis |
| OpEx | Low (power + support) | High (GPU-hours + egress) | Low + small cloud control plane |
| CapEx | Appliance per site | ~0 | Appliance per site |
| Maintenance | Hard without remote management | Easy | Remote management via the control plane solves A's weakness |
| Fleet scalability | Poor (manual) | Good | Good |
| **Verdict** | Good for one site, but unmanageable at fleet scale | **Rejected as default** (offline, privacy, uplink) | **Default** |

B remains an option for **non-real-time batch work** (model training on consented data, offline re-evaluation) in an isolated research tenant.

---

## 3. High-level architecture

```mermaid
flowchart LR
  subgraph Facility["Facility LAN (trusted zone)"]
    CAM[IP cameras] -->|RTSP over isolated camera VLAN| NVR[(Existing NVR - optional)]
    CAM -->|RTSP sub/main stream| VW
    NVR -.->|RTSP re-stream if cameras unreachable| VW
    subgraph EDGE["Edge appliance (GPU)"]
      VW["vision-worker (Python)<br/>ingest · decode · detect · track · pose"]
      RB[("RAM ring buffer<br/>raw encoded segments")]
      CORE["core (Go)<br/>event engine · incidents · alerts<br/>API · health · audit · retention"]
      PG[(PostgreSQL)]
      EV[("Encrypted evidence store<br/>(local disk)")]
      WEB["web (Next.js dashboard)"]
      VLM["vlm-worker (optional, P2)<br/>event-triggered summaries"]
    end
    VW --> RB
    VW -->|Observations gRPC stream| CORE
    CORE -->|clip request| VW
    VW -->|remuxed clip| EV
    CORE --> PG
    CORE --> EV
    WEB --> CORE
    CORE -.->|evidence id only| VLM
    VLM -.->|structured summary| CORE
    STAFF[Staff tablets / workstations] -->|HTTPS + WebSocket| WEB
    CORE --> SIREN[Local alert device / speaker]
  end
  subgraph Cloud["Cloud control plane (no video by default)"]
    FLEET[Fleet registry · config · signed model releases]
    TELE[Metadata telemetry · uptime · alert-delivery stats]
    PUSH[Notification relay: push / SMS]
    RA[Remote admin portal]
  end
  CORE <-->|outbound-only mTLS| FLEET
  CORE -->|metrics, incident metadata| TELE
  CORE -->|alert w/o media| PUSH
  RA <-->|via outbound tunnel| CORE
```

| Component | Why it exists |
|---|---|
| Camera VLAN | Cameras are notoriously insecure, so isolate them. The edge box is the only host allowed to reach them. |
| NVR (optional) | Many sites already record on an NVR. We read RTSP from cameras directly where possible (lower latency, less NVR load). We fall back to NVR re-streams. **We do not replace the NVR's legal recording role.** |
| vision-worker | GPU-bound work. Python because the model ecosystem lives there. One process with one pipeline per camera. |
| Ring buffer | Holds the last N seconds of *original encoded* packets per camera, so pre-event clips need no re-encode (no NVENC needed, bit-exact evidence). |
| core | Deterministic, testable decision logic and everything stateful. Go for static binaries, low memory, strong concurrency and easy cross-compile (x86/ARM). |
| PostgreSQL | Durable incidents, audit log, config, users. One database avoids split-brain state. |
| Evidence store | Local encrypted filesystem with content-addressed files, served only through `core` with short-lived authorized tokens. Avoids a separate object-store service on the edge (MinIO's licensing and distribution changes make it a poor default). |
| web | Staff UI, served locally so it works offline. |
| vlm-worker | Optional. Off the critical path. Only reads evidence IDs it is handed. |
| Cloud control plane | Fleet operations without video. The edge **initiates** all connections (no inbound ports). |

---

## 4. End-to-end video processing pipeline

```mermaid
flowchart LR
  A[RTSP client<br/>TCP interleaved, auth from secret store] --> B[Depacketize H.264/H.265]
  B --> RB[(Ring buffer of encoded packets)]
  B --> C[HW decode NVDEC]
  C --> D[Frame sampler<br/>adaptive per camera: base fps, burst fps]
  D --> Q{{"Bounded queue (drop-oldest, counted)"}}
  Q --> E[Quality checks<br/>black / frozen / blur / IR mode / obstruction]
  Q --> F[Detector: adult, infant, crib/surface]
  F --> G[ByteTrack per camera]
  G --> H[Pose on tracked crops<br/>+ pose quality score]
  H --> I[Feature extraction<br/>zone membership, height-above-floor proxy,<br/>velocity, posture state]
  E --> O[Observation builder]
  I --> O
  O -->|"Observation{cam, ts, tracks, quality, model_versions}"| CORE[core / event engine]
```

Key design choices:
- **Clock:** each observation carries `capture_ts` derived from RTCP sender reports (NTP-mapped) when available, otherwise the edge receive time with a flag `ts_source=receive`. The edge runs `chrony`, and cameras use the edge as their NTP server (§7).
- **Adaptive sampling:** base 5 fps on all cameras. **≥15 fps on cameras covering raised surfaces** (falls last ~0.35–0.45 s; see AI_MODEL_STRATEGY §5). Burst to the max decoded fps when a candidate starts.
- **Backpressure:** each camera has a bounded queue. On overflow the oldest frame is dropped and `frames_dropped_total{camera}` increments. **If the drop rate exceeds its threshold, the camera's monitoring state is downgraded to DEGRADED and the dashboard says so.**

---

## 5. Incident detection and decision flow

```mermaid
flowchart TD
  O[Observation stream] --> V{Camera analyzable?<br/>health=OK and quality>=q_min}
  V -- no --> H[Emit MonitoringState change<br/>UNAVAILABLE / DEGRADED] --> UI[Dashboard banner + ops alert]
  V -- yes --> R[Per-event-type rule evaluators<br/>each with own thresholds]
  R --> C{Candidate condition met?}
  C -- no --> O
  C -- yes --> W[Open candidate window<br/>request burst fps, mark ring-buffer]
  W --> T{Temporal persistence +<br/>consistency checks pass?}
  T -- inconclusive/visibility lost --> INC[Record INCONCLUSIVE review item]
  T -- no --> X[Discard, log for FP analysis]
  T -- yes --> D{Duplicate of open incident?<br/>same room, overlapping time, same event family}
  D -- yes --> M[Merge evidence into existing incident]
  D -- no --> N[Create incident<br/>idempotency key = hash cam, type, window start bucket]
  N --> P{Severity policy for this event type}
  P -- review --> RQ[Low-priority review queue]
  P -- alert --> AL[Alert dispatcher: local siren/dashboard first,<br/>then push]
  AL --> ACK{Acknowledged within T_ack?}
  ACK -- no --> ESC[Escalate to next role / fallback channel]
  ACK -- yes --> REV[Designated reviewer disposition]
  N --> CLIP[Request pre/post clip from ring buffer]
```

The full state machine and pseudocode are in [AI_MODEL_STRATEGY §7](AI_MODEL_STRATEGY.md#7-temporal-decision-engine).

---

## 6. Multi-camera coordination

```mermaid
flowchart LR
  subgraph Room1["Room 'Infants A' (topology config)"]
    C1[Cam 1: crib row] --- C2[Cam 2: floor area]
    C2 --- C3[Cam 3: door]
  end
  C1 --> T1[Tracks cam1 - local ids]
  C2 --> T2[Tracks cam2]
  C3 --> T3[Tracks cam3]
  T1 & T2 & T3 --> Z[Zone projection<br/>each camera's zones mapped to room-level zones<br/>e.g. crib-3, floor, exit]
  Z --> CORR[Room-level correlator<br/>events keyed by room + zone + time]
  CORR --> DEDUP[Dedup: same family, overlapping window,<br/>same room-zone ⇒ one incident, many evidence clips]
  DEDUP --> INC[(Incident with evidence from cam1 + cam2)]
```

- **Tracking IDs are per camera and short-lived.** Random IDs reset each session. No face recognition ([ADR-003](architecture/adr/ADR-003-no-biometric-identity.md)).
- **Cross-camera association is by room-level zone and time, not by identity.** Installers draw zones per camera at calibration. Overlapping views map to the same room zone (`crib-3`). Two cameras both seeing "infant leaves crib-3 surface" within Δt=3 s become **one incident** with two evidence clips.
- **Appearance re-identification is out of scope** by default. If it is ever needed (e.g. "which infant was in which crib"), the preferred non-biometric solution is **crib/position assignment** in the roster ("crib 3 = child #A12" entered by staff), not vision-based identification. An opt-in ReID design would require a DPIA and legal review (PRIVACY doc §9).
- **Clock sync:** all cameras sync to the edge's NTP. The correlator tolerates ±500 ms skew [E]. If measured skew exceeds 1 s, the camera is flagged `CLOCK_DRIFT` and cross-camera merge for it is disabled, never silently mis-merged.

---

## 7. Video ingestion and robustness

| Concern | Design |
|---|---|
| RTSP auth & secrets | Credentials live in the edge secret store (OS keyring or age-encrypted file, TPM-sealed where available). They are never in config files, logs or the DB in plaintext. The UI shows `rtsp://user:****@host`. |
| Reconnect | Exponential backoff 1 s → 30 s with jitter, unbounded retries, and a state machine `CONNECTING → STREAMING → STALLED → RECONNECTING`. A stall means no packet for 3 s [E]. |
| NVR compatibility | ONVIF Profile S discovery (optional) plus manual RTSP URL. Test matrix: Hikvision, Dahua, Axis, Uniview, Reolink (OQ-H2). Prefer camera **sub-stream** (e.g. 640–1280 px) for inference and the main stream for evidence if bandwidth allows. |
| H.264 vs H.265 | Both are supported via NVDEC (verify per GPU generation). Prefer H.264 for widest compatibility and fewer browser-playback problems in clip review. H.265 clips get server-side HLS/fMP4 transmux or a fallback transcode for browsers lacking HEVC. |
| Variable frame rate / drops | Use PTS, never frame counters. Sampler targets wall-clock rate. Gaps over 1 s are recorded as gaps in observations (`gap=true`). |
| Clock drift | RTCP SR → NTP mapping. Compare against the edge clock and alarm when |skew| > 1 s. |
| Network congestion | TCP-interleaved RTSP (no UDP loss). Monitor receive bitrate vs. expected. |
| Corrupt video | Decoder errors are counted. More than N per minute leads to DEGRADED. Keyframe resync. |
| Night / IR | The quality module detects grayscale/IR mode, and models are evaluated separately on IR (test-set stratum). Different thresholds are possible per mode. |
| Motion blur / poor light | Pose-quality score (mean keypoint confidence, visible keypoint count) gates pose-dependent rules. Low quality → *inconclusive*, never "all clear". |
| Obstruction / view change | Reference-frame comparison (SSIM / feature-match vs. calibration snapshot) every 60 s gives `VIEW_CHANGED` or `OBSTRUCTED`. |
| GPU overload | Per-camera queues + global inference-budget scheduler. Overload lowers base fps on low-risk cameras first and **never** on raised-surface cameras. If minimums cannot be met, the camera goes DEGRADED and the dashboard warns. |
| Graceful shutdown | SIGTERM → stop intake → flush observations → finish in-flight clip writes (≤10 s) → exit. systemd restart policy `on-failure`. |
| Health | `/healthz` (process up), `/readyz` (all critical deps OK), per-camera health exported to Prometheus. |

---

## 8. Data storage and retention lifecycle

```mermaid
flowchart LR
  F[Decoded frames] -->|RAM only, ms lifetime| X1((discarded))
  RB[Ring buffer: encoded packets] -->|RAM, 60–120 s rolling| X2((overwritten))
  RB -->|on incident| CLIP[Evidence clip<br/>encrypted, local]
  O[Observations] -->|RAM; aggregates only| AGG[Per-minute health/activity aggregates]
  AGG --> PG[(PostgreSQL)]
  INC[Incident record] --> PG
  CLIP -->|retention_deadline default 30 days, policy| DEL[Crypto-shred + delete]
  CLIP -->|legal hold set by authorized role| HOLD[Held until released]
  CLIP -.->|explicit research consent only| RES[(Research dataset, separate tenant)]
  PG -->|audit log append-only, 1–7 yrs per policy| AUD[(Audit)]
```

| Data | Stored? | Where | Default retention [E, policy-configurable] |
|---|---|---|---|
| Live decoded frames | **No** (memory only) | GPU/RAM | Milliseconds |
| Encoded ring buffer | Memory only | RAM | 60–120 s rolling |
| Continuous recording | **Not by us**. The facility NVR keeps doing this under its own policy | NVR | Facility's policy |
| Pose keypoints / tracks | Memory only. Stored *only* inside incident evidence windows | RAM / incident | With incident |
| Incident metadata | Yes | Postgres | 1 year or per policy |
| Incident clips | Yes, encrypted | Local disk | **30 days** unless held |
| Audit log | Yes, append-only and hash-chained | Postgres | Per legal advice |
| Face embeddings / biometric templates | **Never collected** | — | — |
| Training data | **Not by default**. Separate explicit consent | Separate research store | Per consent |

---

## 9. Network and security boundaries

```mermaid
flowchart TB
  subgraph Z0["Zone 0: Camera VLAN (untrusted devices)"]
    CAM[Cameras]
  end
  subgraph Z1["Zone 1: Edge appliance"]
    VW[vision-worker]:::svc
    CORE[core]:::svc
    PG[(Postgres - localhost only)]
    EV[(Evidence - encrypted)]
  end
  subgraph Z2["Zone 2: Staff LAN / Wi-Fi"]
    TAB[Staff devices]
  end
  subgraph Z3["Zone 3: Internet"]
    CP[Cloud control plane]
    PUSH[Push/SMS providers]
  end
  CAM -- "RTSP 554 only; edge→camera" --> VW
  TAB -- "HTTPS 443 only; SSO/MFA" --> CORE
  CORE -- "outbound mTLS 443 only; allow-listed hosts" --> CP
  CORE -- "outbound 443; payload without media" --> PUSH
  CAM -. "blocked: camera→internet" .-x Z3
  TAB -. "blocked: staff→camera VLAN" .-x CAM
  classDef svc fill:#eef,stroke:#446;
```

- Cameras cannot reach the internet (prevents vendor cloud phoning home and botnets).
- Staff devices cannot reach cameras directly. All video goes through `core` with authorization and access logging.
- The edge accepts **no inbound connections from the internet**. Remote access goes through an outbound tunnel initiated by the edge.
- Postgres listens on localhost only. The evidence disk uses LUKS full-disk encryption, plus per-clip AES-GCM keys stored wrapped in the DB to allow crypto-shredding.

---

## 10. Failure recovery and degraded operation

```mermaid
stateDiagram-v2
  [*] --> FULLY_OPERATIONAL
  FULLY_OPERATIONAL --> CAMERA_UNAVAILABLE: no packets > 3s
  FULLY_OPERATIONAL --> VIDEO_DEGRADED: drops>thr / blur / IR-unsupported / obstructed
  FULLY_OPERATIONAL --> AI_UNAVAILABLE: vision-worker down / GPU error / model load fail
  FULLY_OPERATIONAL --> ALERTS_DELAYED: notifier failures / queue age > 10s
  FULLY_OPERATIONAL --> EVIDENCE_UNAVAILABLE: disk full / write errors
  CAMERA_UNAVAILABLE --> FULLY_OPERATIONAL: stream restored + 10s stable
  VIDEO_DEGRADED --> FULLY_OPERATIONAL: quality ok for 60s
  AI_UNAVAILABLE --> FULLY_OPERATIONAL: worker ready + self-test passed
  ALERTS_DELAYED --> FULLY_OPERATIONAL: delivery ok
  EVIDENCE_UNAVAILABLE --> FULLY_OPERATIONAL: space reclaimed + write test ok
```

Each camera has its own state, and the facility state is the **worst** state of any camera (shown per room). The dashboard **never shows green unless the state is FULLY_OPERATIONAL *and* the last observation is younger than 5 s.** A heartbeat-age check prevents a frozen pipeline from looking healthy ("dead-man's switch").

| Failure | Detection | Automatic response | What staff see |
|---|---|---|---|
| Camera disconnect | No packets 3 s | Reconnect w/ backoff | Room tile red "Camera offline — not monitored", plus ops alert after 60 s |
| Frozen video | Identical frame hashes / PTS stall | Reconnect | "Video frozen — not monitored" |
| Black frames | Mean luminance < thr | — | "No image — check lighting/IR" |
| GPU failure / Xid | NVML errors, CUDA exceptions | Restart worker (max 3 per 10 min), then halt and alert | Facility banner "AI analysis unavailable" |
| Model crash / OOM | Process exit, cgroup OOM | systemd restart. Lower batch size | Same |
| DB unavailable | core health check | core buffers incidents in a local append-only WAL file (bounded 100 MB) and replays them | "Incident history delayed" |
| Internet outage | Control-plane heartbeat | Local alerting continues. Push queue retained | Badge "Remote notifications offline" |
| Notification provider down | Delivery receipts | Retry with backoff, then fallback channel (SMS ↔ push), then local siren | "Alert delivery failing" ops alert |
| Disk exhaustion | <10% free | Purge expired clips first, then refuse new non-critical writes | "Evidence storage low" |
| Clock sync failure | chrony offset >1 s | Disable cross-camera merge | "Time sync problem" |
| Queue overflow | Drop counters | Reduce fps on low-risk cams | "Analysis degraded" |
| Stale/incorrect model | Model hash ≠ release manifest | Refuse to start | "Model verification failed" |
| Bad config | Schema validation on load | Keep last-known-good config | Admin error |

---

## 11. Deployment topologies

```mermaid
flowchart TB
  subgraph Local["Local-only (air-gapped option)"]
    L1[Edge appliance] --- L2[Staff LAN]
  end
  subgraph Hybrid["Hybrid (default)"]
    H1[Edge appliance per facility] -->|outbound mTLS| H2[Cloud control plane<br/>multi-tenant]
    H2 --> H3[Fleet ops console]
  end
  subgraph CloudBatch["Cloud batch (research tenant, opt-in)"]
    B1[Consented clip upload] --> B2[Isolated training/eval cluster]
    B2 -->|signed model release| H2
  end
```

---

## 12. Service boundaries

| Process | Language | Contains | Why separate |
|---|---|---|---|
| `vision-worker` | Python 3.11/3.12 | RTSP ingest, decode, sampling, quality, detection, tracking, pose, feature extraction, ring buffer, clip cutting | GPU memory and crash isolation. Python ML ecosystem. Restartable without losing incidents |
| `core` | Go | Event engine, incident store, alert dispatcher, REST/WebSocket API, auth/RBAC, health aggregation, retention jobs, audit, control-plane sync | Deterministic logic. Must stay alive when ML crashes. One binary, easy to test |
| `web` | Next.js/TypeScript | Dashboard | Different deploy cadence. Could be static-exported and served by core later |
| `postgres` | — | Durable state | Standard |
| `vlm-worker` (P2) | Python + vLLM | Clip summarization | Large GPU memory and slow, so it must not share fate with vision-worker |

**Inter-process interface:** gRPC over a UNIX domain socket (`Observations` stream vision→core, `ClipRequest` core→vision). Contracts are in [`packages/contracts`](../packages/contracts/). **A broker (NATS JetStream) is introduced only** when there are ≥2 independent observation consumers or multiple GPU nodes per site ([ADR-006](architecture/adr/ADR-006-service-boundaries.md)).

**Why not Redis?** No requirement needs it. Ephemeral state lives in-process (core) or in Postgres. Redis would add another stateful service to secure and monitor.
**Why not Kubernetes?** One appliance per site. Docker Compose + systemd is enough. Kubernetes is reconsidered only for the cloud control plane once there are many tenants.

---

## 13. Model-serving design

- Models are exported to ONNX and compiled to TensorRT engines **on the target GPU at install time**, because engines are GPU- and TRT-version-specific. Engine hashes are recorded.
- A model-release manifest (signed, see PRIVACY doc §7) lists model ID, version, SHA-256, training-data snapshot ID, validation report ID and allowed event types.
- The vision-worker includes model versions in every observation, and the core stamps them into every incident.
- **CPU fallback:** ONNX Runtime CPU at reduced fps covers only *camera health + zone presence*. The dashboard marks the reduced capability explicitly ("Fall detection unavailable — CPU fallback").
- **DeepStream decision:** [ADR-005](architecture/adr/ADR-005-video-pipeline.md). Start with GStreamer/PyAV + NVDEC + TensorRT (simpler, portable, easier to debug). Benchmark DeepStream 8/9 in Phase 1. Adopt it if it delivers ≥1.5× camera density at equal accuracy and its license terms are acceptable.

---

## 14. Scaling strategy

| Stage | Cameras | Topology | Change from previous stage |
|---|---|---|---|
| Lab | 1 (file replay) | Laptop/workstation GPU | — |
| Pilot | 1–4 | 1 edge box, 1 GPU | Real RTSP, real staff UI |
| Facility | 8–16 | 1 edge box, 1 GPU (size by benchmark) | Room topology, multi-camera dedup |
| Large facility | 32–64 | 2–4 edge nodes, or 1 node with 2 GPUs | Introduce NATS for observation fan-in. Shard cameras by room to keep room correlation on one node |
| Multi-facility | N sites | Edge per site + multi-tenant control plane | Tenant isolation, fleet upgrades, remote ops |

Capacity is calculated in [COST_AND_DEPLOYMENT.md](COST_AND_DEPLOYMENT.md) from measured per-model latencies ([BENCHMARK_RESULTS](BENCHMARK_RESULTS.md)). The inference-only benchmark gives ≈46 cameras per GPU (YOLOX-M 640 + RTMPose-m, TensorRT FP16, RTX PRO 4500 Blackwell). **That is an upper bound, not a sizing claim:** decode, CPU and end-to-end limits come from the soak test (1.10) on the chosen edge SKU.

---

## 15. Architecture decision records

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](architecture/adr/ADR-001-edge-first-hybrid.md) | Edge-first hybrid deployment | Accepted (provisional) |
| [ADR-002](architecture/adr/ADR-002-observation-decision-separation.md) | Detectors emit observations; the rules engine decides; the alerter delivers | Accepted |
| [ADR-003](architecture/adr/ADR-003-no-biometric-identity.md) | No face recognition / biometric ReID by default | Accepted |
| [ADR-004](architecture/adr/ADR-004-licensing-safe-model-stack.md) | Only Apache/MIT/BSD-compatible code and rights-cleared weights in product | Accepted |
| [ADR-005](architecture/adr/ADR-005-video-pipeline.md) | GStreamer/PyAV + TensorRT first; DeepStream after benchmark | Proposed |
| [ADR-006](architecture/adr/ADR-006-service-boundaries.md) | 3 processes + Postgres; no Redis/K8s/broker in MVP | Accepted (provisional) |
| [ADR-007](architecture/adr/ADR-007-vlm-off-critical-path.md) | VLM is local, event-triggered and advisory only | Accepted |
| [ADR-008](architecture/adr/ADR-008-event-level-validation-gates.md) | Event-level metrics + shadow-mode gates before any alert is enabled | Accepted |
