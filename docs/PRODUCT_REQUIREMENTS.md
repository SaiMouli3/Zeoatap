# Product Requirements

**Date:** 2026-10-27 · **Product positioning:** a *safety-support and review tool* for licensed infant-care facilities. It is **not** a substitute for supervision, **not** a medical device and **not** an abuse-adjudication system.

---

## 1. Personas

| Persona | Context | Needs | Fears |
|---|---|---|---|
| **Caregiver** (room staff) | Hands busy, tablet on wall | Few, clear, actionable prompts. One-tap acknowledge | Being blamed by an algorithm, alarm noise |
| **Room lead** | Supervises 2–4 staff | See room status, respond to alerts | Missing something while busy |
| **Safeguarding reviewer** | Designated lead / director | Evidence review, disposition, escalation records | Losing evidence, false accusations |
| **Facility admin** | Operations | Camera setup, users, retention, health | Outages nobody notices, compliance |
| **Parent** (indirect) | Trusts the facility | Assurance, privacy | Their child's video leaking |
| **Vendor ops** | Fleet support | Health, upgrades without seeing video | Silent failures across fleet |

## 2. Core use cases
1. Staff are told within seconds when monitoring is **not** working in a room.
2. Staff get a prompt when an infant appears to have left a raised surface unexpectedly (once validated).
3. Staff get a prompt when an adult enters a configured restricted zone.
4. A reviewer examines the incident clip, records a disposition and escalates per facility policy.
5. An admin registers an authorized camera, draws zones and sets retention.
6. Vendor ops sees fleet health and rolls back a bad model, without video access.

## 3. User stories (MVP, with acceptance criteria)

| ID | Story | Acceptance criteria |
|---|---|---|
| US-01 | As a caregiver, I see at a glance whether my room is being monitored | Tile shows one of 7 states (SYSTEM_ARCHITECTURE §10) with icon + text + colour. Green only when FULLY_OPERATIONAL and heartbeat < 5 s. Verified by failure-injection tests |
| US-02 | As a caregiver, I can acknowledge an alert in one tap | Ack recorded with user, time and device. Alert stops repeating. p95 UI response < 300 ms |
| US-03 | As a reviewer, I review an incident clip securely | Clip plays only after authentication + role check. Access logged. URL expires ≤ 5 min |
| US-04 | As a reviewer, I record a disposition | Dispositions: `no_concern`, `routine_care`, `false_detection`, `concern_followed_up`, `escalated_external`, `inconclusive`. Free-text note. Immutable once submitted (amendments are appended) |
| US-05 | As an admin, I register a camera only with an authorization record | Form requires authorizer name, date, scope and room. Camera cannot be enabled without it |
| US-06 | As an admin, I draw zones (surfaces, restricted, sleep) per camera | Polygon editor on a calibration snapshot. Saved versioned. Rules reference zone IDs |
| US-07 | As an admin, I set clip retention | 7–90 days. The deletion job runs nightly and produces a report |
| US-08 | As an admin, I see model and service health | Model versions, GPU temperature/utilization, fps per camera, drop rate, last observation age |
| US-09 | As ops, alert delivery failures are visible | Failed deliveries show in the ops panel within 60 s and trigger the fallback channel |
| US-10 | As anyone, I see a clear warning when monitoring is unavailable | Full-width banner listing affected rooms. Cannot be dismissed while the condition persists |

## 4. Functional requirements (MVP)
- FR-1 Secure login (OIDC SSO or local accounts with MFA), RBAC per PRIVACY §4
- FR-2 Facility → rooms → cameras hierarchy. Room topology and zones
- FR-3 Camera registration with authorization record, RTSP test, health checks
- FR-4 Camera health dashboard + per-room monitoring state
- FR-5 Near-live view (if the facility enables it, role-restricted, low-res sub-stream)
- FR-6 Incident inbox (filters: room, type, severity, status, time)
- FR-7 Alert acknowledgement and escalation chain
- FR-8 Restricted incident-clip review with timeline of observations
- FR-9 Audit history (who saw/did what)
- FR-10 Retention settings + legal holds
- FR-11 System/model health
- FR-12 "Shadow mode" switch per rule (incidents created but hidden from staff)

## 5. Non-functional requirements

| Category | Requirement (goal) |
|---|---|
| Latency | Camera-health alert ≤ 10 s. Validated safety alert p95 ≤ 5 s from event start to local display |
| Availability | Edge processing uptime ≥ 99.5% of operating hours (goal). Every outage must be *visible* |
| Offline | All local detection, alerting and review work with no internet |
| Security | PRIVACY §7 checklist complete before pilot |
| Privacy | No video egress by default. No biometrics |
| Accessibility | WCAG 2.2 AA. Alerts never signalled by colour alone |
| Scalability | 1 → 16 cameras per appliance without architecture change. 64 via multi-node |
| Maintainability | Signed OTA updates, rollback ≤ 5 min |
| Observability | Prometheus metrics, structured JSON logs, trace IDs per incident |

