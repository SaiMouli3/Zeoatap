# Open Questions

**Date:** 2026-10-27. Each item has a provisional assumption so work isn't blocked.

## Business & scope
| ID | Question | Provisional assumption | Blocking? |
|---|---|---|---|
| OQ-B1 | Which country/state is the first deployment? | US, one state, decided in Phase 0 | Blocks pilot, not design |
| OQ-B2 | Is there a partner facility willing to run shadow mode? | Yes, to be recruited | Blocks Phase 4 |
| OQ-B3 | Target customer: daycare centres, NICUs or home? | Licensed daycare centres (infant rooms) | No |
| OQ-B4 | Pricing model (hardware + subscription?) | Subscription incl. appliance | No |

## Hardware & environment
| ID | Question | Assumption | Blocking? |
|---|---|---|---|
| OQ-H1 | What cameras/NVRs exist at pilot sites (make, resolution, codec, fps, IR, mounting)? | 1080p H.264/H.265 PoE, IR, ceiling-corner mounts | Blocks Phase 1 site tests |
| OQ-H2 | Which NVR brands need support? | Hikvision, Dahua, Axis, Uniview, Reolink | No |
| OQ-H3 | Jetson vs. discrete GPU for small sites? | Discrete x86 for pilot | No |
| OQ-H4 | Exact GPU SKU, and its NVDEC throughput limits? | ≥16 GB VRAM discrete GPU | Blocks capacity claims |
| OQ-H5 | Is a GPU available for development? | **None in this environment.** Needs a dev workstation or a cloud dev GPU (approval needed for cost) | Blocks Phase 1 benchmark |

## Legal & licensing
| ID | Question | Assumption |
|---|---|---|
| OQ-L1 | Will ACLab (Northeastern) license FiDIP/SyRIP/InfAct commercially? | No, so plan without them |
| OQ-L2 | Applicable childcare camera laws in the pilot jurisdiction? | Video allowed with notice. No audio |
| OQ-L3 | May we commercially ship weights pretrained on COCO/Kinetics/Objects365? | Uncertain, so budget for retraining on licensed data |
| OQ-L4 | NVIDIA TensorRT/DeepStream redistribution terms for an appliance? | Permitted under NVIDIA SLA, to be confirmed |
| OQ-L5 | ICVD license and commercial availability? | Unknown. Enquire with authors (Bogdanov & Green) |
| OQ-L6 | Are body keypoints "biometric" under BIPA/CUBI/CPRA/GDPR? | Treat as sensitive. Never persist outside incidents |
| OQ-L7 | Mandatory-reporting interplay: what must the facility do when a reviewer sees a concern? | Facility's existing safeguarding policy applies. We provide evidence export |
| OQ-L8 | Parent consent model: opt-in vs. notice? Per child or per room? | Written notice + opt-in for any research use |
| OQ-L9 | Is a patent freedom-to-operate review needed (infant posture patent US 20240193809)? | Yes, before productizing posture detection |

## Research references
| ID | Question |
|---|---|
| OQ-R1 | Exact URL of the "DAÏO fall-detection implementation". Not found by search |
| OQ-R2 | Exact URL of "SentinelCV". Not found by search |
| OQ-R3 | InfAct / InfActPrimitive repo license (README not retrievable) |
| OQ-R4 | DSANet code release |

## Product
| ID | Question | Assumption |
|---|---|---|
| OQ-P1 | Which rooms/hazards matter most to facilities? | Changing tables, cribs, exits, kitchen doors |
| OQ-P2 | Is live viewing wanted, or is it a privacy liability? | Optional, off by default, leads/reviewers only |
| OQ-P3 | What false-alert rate will staff tolerate? | ≤ ~1 high-priority false alert/facility/day |
| OQ-P4 | Alert channels: wall tablet, siren, phone push, pager? | Wall tablet + local chime + push to leads |
| OQ-P5 | Escalation chain and T_ack per facility? | 60 s to the room lead, then 120 s to the director |
| OQ-P6 | Sleep schedules available for C1 rules? | Entered per room by admin |
