# Cost & Deployment Model

**Date:** 2026-10-27 · **All numbers are illustrative engineering estimates [E]** unless they carry a source. No vendor prices were verified in this session, so prices appear as **variables**, not dollar figures. Replace the variables with quotes during Phase 0.

---

## 1. Inputs (variables)

| Symbol | Meaning | Illustrative value |
|---|---|---|
| N | cameras | 4 / 16 / 64 |
| b | stream bitrate (Mbps) | H.264 1080p ≈ 4; H.265 ≈ 2; sub-stream 720p ≈ 1 |
| f_base | analyzed fps (normal cams) | 5 |
| f_hi | analyzed fps (raised-surface cams) | 15 |
| p_hi | fraction of cams that are raised-surface | 0.5 |
| P | mean persons per analyzed frame | 3 |
| t_det | detector GPU ms/frame (batched) | **measure** (Phase 1) |
| t_pose | pose GPU ms/person crop (batched) | **measure** |
| t_win | window classifier ms per candidate window | **measure** |
| c_rate | candidate windows per camera-hour | **measure** (shadow mode) |
| clip_s | pre+post clip length (s) | 60 (30 + 30) |
| i_rate | incidents (incl. review) per camera-day | 2 [H] |
| R_days | clip retention days | 30 |
| H_op | operating hours/day | 11 |
| U | target GPU utilization ceiling | 0.6 (headroom for bursts) |

---

## 2. Formulas

**Ingest bandwidth (LAN):** `B_lan = N × b` Mbps
**Decode load:** `N × stream_fps` frames/s through NVDEC. The ring buffer stores *encoded* packets, so it needs no decode. In practice the decoder still has to process every reference frame of a long-GOP stream, so assume full-rate decode when sizing.
**Analyzed fps:** `F = N × ((1−p_hi)·f_base + p_hi·f_hi)`
**GPU time per second:** `G = F × (t_det + P × t_pose) + (N × c_rate/3600) × t_win` ms/s
**GPUs needed:** `ceil(G / (1000 × U))`
**Clip storage:** `S = N × i_rate × R_days × clip_s × b / 8` MB
**Ring buffer RAM:** `M = N × ring_s × b / 8` MB
**Egress (hybrid default):** metadata only, on the order of KB per incident + metrics ≈ <1 GB/month/site [E]
**Egress (cloud-processing alternative):** `N × b × 3600 × H_op × 30 / 8 / 1000` GB/month

---

## 3. Scenarios (illustrative, b = 4 Mbps main stream; inference on a 2 Mbps sub-stream where available)

| | Small pilot | Medium | Large |
|---|---|---|---|
| Cameras N | 4 | 16 | 64 |
| LAN ingest (main) | 16 Mbps | 64 Mbps | 256 Mbps |
| Analyzed fps F (p_hi=0.5) | 40 | 160 | 640 |
| Ring buffer RAM (120 s) | 240 MB | 960 MB | 3.8 GB |
| Clip storage (30 days, 2 inc/cam/day, 60 s @4 Mbps = 30 MB/clip) | 7.2 GB | 29 GB | 115 GB |
| Cloud-processing upload alternative (main stream, 11 h/day) | ~2.4 TB/mo | ~9.5 TB/mo | ~38 TB/mo |
| Upstream needed for cloud processing | 16 Mbps sustained | 64 Mbps | 256 Mbps |
| GPU requirement | `ceil(G/600)` with measured t_det/t_pose | same | same, likely multi-GPU / multi-node |
| CPU | 4–8 cores (RTSP, demux, rules) | 8–16 cores | 2–4 nodes × 16 cores |
| System RAM | 16 GB | 32 GB | 64 GB per node |
| Disk | 512 GB NVMe | 1 TB NVMe | 2 TB NVMe per node |