---

## 6. Feature matrix

**Legend:** Complexity S/M/L/XL; Release MVP / R2 / R3 / Later / **Not recommended**

### 6.1 Essential MVP
| Feature | User | Problem solved | Complexity | Dependencies | Risks | Release |
|---|---|---|---|---|---|---|
| Secure login + RBAC | All | Unauthorized access to child video | M | Auth library | Misconfigured roles | MVP |
| Facility/room/camera management | Admin | Setup | M | DB | — | MVP |
| Authorized camera registration | Admin | Legal authorization trail | S | — | Admin skips paperwork, so it is enforced in the form | MVP |
| Camera health dashboard | Staff/admin | Silent outages | M | Ingest health | False "healthy" | MVP |
| Near-live view | Lead/reviewer | Situational awareness | M | Transmux | Privacy creep, so role-restricted | MVP (optional) |
| Incident timeline | Reviewer | History | M | Event engine | — | MVP |
| Alert acknowledgement | Staff | Closing the loop | M | Notifier | Ack without looking, so record time-to-ack | MVP |
| Restricted clip review | Reviewer | Evidence | M | Evidence store | Leaks | MVP |
| Staff roles and permissions | Admin | Least privilege | S | Auth | — | MVP |
| Audit history | Admin/reviewer | Accountability | M | Hash-chain | — | MVP |
| Retention settings | Admin | Compliance | S | Jobs | Accidental deletion, so holds | MVP |
| Model & service health | Admin/ops | Trust | S | Metrics | — | MVP |
| Monitoring-unavailable warnings | All | Over-reliance | S | Health | — | MVP |

### 6.2 Advanced safety intelligence
| Feature | Complexity | Dependencies | Risks | Release |
|---|---|---|---|---|
| Multi-camera incident correlation | M | Topology, clock sync | Mis-merge | R2 |
| Temporal event analysis (window classifier) | L | Labelled data | Over-claiming | R2 (shadow first) |
| Configurable review policies | M | Rules engine | Misconfiguration | R2 |
| Severity + uncertainty display | S | Engine | Misreading | MVP/R2 |
| False-alert feedback | S | Dispositions | Feedback used for training without consent, so it is gated | R2 |
| Camera blind-spot reporting | M | Calibration | — | R2 |
| Model drift monitoring | M | Telemetry | — | R2 |
| Per-camera calibration | M | Zone editor | — | MVP (zones) / R2 |
| Evidence-quality indicators | S | Quality module | — | R2 |
| Staff response-time analytics | M | Ack data | **Can become staff surveillance.** Aggregate per room, never ranked per person | R3 (careful) |
| Facility operational reports | M | — | — | R3 |

### 6.3 Optional future (assessed)
| Feature | Verdict | Reason |
|---|---|---|
| Natural-language search over metadata | R3 | Useful for reviewers. Metadata only, no video embeddings |
| Daily safety summaries | R3 | Health + incident counts. **No** narrative about children |
| Search by event category | R2 | Simple filter |
| Automated report generation | R3 | Template from dispositions. Human-signed |
| Anomaly-based review prioritization | Later | Needs consented facility-normal data. Never an alert |
| Privacy-preserving analytics | Later | Aggregates with k-anonymity thresholds |
| On-prem only (air-gapped) | R2 | Already supported by the edge design |
| Multi-facility admin | R3 | Control plane |
| Parent-facing reports | **Later, with caution** | Restricted info only ("room monitored 99.2% of hours"). **Never clips or incident details** without facility process |
| Visual respiration | **Not recommended** for product | Research only. Regulatory exposure |
| Facility-system integration (rosters) | R3 | Crib assignment helps context |
| **Facial recognition** | **Not recommended** | Privacy/biometric law, unnecessary |
| **Emotion detection** | **Not recommended** | Unreliable. Prohibited for workplaces under EU AI Act |
| **Caregiver performance scoring** | **Not recommended** | Surveillance harm, labour law |
| **Automated abuse accusations** | **Never** | Unsupported inference, legal harm |

---

## 7. Dashboard design

### 7.1 Navigation
```
Overview (facility status) ── Rooms ── Cameras
Incidents (inbox) ── Incident detail
Health (cameras · AI · alert delivery · storage)
Settings: Facility · Rooms & zones · Cameras · Rules & policies · Retention · Users & roles
Audit
```

