# Privacy, Security & Compliance

**Date:** 2026-10-27 · **Not legal advice.** Every legal statement here is a *question for qualified counsel* in the deployment jurisdiction.

---

## 1. Summary

- Video of infants in care is among the most sensitive data a company can hold. **Default posture: video never leaves the facility, nothing is used for training, there are no biometrics, and retention is short.**
- The system **augments** supervision. Facilities must not reduce staffing or supervision because of it, and contracts and training must say so.
- Legal requirements vary by jurisdiction and are unresolved. Counsel review is a **Phase 0 gate**.

---

## 2. Threat model (STRIDE, abridged)

| Asset | Threat | Example | Mitigation |
|---|---|---|---|
| Live video | Information disclosure | Attacker on Wi-Fi watches camera feeds | Camera VLAN. Edge-only access to cameras. TLS. No direct staff→camera path |
| Evidence clips | Disclosure / tampering | Leaked clip of an infant; altered evidence | Encryption at rest, per-clip keys, short-lived tokens, access log, SHA-256 hash chain in audit |
| Camera credentials | Disclosure | RTSP passwords in logs/config | Secret store, redaction, rotation guidance |
| Admin actions | Repudiation | Admin deletes an incident and denies it | Append-only hash-chained audit log, signed admin actions, two-person rule for deleting held evidence |
| Model release | Tampering / supply chain | Malicious model update | Signed manifests (Sigstore/minisign), hash verification, SBOM, pinned dependencies |
| Edge appliance | Elevation | Physical access to the box | Disk encryption (LUKS + TPM), secure boot where supported, locked enclosure, no default passwords |
| Control plane | Spoofing | Fake edge device | mTLS with per-device certs provisioned at enrolment |
| Alerts | Disclosure | Push notification shows image on lock screen | **No media or child-identifying data in notifications**: "Room Infants-A: review required" |
| Tenant data | Disclosure across tenants | Facility A sees Facility B | Tenant ID on every row + Postgres row-level security in the cloud. Edge holds one tenant only |
| Availability | DoS | Camera flood, disk fill | Rate limits, bounded queues, disk quotas, health alerts |

---

## 3. Data classification

| Class | Examples | Handling |
|---|---|---|
| **C4 – Restricted** | Incident clips, live video, any frames | Edge only. Encrypted. RBAC + access log. Retention ≤ 30 days default |
| **C3 – Sensitive** | Incident metadata linked to a room/time, staff identities, roster (crib ↔ child) | Edge DB. Pseudonymous child IDs. Cloud only as metadata if the facility opts in |
| **C2 – Internal** | Health metrics, model versions, aggregate counts | Cloud telemetry allowed |
| **C1 – Public** | Docs | — |
| **Never collected** | Face embeddings, biometric templates, audio (unless separately justified), emotion inferences, caregiver performance scores | — |

---

## 4. Access control (RBAC)

| Role | Live view | Incident list | Clip view | Disposition | Config | Users | Retention / holds | Audit |
|---|---|---|---|---|---|---|---|---|
| Caregiver | Own rooms (if facility enables) | Own rooms | ❌ by default | Ack only | ❌ | ❌ | ❌ | ❌ |
| Room lead | Own rooms | Own rooms | ✅ own rooms | Ack + note | ❌ | ❌ | ❌ | ❌ |
| Designated reviewer (safeguarding lead) | ✅ | ✅ | ✅ | ✅ resolve / escalate | ❌ | ❌ | Request hold | Read own |
| Facility admin | ✅ | ✅ | ❌ unless also reviewer | ❌ | ✅ | ✅ | ✅ | ✅ |
| Vendor support | ❌ | Metadata only | ❌ (break-glass with facility approval, time-boxed and logged) | ❌ | Remote with approval | ❌ | ❌ | ❌ |

MFA is required for reviewer and admin roles. Sessions time out after 15 minutes of inactivity on shared devices.

---

## 5. Retention

| Data | Default | Configurable range | Deletion method |
|---|---|---|---|
| Ring buffer | 60–120 s | 30–300 s | Overwrite (RAM) |
| Incident clips | 30 days | 7–90 days (per counsel) | Crypto-shred key + file delete. Verified by a deletion job report |
| Legal hold | Until released | — | Two-person release |
| Incident metadata | 1 year | Per counsel | Hard delete or anonymize |
| Audit log | 3 years | Per counsel | — |
| Research data | Only under separate consent | Per consent | Per consent |

**Operational CCTV retention (NVR) stays the facility's responsibility** under their existing policy. We do not copy continuous video.

---

