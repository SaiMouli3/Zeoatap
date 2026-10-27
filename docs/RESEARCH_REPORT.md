# Research Report — AI Baby-Care CCTV Safety Intelligence Platform

**Sources and licenses checked:** 2026-10-27
**Status of this document:** Research findings. Nothing here is a validated performance claim for our target environment.

---

## 0. Executive summary

1. **No off-the-shelf model detects infant-care safety incidents.** All candidate models are general-purpose (adult pose, action recognition on Kinetics/NTU, violence or anomaly detection on UCF-Crime/XD-Violence) or infant-specific research models with **non-commercial licenses**. Every safety event class needs a purpose-built, legally sourced, labelled dataset before anyone can make a performance claim.
2. **The infant-specific research ecosystem is dominated by one lab (Northeastern ACLab, Ostadabbas).** It is high-quality, but its code (FiDIP, infant posture) and **all** its datasets (SyRIP, AIR-125/AIR-400, InfAnFace, NNS) are restricted to non-commercial use, and its datasets are "not intended for … incorporation into a product". The AIR-400 *code* repository is MIT-licensed, but that does **not** license the data or necessarily the trained checkpoints.
3. **A commercially safe baseline stack exists:** Apache-2.0/MIT detectors (RT-DETR, YOLOX, RF-DETR), trackers (ByteTrack, BoT-SORT), pose models (RTMPose/RTMO via MMPose or rtmlib), skeleton action models (PoseC3D via MMAction2) and an Apache-2.0 VLM (Qwen3-VL 2B/4B/8B). **Ultralytics YOLO and BoxMOT are AGPL-3.0** and should not ship in a closed product without a commercial license. Checkpoint provenance (COCO, Kinetics/YouTube-derived data) still needs legal review.
4. **The temporal *decision* layer, not the model, determines whether the product is useful.** Published benchmarks report clip-level accuracy (e.g. PoseC3D ≈71% on InfActPrimitive, InfAct pipeline 78.0% on 10 classes). These are far below what event-level safety alerting needs, and they come from a different domain (home videos, infant motor milestones).
5. **Several requested capabilities are technically weak on ordinary CCTV.** These are shaking or rough-handling recognition, prolonged-immobility "danger" detection and visual respiration. Treat them as research or review-signal features, not alerts. The strongest early-value features are deterministic and measurable: camera/monitoring health, zone and supervision rules, infant-off-raised-surface state changes, and evidence capture with review workflow.

---

## 1. Methodology

| Step | What was done | Limitation |
|---|---|---|
| Repository inventory | Inspected `/home/user/Zeoatap` (empty git repo, no commits, no footage, no weights, no credentials). Environment: 4 vCPU, 15 GiB RAM, **no NVIDIA GPU**, Python 3.13, Go 1.24, Node 22. | No camera, NVR or GPU could be inspected. |
| Repo verification | Fetched README and LICENSE files directly from `raw.githubusercontent.com`. | GitHub HTML and API were blocked in this session, so star counts, issues, commit history and release tags could not be inspected. Where a README or LICENSE could not be retrieved, this is marked **UNVERIFIED**. |
| Paper verification | Read abstracts from arXiv `abs` pages and CVF open-access PDFs. | Full-paper method sections were not reviewed for every paper. Reported metrics are quoted from abstracts or READMEs. |
| Model-card verification | Hugging Face model API (`license` tags) and raw model-card/LICENSE files. | Model-card licenses can change, so recheck them before procurement. |
| Dataset terms | Lab dataset pages and dataset READMEs. | Data-use agreements behind passwords or forms could not be read. |
| Legal | Statute names and secondary legal commentary only. | **Not legal advice.** Every legal item is a question for qualified counsel. |

Evidence labels used throughout:

- **[V]** Verified from a primary source on 2026-10-27
- **[R]** Reported research result (copied from the authors; not reproduced by us)
- **[E]** Our engineering estimate or calculation
- **[H]** Hypothesis to be tested
- **[U]** Unverified (source could not be accessed)

---

## 2. Infant-specific pose and action research