### 7.2 Main dashboard wireframe
```
┌──────────────────────────────────────────────────────────────────────────┐
│ ▲ MONITORING UNAVAILABLE: Room "Toddlers B" — camera offline (2 min)      │  ← full-width, non-dismissable
├──────────────────────────────────────────────────────────────────────────┤
│ Sunflower Nursery   ● 5/6 rooms fully monitored     Alerts open: 1   👤 J │
├───────────────┬──────────────────────────────────────────────────────────┤
│ Rooms         │  ┌ Infants A ──────────┐ ┌ Infants B ──────────┐         │
│ ▸ Infants A ✓ │  │ ✓ Fully monitored   │ │ ! Review required    │         │
│ ▸ Infants B ! │  │ 3 cams · 4 infants  │ │ Infant off surface?  │         │
│ ▸ Toddlers B ✕│  │ last frame 1s ago   │ │ 14:02:11 · Crib 3    │         │
│               │  └─────────────────────┘ │ [Acknowledge] [View] │         │
│ Incidents (3) │  ┌ Toddlers B ─────────┐ └──────────────────────┘         │
│ Health        │  │ ✕ NOT MONITORED     │                                   │
│ Settings      │  │ Cam 2 offline 2m    │                                   │
└───────────────┴──────────────────────────────────────────────────────────┘
```

### 7.3 Camera grid
Tiles with a low-res snapshot (if live view is permitted) or a placeholder, a state badge (icon + word), fps, drop %, and last-frame age. A tile with no live-view permission shows the state only.

### 7.4 Incident inbox
Columns: severity icon + label, type, room/zone, start time, status (`new → acknowledged → under review → resolved`), uncertainty, evidence quality. Sort by severity then time. Unacknowledged alerts are pinned.

### 7.5 Incident detail / evidence review
```
┌ Incident INC-2026-000123 · Possible: infant left raised surface · Room Infants B ─┐
│ Status: Acknowledged by J. (14:02:19) · Reviewer: unassigned  [Assign to me]       │
│ ⚠ This is an automated observation requiring human review. It is not a finding.    │
├───────────────────────────────────────┬─────────────────────────────────────────────┤
│  [ video player: cam 1 | cam 2 ]      │ Evidence                                     │
│  ▮▮▮▮▮▮▮▮▮▮▮|▮▮▮▮▮ timeline with      │ • 14:02:10.4 infant on Changing table 1     │
│  markers: candidate / confirmed       │ • 14:02:11.0 infant below surface, not held │
│                                       │ Visibility: partial (adult occludes 0.4 s)   │
│                                       │ Uncertainty: medium                          │
│                                       │ Models: det v0.3.1 · pose v0.2.0 · rules 14  │
├───────────────────────────────────────┴─────────────────────────────────────────────┤
│ Disposition: (○ routine care ○ false detection ○ concern followed up ○ escalated   │
│ ○ inconclusive)  Note: [.................]  [Submit]   Access to this clip is logged│
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 7.6 Other screens
- **Camera health:** per-camera stream state, bitrate, fps, decode errors, clock skew, last calibration, view-change score.
- **Facility settings:** rooms, operating hours, sleep periods (used by C1 rules), escalation chain.
- **Users & roles:** invite, role, MFA status, last login.
- **Audit & retention:** audit search, retention sliders, legal holds (two-person release).
- **System & model health:** GPU, worker status, model versions, rule versions, shadow/live flag per rule.

### 7.7 Design system
| Token | Value |
|---|---|
| Typeface | Inter / system UI. Tabular numerals for times |
| Scale | 12 / 14 / 16 (body) / 20 / 24 / 32 |
| Spacing | 4-pt grid: 4, 8, 12, 16, 24, 32, 48 |
| Radius | 8 px cards, 6 px controls |
| Neutral palette | Slate 50–900 |
| Status: operational | Green 600 + ✓ icon + "Fully monitored" |
| Status: degraded | Amber 600 + ◐ icon + "Degraded" |
| Status: unavailable | Red 700 + ✕ icon + "Not monitored" |
| Alert: review required | Indigo 600 + ! icon |
| Alert: urgent | Red 700 background, white text, ▲ icon, pulsing border (respects `prefers-reduced-motion`) |
| Focus | 3 px outline, high contrast |
| Contrast | ≥ 4.5:1 text, ≥ 3:1 UI components |

States: loading (skeletons, never a spinner that implies "all OK"), empty ("No incidents in the last 24 h. Monitoring was fully operational 98.7% of operating hours"), error (explicit, actionable). Responsive: wall tablet (landscape), phone (single column, alert-first), desktop reviewer (two-pane). Touch targets ≥ 44 px.