## 6. Consent and notices
- Written facility authorization for **every camera** (camera registration requires an authorization record: who authorized, date, scope).
- **Parent notice and consent** form templates (counsel to adapt): what is analyzed, what is stored, retention, who can access, complaints process, that there is no biometric identification, that there is no training use without separate opt-in.
- **Staff notice** (and works-council / union consultation where applicable): monitoring scope, that the system does not score staff performance, the review process and appeal rights.
- Signage at entrances and in monitored rooms.
- **Excluded areas:** toilets, nappy-changing areas where local law or policy prohibits recording (for example, the Texas HB 459 analysis proposed bans on monitoring bathrooms and changing areas; verify what is actually in force), and staff break rooms.

---

## 7. Security engineering checklist
- [ ] TLS 1.3 on all HTTP. mTLS edge↔cloud. RTSP over camera VLAN (RTSPS where cameras support it)
- [ ] LUKS2 full-disk encryption + per-clip AES-256-GCM
- [ ] Secrets: OS keyring / age-encrypted, never in env files in git, rotation procedure
- [ ] Signed releases (containers + models). SBOM (Syft). Vulnerability scan (Grype/Trivy) in CI. Dependency pinning (`uv.lock`, `go.sum`, `pnpm-lock.yaml`)
- [ ] Clip URLs: opaque IDs + 5-minute tokens bound to user session. No public bucket URLs
- [ ] Outbound egress allow-list on the edge firewall (control plane, NTP, push relay)
- [ ] Audit: append-only table, each row includes `prev_hash`, daily anchor hash sent to the cloud
- [ ] Pen test before pilot (Phase 4). Annual thereafter
- [ ] Backup: Postgres metadata nightly (encrypted) to the facility's chosen location or the cloud (metadata only). Restore is tested quarterly
- [ ] Incident response plan: detection, containment, notification timelines per jurisdiction (counsel), customer comms templates
- [ ] Data subject requests: export and deletion procedures for parents and staff
- [ ] Privacy complaint process with an owner and SLA

---

## 8. External processing
**No continuous video goes to any third party.** Opt-in cloud features (e.g. cloud VLM summaries, off-site clip escrow) require:
- Facility admin opt-in + DPA with the vendor + zero-data-retention terms
- Data minimization: only the incident clip, with non-subject people blurred where feasible
- Per-request audit record and a documented local-only fallback
- Cost estimate shown to the customer

---

## 9. Biometrics and re-identification
- **No face recognition, no face embeddings, no gait ID** by default (ADR-003).
- Pose keypoints are **not stored outside incident windows**. Whether body keypoints could be "biometric" under some laws (e.g. Illinois BIPA covers "scan of hand or face geometry"; courts are divided on scope) is **OQ-L6** for counsel. Mitigation: never derive face/hand geometry templates, never persist keypoints beyond the incident retention period, and never use them for identification.
- An opt-in ReID design would require a DPIA, legal review in each jurisdiction, explicit consent, separate storage, deletion at session end and a measured accuracy proof. **Not planned.**

---

## 10. Legal-review checklist (US-first; adapt per country)

| Area | Question for counsel | Source to start from |
|---|---|---|
| State childcare licensing | Are cameras permitted/required? Do they require parent notice, retention minimums or bans on certain areas? (e.g. Texas: cameras not mandated by minimum standards; HB 459 (2019) proposed mandates — verify current law) | State licensing agency rules (e.g. Texas HHSC minimum standards) |
| Recording consent | Is video-only (no audio) analysis consent-exempt? Audio triggers wiretap laws, **so do not enable audio** | State wiretap statutes |
| Biometrics | BIPA (IL), CUBI (TX), WA RCW 19.375, CCPA/CPRA "biometric information": does pose analysis fall in scope? | 740 ILCS 14; Tex. Bus. & Com. Code §503.001 |
| Children's data | COPPA applies to online services collecting from children; probably not directly applicable, but state child-privacy laws may be | FTC COPPA rule |
| Mandatory reporting | If a reviewer sees possible abuse, what are the facility's duties? The product must support, not replace, those processes | State child-protection statutes |
| Evidence | Chain-of-custody expectations for clips used by authorities | Counsel |
| Employment | Employee monitoring notice rules (e.g. NY Civil Rights Law §52-c electronic monitoring notice) | State statutes |
| Medical device | Would any respiration/immobility feature be a medical device (FDA SaMD)? **Avoid health claims entirely** | FDA SaMD guidance |
| International | GDPR (DPIA mandatory for systematic monitoring of vulnerable subjects), UK ICO surveillance code, India DPDP Act (children's data, verifiable parental consent) | Regulator guidance |
| EU AI Act | Could this be "high-risk" or touch prohibited practices (e.g. emotion recognition in workplaces is prohibited)? Excluding emotion detection is required | AI Act text |