### 2.1 FiDIP — Fine-tuned Domain-adapted Infant Pose
- **Paper:** Huang, Fu, Liu, Ostadabbas, "Invariant Representation Learning for Infant Pose Estimation with Small Data", IEEE FG 2021, [arXiv:2010.06100](https://arxiv.org/abs/2010.06100) [V]
- **Repo:** [ostadabbas/Infant-Pose-Estimation](https://github.com/ostadabbas/Infant-Pose-Estimation) (README on `master`) [V]
- **Institution:** Augmented Cognition Lab (ACLab), Northeastern University
- **Method:** Adult pose backbones (e.g., DarkPose/HRNet) fine-tuned with domain adaptation on SyRIP (synthetic + real infant images).
- **Reported:** README states best model (DarkPose-based) **93.6 mAP** on SyRIP test [R]. This is a COCO-style keypoint AP on still images of infants, mostly from home and internet imagery. It is **not** CCTV at 3–6 m distance with IR and occlusion.
- **License:** README: *"This code is for non-commertial purpose only."* [V]. SyRIP dataset: ACLab non-commercial terms, and the expanded set is password-gated [V].
- **Use for us:** **Research benchmark only.** We can use it to measure how much infant-specific adaptation helps, under a research agreement. It must not ship. The licensing-safe fallback is §2.6.

### 2.2 Infant posture / 3D (ACLab)
- **Repo:** [ostadabbas/Infant-Posture-Estimation](https://github.com/ostadabbas/Infant-Posture-Estimation): *"This code is for non-commercial purpose only."* [V]. It relies on HW-HuP-Infant for 3D, and its MIMM data is private (obtained through Early Markers) [R via search snippet].
- A patent family exists: "Infant 2D pose estimation and posture detection system" (US patent publication 20240193809, and USPTO document 12567173) [V via search listing]. **Implication:** commercial use may face patent constraints as well as copyright licenses, so a freedom-to-operate review is needed before productizing a similar posture pipeline.

### 2.3 InfAct — Posture-based Infant Action Recognition (CVPRW 2023)
- **Paper:** Huang et al., [CVPRW 2023 L3D-IVU](https://openaccess.thecvf.com/content/CVPR2023W/L3D-IVU/papers/Huang_Posture-Based_Infant_Action_Recognition_in_the_Wild_With_Very_Limited_CVPRW_2023_paper.pdf) [V]
- **Data:** 200 fully annotated **home videos**. Action = stable posture → transition → stable posture [V].
- **Reported:** 78.0% accuracy over the 10 most common classes [R].
- **Repo:** `github.com/ostadabbas/Infant-Posture-based-Action-Recognition`. README/LICENSE could not be retrieved (**[U]**). Assume ACLab non-commercial terms until verified.
- **Useful idea we *can* reuse (ideas are not copyrightable):** model actions as **posture-state transitions**. This maps directly onto "infant on raised surface → infant on floor" fall-state logic (§8 of AI_MODEL_STRATEGY).

### 2.4 InfActPrimitive (WACVW 2024)
- **Paper:** Hatamimajoumerd et al., [arXiv:2311.12300](https://arxiv.org/abs/2311.12300) [V]. Five milestone actions (e.g. sitting, standing transitions).
- **Reported:** PoseC3D best at ≈71%, and other skeleton models "struggle" [R]. **Key takeaway:** even the best skeleton model does poorly on infants. Adult-trained action models do **not** transfer.
- **Repo:** `ostadabbas/Video-Based-Infant-Action-Recognition`. README not retrievable **[U]**.

### 2.5 Why adult pose models fail on infants [R, from FiDIP and InfActPrimitive]
- Different body proportions: large head-to-body ratio, short limbs.
- Infants often lie supine or prone and curl up, poses that are rare in COCO.
- Self-occlusion, blankets, swaddles, cribs (bars occlude), caregivers' hands.
- Small pixel size on CCTV [E]: a 1080p camera with a 100° horizontal FOV at 5 m covers ≈11.9 m of width, which is ≈161 px/m. A 0.6 m infant spans ≈97 px. That is marginal for 17-keypoint pose. **Hurdle H-07**, solved by camera placement, higher resolution or region-of-interest crops.

### 2.6 Licensing-safe fallback for infant pose
1. Start from **Apache-2.0 RTMPose/RTMO** ([arXiv:2303.07399](https://arxiv.org/abs/2303.07399), [arXiv:2312.07526](https://arxiv.org/abs/2312.07526); MMPose Apache-2.0 [V]).
2. Fine-tune on **our own consented, commercially licensed** infant data: manikin recordings, consented parent recordings with explicit commercial-ML consent, licensed synthetic data from a SMPL-family body model *only if* its license allows commercial use (SMIL/SMPL are separately licensed, **[U]** for commercial terms).
3. Benchmark against FiDIP **under a research agreement only** to quantify the gap.
4. Approach ACLab about **commercial licensing** (Open Question OQ-L1).

---

## 3. General video action recognition

| Model | Paper | Code license | Input | Notes |
|---|---|---|---|---|
| **PoseC3D** | [arXiv:2104.13586](https://arxiv.org/abs/2104.13586) | MMAction2 Apache-2.0 [V] | 3D heatmap volume from 2D skeletons | Robust to pose noise, handles multiple people "without additional computation cost", fuses with RGB [R]. Best on InfActPrimitive (~71%) [R]. |
| **SlowFast** | [arXiv:1812.03982](https://arxiv.org/abs/1812.03982) | Apache-2.0 [V] | RGB clips | Strong on Kinetics/AVA. AVA spatio-temporal action detection is relevant for person-level actions. Heavy. |
| **X3D** | [arXiv:2004.04730](https://arxiv.org/abs/2004.04730) | Apache-2.0 (in SlowFast repo) [V] | RGB clips | 4.8×/5.5× fewer FLOPs/params at similar accuracy [R]. Best lightweight RGB candidate. |
| **Video Swin** | [arXiv:2106.13230](https://arxiv.org/abs/2106.13230) | Apache-2.0 [V] | RGB clips | 84.9% K400 top-1 [R]. Too heavy for always-on multi-camera edge. Candidate for offline/second-stage use. |
| VideoMAE | — | **CC BY-NC 4.0** [V] | RGB | **Excluded** for commercial use. |
| Sapiens (Meta) | — | **CC BY-NC 4.0** [V] | RGB | **Excluded**. |
| TimeSformer / MotionFormer | used by ICVD | not checked [U] | RGB | Reported 93.97%/93.17% top-1 on ICVD manikin interventions [R]. |

**Skeleton vs. RGB, for our events:**

| Concern | Skeleton (PoseC3D) | RGB (X3D/SlowFast) |
|---|---|---|
| Privacy / data minimization | ✅ Can discard pixels after pose extraction | ❌ Needs pixels |
| Infant domain gap | ❌ Depends on infant pose quality | ❌ Depends on Kinetics features (adult, YouTube) |
| Blankets / occlusion | ❌ Keypoints vanish | ⚠️ Some context retained |
| Interaction (caregiver + infant) | ✅ Multi-person heatmaps | ✅ |
| Compute | ✅ Lighter once pose exists | ⚠️ Heavier |
| Lighting / IR shift | ✅ Abstracted away by pose | ❌ Sensitive |
| Scene context (crib edge, floor) | ❌ Needs separate zone/scene info | ✅ |

**Recommendation [E]:** skeleton-first (PoseC3D-style) on tracked person crops, **plus** explicit scene geometry (zones), **plus** an optional RGB second-stage classifier (X3D-S/M) for candidate windows only. Both need fine-tuning. **No pretrained checkpoint is usable as-is for infant safety events.**

**Maintenance risk [V]:** MMAction2's last PyPI release is 1.2.0 (2023-10-12), MMPose 1.3.2 (2024-07-12) and MMCV 2.2.0 (2024-04-24). The OpenMMLab stack is effectively in maintenance mode and has known friction with newer PyTorch/CUDA. **Mitigation:** use MMAction2/MMPose for *training*, export to ONNX and run inference with ONNX Runtime/TensorRT (rtmlib, Apache-2.0, actively released 2026-08-04 [V]). This avoids an OpenMMLab runtime dependency on the edge.

---

## 4. Video anomaly detection (VAD)

| Method | Venue | Code | License | Reported | Assessment |
|---|---|---|---|---|---|
| **VadCLIP** | AAAI 2024 | [nwpu-zxr/VadCLIP](https://github.com/nwpu-zxr/VadCLIP) | Apache-2.0 [V] | 84.51% AP XD-Violence, 88.02% AUC UCF-Crime [R] | Weakly supervised, CLIP features. Released weights are trained on **UCF-Crime / XD-Violence**, which are research datasets of crime/violence clips. Domain mismatch with nurseries is total. |
| **DSANet** | AAAI 2026 | No code link found [U] | — | 86.95% AP XD-Violence, 89.44% AUC UCF-Crime [R] ([arXiv:2511.10334](https://arxiv.org/abs/2511.10334)) | Not reproducible yet. Monitor only. |
| **CoReVAD** | ICPR 2026 | [Muk-00/CoReVAD](https://github.com/Muk-00/CoReVAD) | **No LICENSE file found** [V: 404] | "Competitive among training-free methods" [R] ([arXiv:2605.23116](https://arxiv.org/abs/2605.23116)) | Training-free, single frozen VLM. With no license, **all rights reserved**, so we cannot use the code. We can study the idea. |

**Four different things, which must not be conflated:**

1. **Unusual scene** (anomaly score): "this looks statistically different from what this camera usually sees". A new piece of furniture scores high and a quiet harmful act may score low.
2. **Action recognition:** "this window looks like class *k* (e.g. lift, carry, rock)".
3. **Safety-incident probability:** requires calibration on *labelled incidents* in the target domain. **We do not have this, and no public dataset provides it.**
4. **Alert decision:** a policy combining (2), rules, visibility, duration and the cost of false alerts, defined per event type.

**Recommended role for VAD [E]:** a *post-hoc review prioritizer* that ranks hours of footage for optional human audit (Phase 6+). It never triggers alerts and never sits on the critical path. Before any use, retrain or calibrate it on facility-normal footage (this needs consent; see PRIVACY doc).

---

## 5. Reference implementations

| Project | Verified facts | Reusable? |
|---|---|---|
| **zeenolife/ai-baby-monitor** | MIT, © 2025 Almaz Zinollayev [V]. Frames → Redis → watcher → local vLLM (Qwen2.5-VL-7B-AWQ) → JSON verdict → beep. "~1 req/s on consumer GPUs" [R, README]. Streamlit UI. Self-described hobby project. | **Pattern only.** Natural-language rules in YAML are a nice UX. **Anti-pattern for us:** the VLM *is* the detector, the safety decision is an opaque prompt, there is no event-level validation, and 1 req/s on a single camera does not scale. Our design keeps the VLM off the critical path. |
| **DAÏO fall detection** | No repository found under that name **[U]**. | Cannot evaluate. Need the exact URL (OQ-R1). |
| **SentinelCV** | No repository found **[U]**. | Need the exact URL (OQ-R2). |
| atmguille/Violence-Detection-With-Human-Skeletons | CVIU 2023. 90.25% on RWF-2000 val with 60k params [R]. Uses OpenPose (**OpenPose's own license is non-commercial**, verify) | Pattern only (skeleton + change detection). |
| RWF-2000 | Flow-gated network: 87.25% [R], [arXiv:1911.05913](https://arxiv.org/abs/1911.05913). Access requires a signed agreement [R]. | Adult fighting clips, irrelevant to infant handling. Research only. |
| DebadityaPal/hawkeye | TFLite + RWF-2000 [R] | Prototype only. |
| Ambianic `falldetect` | PoseNet + heuristics [R] | Adult falls, prototype quality. |

**General finding:** every reference "violence/fall/baby" project we found is a **prototype**. None has event-level validation, none handles camera-health honesty, and none separates detection from decision. Reusable value is architectural patterns, not code.

---

## 6. Infant-related datasets

| Dataset | Size | Subjects | Content | Access | Commercial use | Redistribution |
|---|---|---|---|---|---|---|
| **SyRIP** (ACLab) | Real + synthetic infant images. Expanded set adds 400 real training images [V] | Real infants + synthetic | 2D keypoints | Password on request [V] | ❌ Non-commercial [V] | ❌ |
| **InfAct** | 200 videos, 10 common action classes [V] | Real infants, home videos | Posture/action segments | GitHub [U] | ❌ assumed (ACLab) | ❌ |
| **InfActPrimitive** | 5 milestone classes [V] | Real infants | Skeleton action | GitHub [U] | ❌ assumed | ❌ |
| **AIR-125 / AIR-400** | 400 videos. Lab page says 18 infants. Repo README says AIR-125 = 125 videos/8 subjects and AIR-400 adds 275 videos/10 subjects [V] | Real infants. Baby monitors + YouTube [V] | Synchronized .mp4 + breathing signal .hdf5 [V] | Public link [V] | ❌ "Non-commercial … not intended for … incorporation into a product" [V]. Code is MIT [V], but **data and possibly checkpoints are not commercial** | ❌ |
| **ICVD** — Infant Care Video Dataset | 4,144 videos, 12 intervention classes, 2892/624/631 split [R] | **Manikin**. Clinician skin tone and camera angle varied, light and dark conditions [R] | NICU care interventions | infantcaredataset.org [U] | License not stated [U] | [U] |
| NICU-Care (IEEE DataPort) | Multi-view RGB, 6 caregiving procedures [R] | Simulated infants | Nurse procedures | IEEE DataPort [U] | [U] | [U] |
| InfAnFace, NNS (ACLab) | 410 faces / 10 clips [V] | Real infants | Faces / sucking | Public | ❌ | ❌ |
| Helsinki/Pisa pose-estimated 3D infant data | [U] | Real infants | 3D pose of spontaneous movement | University catalogue [U] | [U] | [U] |
| UCF-Crime, XD-Violence, RWF-2000, Kinetics, NTU RGB+D | Large adult datasets | Adults | Crime/violence/actions | Agreements vary | Research-oriented. NTU requires a request and is research-only [R]. Kinetics is YouTube-derived | ❌ / ⚠️ |

**Critical finding:** there is **no** public dataset containing labelled real infant-*safety incidents* in childcare CCTV (falls, rough handling). For ethical reasons, there never will be a staged one with real infants. Any positive-class data must come from **manikins, synthetic rendering, consented staged non-harmful proxies, or (rarely) legally obtained and consented historical incident footage**. Hurdle H-01 is the single biggest product risk.

---

## 7. Video-language models (VLMs)

| Model | License [V] | Video input | Local? | Notes |
|---|---|---|---|---|
| **Qwen3-VL-2B/4B/8B-Instruct** | **Apache-2.0** | Yes, 256K context, interleaved video [R] ([arXiv:2511.21631](https://arxiv.org/abs/2511.21631)) | Yes (vLLM) | **Primary candidate** for event-triggered summaries. |
| Qwen3-VL-30B-A3B | Apache-2.0 | Yes | Needs a large GPU | Too heavy for the edge box. |
| Qwen2.5-VL-7B | Apache-2.0 | Yes, second-level event localization [R] | Yes | Used by zeenolife. |
| Qwen2.5-VL-3B | **Qwen Research License** (non-commercial) [V] | Yes | — | ❌ Excluded |
| Qwen2.5-VL-72B | "other" (Qwen license) [V] | — | — | ❌ Too heavy, license review |
| InternVL3-8B | Apache-2.0 [V] | Multi-frame | Yes | Alternative. |
| SmolVLM2-2.2B | Apache-2.0 [V] | Video | Yes | Lightweight alternative. |
| Phi-4-multimodal | MIT [V] | Images | Yes | Alternative. |
| Gemma 3 | Gemma terms [V] | Images | Yes | Requires terms review. |

**Constraints [E]:** VLM latency is seconds, not milliseconds, and its outputs are not calibrated. It must **never** be the sole basis of a high-priority alert. Use it only to *summarize already-captured evidence* in a constrained JSON schema (see AI_MODEL_STRATEGY §9) and only after an A/B test shows it saves reviewer time without adding errors.

---

## 8. Detection and tracking (commercial-safety check)

| Component | License [V] | Verdict |
|---|---|---|
| RT-DETR (lyuwenyu) | Apache-2.0 | ✅ Candidate |
| YOLOX | Apache-2.0 | ✅ Candidate (mature, easy TensorRT export) |
| RF-DETR (Roboflow) | Apache-2.0 (develop branch LICENSE) | ✅ Candidate. Check per-checkpoint terms |
| **Ultralytics YOLO (v8/11/…)** | **AGPL-3.0** | ⚠️ Only with a purchased enterprise license |
| ByteTrack | MIT | ✅ Default tracker |
| BoT-SORT | MIT | ✅ Alternative (camera-motion compensation unnecessary for fixed CCTV) |
| **BoxMOT** | **AGPL-3.0** | ⚠️ Avoid |
| rtmlib (RTMPose/RTMO ONNX runtime) | Apache-2.0 | ✅ Inference runtime |
| DWPose | Apache-2.0 | ✅ Alternative pose |
| ViTPose | Apache-2.0 | ✅ Accurate but heavier |
| SAM 2 | Apache-2.0 | Optional annotation tooling only |

**Weights provenance caution:** Apache-licensed *code* may ship weights trained on COCO (images under Flickr terms, annotations CC-BY 4.0), Objects365, Kinetics (YouTube) and so on. Whether redistributing or commercially using those weights is permitted is a **legal question** (OQ-L3). The licensing-safe path is to retrain or fine-tune on data we hold rights to and record provenance in `LICENSING_REGISTER.md`.

---

## 9. Video infrastructure facts

- **DeepStream 8.0** supports Turing, Ampere, Hopper, Ada, Blackwell and Jetson Thor. It is built on JetPack 7.0, and **Jetson Orin is not in the 8.0 support table** per an NVIDIA forum report. **DeepStream 9.0** has been released (Triton 26.01 x86, JetPack 7.1) [V: [DS 8.0 release notes](https://docs.nvidia.com/metropolis/deepstream/8.0/text/DS_Release_notes.html), [DS 9.0 release notes](https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_Release_notes.html)]. Known issues reported include FP16 NaNs on Thor and CUDA 13 + Triton incompatibilities [R, forum].
- **TensorRT** is proprietary (NVIDIA license) [V: PyPI], and so is DeepStream. Both are redistributable under NVIDIA terms, which need review (OQ-L4).
- **ONNX Runtime GPU** 1.31.1 is MIT [V]. **vLLM** 0.32.0 [V].

---

## 10. Decision matrix

Legend: Priority **P0** (first prototype), **P1** (soon after), **P2** (later), **X** (excluded until resolved). Confidence is our confidence in *this assessment*, not in the model.

| Project / model | Purpose | Infant-specific | Input | Role in system | Published results (benchmark) | Real-time suitability | GPU / mem | Fine-tuning needed | Repo license | Checkpoint license | Dataset restrictions | Commercial feasibility | Main failure modes | Integration complexity | Priority | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| YOLOX / RT-DETR | Person detection | No | Frame | Stage 1 detector (adult + infant classes) | COCO AP [R] | High [E] | Small–medium | **Yes** (add infant class; IR footage) | Apache-2.0 | COCO-trained [U] | COCO terms | ✅ after weight review | Infants in cribs/blankets missed; IR domain | Low | **P0** | High |
| ByteTrack | Tracking | No | Detections | Track IDs | MOT17 [R] | High | CPU | No | MIT | n/a | n/a | ✅ | ID switches during caregiver–infant overlap | Low | **P0** | High |
| RTMPose / RTMO | 2D pose | No | Crops / frame | Pose features | RTMPose-m 75.8% COCO AP, 430+ FPS GTX 1660 Ti; RTMO-l 74.8% AP, 141 FPS V100 [R] | High [R→E] | Small | **Yes** (infant) | Apache-2.0 | COCO/AIC etc. [U] | Varies | ✅ after weight review | Infant poses, occlusion, small pixel size | Low–Med | **P0** | Medium |
| FiDIP | Infant pose | **Yes** | Image | Research benchmark only | 93.6 mAP SyRIP [R] | Unknown | Medium | — | **Non-commercial** | NC | NC | ❌ | Home-photo domain ≠ CCTV | Med | **X** (research) | High |
| InfAct pipeline | Infant posture actions | **Yes** | Video→pose | Idea: posture-transition modelling | 78.0% / 10 classes [R] | Unknown | — | — | [U] | [U] | NC (assumed) | ❌ code; ✅ idea | Home videos only | — | **X** (idea reused) | Medium |
| PoseC3D | Skeleton action | No | Pose heatmaps | Stage 3 window classifier | ~71% InfActPrimitive [R] | Medium | Small–med | **Yes**, new labelled data | Apache-2.0 | NTU/Kinetics-trained [U] | NTU research-only | ✅ if retrained | Pose noise; few positives | Med (MMAction2 maintenance) | **P1** | Medium |
| X3D-S/M | RGB action | No | Clip | Second-stage verifier | Kinetics [R] | Medium (event-triggered) | Medium | **Yes** | Apache-2.0 | Kinetics [U] | YouTube-derived | ⚠️ review | Domain gap | Med | **P2** | Medium |
| SlowFast / Video Swin | RGB action | No | Clip | Offline research only | K400 84.9% (Swin) [R] | Low for multi-cam | High | Yes | Apache-2.0 | Kinetics [U] | ⚠️ | ⚠️ | Heavy | High | **P2** | Medium |
| VadCLIP | Anomaly | No | CLIP features | Review prioritization (later) | 84.51 AP XD / 88.02 AUC UCF [R] | Medium (batch) | Medium | **Yes** (facility normal data) | Apache-2.0 | UCF/XD-trained | Research datasets | ⚠️ retrain required | Scene changes ≠ danger | Med | **P2** | Medium |
| DSANet | Anomaly | No | — | Monitor | 86.95 AP / 89.44 AUC [R] | — | — | — | No code [U] | — | — | ❌ now | — | — | **X** | Low |
| CoReVAD | Training-free anomaly | No | VLM | Idea only | "competitive" [R] | Low | High | No | **No license** [V] | — | — | ❌ | VLM hallucination | — | **X** | Medium |
| Qwen3-VL-4B/8B | VLM summaries | No | Frames/clip | Event-triggered evidence summary | General benchmarks [R] | **Low** (seconds) | 8–24 GB class [E, measure] | Prompt + schema; maybe LoRA | Apache-2.0 | Apache-2.0 | n/a | ✅ | Hallucination, over-claiming | Med | **P2** | Medium |
| zeenolife baby monitor | VLM rule watcher | Partly | Frames | UX pattern only | ~1 req/s [R] | Low | 1 GPU | — | MIT | Qwen2.5-VL-7B Apache | — | ✅ code, but wrong design | Opaque prompt decisions | Low | **Pattern** | High |
| AIR-400 | Respiration | **Yes** | Video | Research only | See paper ([arXiv:2512.06888](https://arxiv.org/abs/2512.06888)) [R] | Unknown | — | — | MIT (code) | [U] | **NC / not for products** | ❌ data | Blankets, motion, lighting | High | **X** (research) | High |
| ICVD | Care interventions | Yes (manikin) | Video | Possible pretraining for "routine care" negatives | 93.97% TimeSformer [R] | — | — | — | — | — | License [U] | [U] → OQ-L5 | Manikin ≠ infant; NICU ≠ daycare | — | **P1 if licensable** | Low |

### 10.1 Conclusions

1. **Best first prototype:** YOLOX or RT-DETR (fine-tuned with an `infant` class) → ByteTrack → RTMPose (via rtmlib, fine-tuned) → **deterministic temporal rules over zones and posture states**. No learned action classifier is needed for the first event families (health, zones, off-surface, supervision).
2. **Best for commercial deployment:** the same stack, retrained end-to-end on rights-cleared data, exported to TensorRT. Add a **PoseC3D-style skeleton window classifier** once ≥ several hundred labelled positive windows per class exist (manikin + staged proxies).
3. **Best lightweight inference:** RTMO (one-stage, cost independent of people count) or RTMPose-s/m on crops. Choose by measured latency on the chosen GPU.
4. **Best for anomaly-based clip prioritization:** VadCLIP architecture, retrained on facility-normal footage, batch/offline only.
5. **Do not use until resolved:** FiDIP, InfAct, InfActPrimitive code and all ACLab datasets (non-commercial), AIR-400 data, VideoMAE, Sapiens, Qwen2.5-VL-3B (non-commercial), Ultralytics/BoxMOT (AGPL, unless licensed), CoReVAD (no license), DSANet (no code), OpenPose-based repos.
6. **Experiments that would change these recommendations:**
   - E1: if RTMPose fine-tuned on ≤2k infant frames reaches <X% PCK on our CCTV test set but FiDIP-style domain adaptation closes the gap → pursue a commercial license with ACLab.
   - E2: if RTMO does not handle cribs/occlusion → switch to a top-down detector + RTMPose on crops.
   - E3: if a skeleton classifier cannot separate rocking/bouncing from abrupt motion on manikin + staged data → **drop "abrupt handling" from alerting entirely** and keep it as a review-only signal.
   - E4: if X3D on candidate windows reduces false alerts by ≥30% at equal recall in shadow mode → promote it to P1.
   - E5: if a VLM summary measurably shortens reviewer time-to-disposition without increasing wrong dispositions → ship VLM summaries.

---

## 11. Research gaps

- No public infant-safety-incident CCTV dataset.
- No published, validated method for detecting shaking/rough handling of infants from ordinary CCTV.
- Visual respiration: research-grade on home baby monitors only. There is no evidence for wide-angle daycare CCTV, and no clinical validation.
- Cross-camera infant identification: no evidence that appearance embeddings are reliable for infants, who wear similar clothing, are swaddled and change outfits.
- IR/night-vision infant pose: no benchmark found.

---

## 12. Sources (checked 2026-10-27)

- FiDIP: https://arxiv.org/abs/2010.06100 ; https://raw.githubusercontent.com/ostadabbas/Infant-Pose-Estimation/master/README.md
- Infant posture: https://raw.githubusercontent.com/ostadabbas/Infant-Posture-Estimation/master/README.md
- InfAct: https://openaccess.thecvf.com/content/CVPR2023W/L3D-IVU/papers/Huang_Posture-Based_Infant_Action_Recognition_in_the_Wild_With_Very_Limited_CVPRW_2023_paper.pdf
- InfActPrimitive: https://arxiv.org/abs/2311.12300
- ACLab datasets & license: https://ostadabbas.sites.northeastern.edu/datasets
- AIR-400: https://arxiv.org/abs/2512.06888 ; https://raw.githubusercontent.com/michaelwwan/air-400/main/README.md
- ICVD: https://arxiv.org/abs/2608.23838
- PoseC3D: https://arxiv.org/abs/2104.13586 ; SlowFast: https://arxiv.org/abs/1812.03982 ; X3D: https://arxiv.org/abs/2004.04730 ; Video Swin: https://arxiv.org/abs/2106.13230
- RTMPose: https://arxiv.org/abs/2303.07399 ; RTMO: https://arxiv.org/abs/2312.07526
- MMAction2 / MMPose / MMDetection LICENSE (Apache-2.0): raw.githubusercontent.com/open-mmlab/*/main/LICENSE ; PyPI release dates: https://pypi.org/pypi/mmaction2/json
- VadCLIP: https://github.com/nwpu-zxr/VadCLIP ; DSANet: https://arxiv.org/abs/2511.10334 ; CoReVAD: https://arxiv.org/abs/2605.23116 , https://github.com/Muk-00/CoReVAD
- zeenolife: https://raw.githubusercontent.com/zeenolife/ai-baby-monitor/master/README.md (+ LICENSE)
- Skeleton violence: https://github.com/atmguille/Violence-Detection-With-Human-Skeletons ; RWF-2000: https://arxiv.org/abs/1911.05913
- Qwen2.5-VL: https://arxiv.org/abs/2502.13923 ; Qwen3-VL: https://arxiv.org/abs/2511.21631 ; HF API license tags: https://huggingface.co/api/models/Qwen/Qwen3-VL-8B-Instruct (and siblings)
- Ultralytics AGPL: https://raw.githubusercontent.com/ultralytics/ultralytics/main/LICENSE ; BoxMOT AGPL: https://raw.githubusercontent.com/mikel-brostrom/boxmot/master/LICENSE
- DeepStream: https://docs.nvidia.com/metropolis/deepstream/8.0/text/DS_Release_notes.html ; https://docs.nvidia.com/metropolis/deepstream/9.0/text/DS_Release_notes.html
- BIPA commentary: https://www.insideprivacy.com/privacy-and-data-security/illinois-federal-court-dismisses-bipa-suit-against-x-holding-biometric-identifiers-must-identify-individuals/
- Infant posture patent listing: https://patents.justia.com/patent/20240193809