**Worked GPU example (hypothetical latencies, to show the method, not a claim):** suppose measurement gives t_det = 2.0 ms and t_pose = 0.8 ms per crop at batch size. Then for 16 cameras: G = 160 × (2.0 + 3 × 0.8) = 704 ms/s, so ≈1.2 GPUs at U=0.6, meaning **2 GPUs or a faster GPU**. Halving f_hi to 10 fps reduces F to 120 and G to 528 ms/s, so 1 GPU. *This shows why per-camera fps policy, not GPU marketing numbers, drives hardware sizing.*

---

## 4. Cost formulas

**Edge (per site, monthly):** `C_edge = (HW_capex / amort_months) + power_W × 24 × 30 / 1000 × kWh_price + support_cost`
**Cloud control plane (per site, monthly):** `C_cp = (control_plane_infra / sites) + push_SMS_messages × unit_price`
**Cloud GPU processing alternative (monthly):** `C_gpu = gpu_hours × gpu_hour_price + egress_GB × egress_price + storage`, with `gpu_hours = H_op × 30 × ceil(G/(1000U))`
**VLM add-on:** `incidents × vlm_seconds_per_incident` GPU-seconds (on the edge GPU off-peak, or a second GPU)

**Sensitivity (direction of change):**

| Lever | Effect |
|---|---|
| Resolution ↑ | Detector/pose cost ↑ roughly with pixels. Bitrate ↑. Small-infant detectability ↑ |
| Analyzed fps ↑ | GPU linear ↑. Fall-motion evidence ↑ |
| Models ↑ | GPU additive. Each must earn its place via an experiment |
| Cameras ↑ | Linear, until NVDEC or PCIe limits are hit, then nodes |
| Retention ↑ | Storage linear |
| % footage sent to VLM ↑ | Most expensive lever, so keep it event-triggered only |

---

## 5. Efficiency techniques

| Technique | Benefit | Risk / guard |
|---|---|---|
| **Batching** across cameras | Higher GPU throughput per ms | Adds latency ≤ one batch interval. Cap at 50 ms |
| **Adaptive sampling** | Lower base fps on low-risk cameras | **Never below 15 fps on raised-surface cameras.** Burst on candidates |
| **Event-triggered inference** | Window classifier / VLM only on candidates | Candidate trigger recall must be measured, because a missed trigger means a missed event |
| **Model cascade** | Cheap detector → pose only on infant/adult crops → classifier only on candidates | Each stage's recall bounds the whole |
| **Motion gating** | Skip detection when the frame difference ≈ 0 | **Dangerous for immobility rules** (C1 needs "still" observations). Only skip with a periodic forced sample (≥1 fps) |
| **Sub-stream inference** | 3–4× less decode/infer cost | Smaller infants in pixels. Validate per camera |

---

## 6. Edge GPU vs. shared cloud inference

| | Dedicated edge GPU | Shared cloud GPU |
|---|---|---|
| Offline | ✅ | ❌ |
| Privacy | ✅ | ❌ |
| Utilization | Low (idle nights) | High |
| Cost driver | CapEx, amortized | GPU-hours + **egress + uplink upgrades** |
| Latency | LAN | WAN |
| **Verdict** | **Default** | Research/batch only |

---

## 7. Hardware recommendation (provisional, pending Phase 1 benchmark)

- **Pilot:** x86 small-form-factor workstation, one NVIDIA discrete GPU with ≥16 GB VRAM (room for the detector, pose and a later VLM at 4-bit), 32 GB RAM, 1 TB NVMe, TPM 2.0, UPS. Discrete x86 avoids the Jetson/JetPack/DeepStream version-lock seen in DS 8.0 (Orin not listed; see RESEARCH_REPORT §9).
- **Jetson** (Orin/Thor) remains an option for very small sites once software support is confirmed for the chosen JetPack/DeepStream versions (OQ-H3).
- **CPU-only** cannot provide fall detection at the required fps. Use it only for health and zone presence as a fallback.
- Validate NVDEC session/throughput limits and driver support on the exact GPU SKU (OQ-H4).
