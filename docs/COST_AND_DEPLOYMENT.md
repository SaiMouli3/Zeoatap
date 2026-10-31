# Cost & Deployment Model

**Date:** 2026-10-27, updated 2026-10-31 with measured t_det/t_pose ([BENCHMARK_RESULTS](BENCHMARK_RESULTS.md)) · **All other numbers are illustrative engineering estimates [E]** unless they carry a source. No vendor prices were verified in this session, so prices appear as **variables**, not dollar figures. Replace the variables with quotes during Phase 0.

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
| t_det | detector GPU ms/frame (batched) | **Measured** (TensorRT FP16, batch 8, RTX PRO 4500 Blackwell): YOLOX-S 0.42 / YOLOX-M 0.96 / RT-DETR-R18 0.89 ms at 640². At 1280²: 1.85 / 4.43 / 3.25 ms. Re-measure on the edge SKU |
| t_pose | pose GPU ms/person crop (batched) | **Measured** (same setup): RTMPose-s 0.061, RTMPose-m 0.112 ms/crop. One-stage RTMO-s 0.44 and RTMO-m 0.89 ms/**frame** |
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
| GPU requirement (inference only, YOLOX-M 640 + RTMPose-m, TRT FP16, benchmark GPU) | 1 GPU (G ≈ 52 ms/s) | 1 GPU (G ≈ 207 ms/s) | 2 GPUs by inference (G ≈ 830 ms/s). In practice multi-node because of decode/CPU |
| CPU | 4–8 cores (RTSP, demux, rules) | 8–16 cores | 2–4 nodes × 16 cores |
| System RAM | 16 GB | 32 GB | 64 GB per node |
| Disk | 512 GB NVMe | 1 TB NVMe | 2 TB NVMe per node |

**Worked GPU example (measured latencies, [BENCHMARK_RESULTS](BENCHMARK_RESULTS.md), TensorRT FP16, batch 8, on an RTX PRO 4500 Blackwell):** YOLOX-M at 640² + RTMPose-m gives t_det = 0.960 ms and t_pose = 0.112 ms, so 1.297 ms per frame with P = 3.

| N | F | G = F × 1.297 | GPUs = ceil(G/600) |
|---|---|---|---|
| 4 | 40 | 52 ms/s | 1 |
| 16 | 160 | 207 ms/s | 1 |
| 64 | 640 | 830 ms/s | 2 |

That is **≈46 cameras per GPU** for inference alone (49 with RT-DETR-R18 at 640², 29 with YOLOX-S at 1280², 12 with YOLOX-M at 1280²). Detector input resolution, not pose, dominates the cost (1280² is ≈4–5× 640²). With ONNX Runtime FP32 instead of TensorRT, capacity drops 2–5× (YOLOX-M 640 + RTMPose-m: 14 cameras). **These figures exclude decode, pre/post-processing, tracking and CPU limits**, so NVDEC and CPU will bind first at high camera counts. The real ceiling comes from the soak test (task 1.10) on the chosen edge SKU (OQ-H4). They measure speed only, not accuracy.

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

## 7. Hardware recommendation (provisional; inference benchmark done, edge-SKU and end-to-end soak pending)

- **Pilot:** x86 small-form-factor workstation, one NVIDIA discrete GPU with ≥16 GB VRAM (detector + pose use < 1 GB each per the benchmark; the headroom is for a later VLM at 4-bit), 32 GB RAM, 1 TB NVMe, TPM 2.0, UPS. Discrete x86 avoids the Jetson/JetPack/DeepStream version-lock seen in DS 8.0 (Orin not listed; see RESEARCH_REPORT §9).
- **Jetson** (Orin/Thor) remains an option for very small sites once software support is confirmed for the chosen JetPack/DeepStream versions (OQ-H3).
- **CPU-only** cannot provide fall detection at the required fps. Use it only for health and zone presence as a fallback.
- Validate NVDEC session/throughput limits and driver support on the exact GPU SKU (OQ-H4), and re-run `services/vision-worker/benchmark` on it.
- **Pin the kernel or use a DKMS-managed driver.** In the benchmark, an unattended kernel upgrade left the GPU driver unloaded (BENCHMARK_RESULTS, Failures 1). An edge appliance must not be able to lose its GPU after a reboot (H-23).
