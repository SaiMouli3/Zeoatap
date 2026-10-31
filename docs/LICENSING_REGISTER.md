# Licensing Register

**Checked:** 2026-10-27 by repository/model-card inspection. **Review status:** *Engineering review only. Not reviewed by counsel.* Re-check before every release.

| Component | Type | Code license | Weights / data license | Commercial use | Attribution | Unresolved | Source | Status |
|---|---|---|---|---|---|---|---|---|
| MMAction2 | Code | Apache-2.0 | Checkpoints trained on Kinetics/NTU etc. | ✅ code; ⚠️ weights | NOTICE + license | Weight provenance | raw.githubusercontent.com/open-mmlab/mmaction2/main/LICENSE | Candidate |
| MMPose (RTMPose/RTMO) | Code | Apache-2.0 | COCO/AIC/etc.-trained | ✅ code; ⚠️ weights | NOTICE | OQ-L3 | …/open-mmlab/mmpose/main/LICENSE | Candidate |
| rtmlib | Code | Apache-2.0 | Downloads MMPose ONNX weights | ✅ code | NOTICE | Same as above | …/Tau-J/rtmlib/main/LICENSE | Candidate |
| MMDetection | Code | Apache-2.0 | — | ✅ | NOTICE | — | …/open-mmlab/mmdetection/main/LICENSE | Candidate |
| SlowFast / X3D | Code | Apache-2.0 | Kinetics-trained | ✅ code; ⚠️ weights | — | Kinetics (YouTube) | …/facebookresearch/SlowFast/main/LICENSE | Candidate (P2) |
| Video Swin | Code | Apache-2.0 | ImageNet/Kinetics | ✅ code; ⚠️ | — | — | …/SwinTransformer/Video-Swin-Transformer/master/LICENSE | Research |
| YOLOX | Code | Apache-2.0 | COCO | ✅; ⚠️ weights | — | — | …/Megvii-BaseDetection/YOLOX/main/LICENSE | Candidate |
| RT-DETR | Code | Apache-2.0 | COCO/Objects365 | ✅; ⚠️ | — | Objects365 terms | …/lyuwenyu/RT-DETR/main/LICENSE | Candidate |
| RF-DETR | Code | Apache-2.0 (develop branch) | Per-checkpoint | ✅; verify per model | — | Some variants may differ | …/roboflow/rf-detr/develop/LICENSE | Candidate |
| ByteTrack | Code | MIT | — | ✅ | Copyright notice | — | …/ifzhang/ByteTrack/main/LICENSE | Candidate |
| BoT-SORT | Code | MIT | — | ✅ | — | — | …/NirAharon/BoT-SORT/main/LICENSE | Alternative |
| DWPose | Code | Apache-2.0 | — | ✅; ⚠️ | — | — | …/IDEA-Research/DWPose/onnx/LICENSE | Alternative |
| ViTPose | Code | Apache-2.0 | — | ✅; ⚠️ | — | — | …/ViTAE-Transformer/ViTPose/main/LICENSE | Offline labelling |
| VadCLIP | Code | Apache-2.0 | Trained on UCF-Crime/XD-Violence | ✅ code; ❌ weights for product | — | Retraining required | …/nwpu-zxr/VadCLIP/main/LICENSE | Later |
| Qwen3-VL 2B/4B/8B/30B-A3B | Weights | Apache-2.0 | Apache-2.0 | ✅ | License notice | — | huggingface.co/api/models/Qwen/Qwen3-VL-8B-Instruct | Candidate (P2) |
| Qwen2.5-VL-7B | Weights | Apache-2.0 | Apache-2.0 | ✅ | — | — | HF API | Alternative |
| InternVL3-8B / SmolVLM2 | Weights | Apache-2.0 | — | ✅ | — | — | HF API | Alternative |
| Phi-4-multimodal | Weights | MIT | — | ✅ | — | — | HF API | Alternative |
| SAM 2 | Weights | Apache-2.0 | — | ✅ | — | — | HF API | Annotation tooling |
| zeenolife/ai-baby-monitor | Code | MIT | — | ✅ (pattern only) | © 2025 Almaz Zinollayev | — | raw…/zeenolife/ai-baby-monitor/master/LICENSE | Not used |
| AIR-400 code | Code | MIT | **Data: ACLab non-commercial, "not … for incorporation into a product"** | ✅ code; ❌ data; checkpoints ⚠️ | — | Checkpoint terms | raw…/michaelwwan/air-400/main/README.md ; ostadabbas.sites.northeastern.edu/datasets | Research only |
| ONNX Runtime | Runtime | MIT | — | ✅ | — | — | PyPI | Candidate |
| PyAV (`av`) | Library (vision-worker) | BSD-3-Clause | Binary wheels bundle FFmpeg libraries (LGPL; check the build's codec set for GPL parts) | ✅ with LGPL obligations for the bundled FFmpeg | Notices | Confirm the wheel's FFmpeg build configuration before shipping (OQ-L10) | PyPI | **In use** |
| NumPy | Library | BSD-3-Clause | — | ✅ | Notice | — | PyPI | **In use** |
| MediaMTX | Test tool (RTSP server) | MIT | — | ✅ (tests only, not shipped) | — | Release checksum file was not found at the expected URL, so the binary is unverified | github.com/bluenviron/mediamtx | Tests only |
| FFmpeg CLI | Test tool (synthetic cameras) | LGPL/GPL depending on build | — | Tests only, not shipped | — | — | Ubuntu package | Tests only |
| TensorRT | Runtime | **NVIDIA proprietary** | — | ✅ under NVIDIA SLA | Per SLA | Redistribution terms (OQ-L4) | PyPI | Candidate |
| DeepStream SDK | Runtime | **NVIDIA proprietary** | — | ✅ under NVIDIA license | Per license | OQ-L4 | docs.nvidia.com | Evaluate |
| vLLM | Runtime | Apache-2.0 | — | ✅ | — | — | — | Candidate (P2) |
| **Ultralytics YOLO** | Code+weights | **AGPL-3.0** | AGPL / enterprise | ❌ unless enterprise license bought | — | — | …/ultralytics/ultralytics/main/LICENSE | **Excluded** |
| **BoxMOT** | Code | **AGPL-3.0** | — | ❌ | — | — | …/mikel-brostrom/boxmot/master/LICENSE | **Excluded** |
| **FiDIP** | Code | **Non-commercial** (README) | SyRIP: ACLab NC | ❌ | — | Commercial license? (OQ-L1) | raw…/ostadabbas/Infant-Pose-Estimation/master/README.md | **Research only** |
| **Infant-Posture-Estimation** | Code | **Non-commercial** | MIMM private | ❌ | — | Patent (US 20240193809) | raw…/ostadabbas/Infant-Posture-Estimation/master/README.md | **Excluded** |
| **InfAct / InfActPrimitive** | Code+data | Could not retrieve | ACLab NC (assumed) | ❌ assumed | — | Verify | CVPRW 2023 paper ; arXiv:2311.12300 | **Excluded until verified** |
| **SyRIP, AIR-125/400, InfAnFace, NNS** | Data | — | **ACLab Non-commercial** | ❌ | — | — | ostadabbas.sites.northeastern.edu/datasets | **Research only** |
| **VideoMAE** | Code | **CC BY-NC 4.0** | — | ❌ | — | — | …/MCG-NJU/VideoMAE/main/LICENSE | **Excluded** |
| **Sapiens** | Code+weights | **CC BY-NC 4.0** | — | ❌ | — | — | …/facebookresearch/sapiens/main/LICENSE | **Excluded** |
| **Qwen2.5-VL-3B** | Weights | — | **Qwen Research License** | ❌ | — | — | huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct | **Excluded** |
| Qwen2.5-VL-72B | Weights | — | Qwen license ("other") | ⚠️ review | — | — | HF API | Not planned |
| Gemma 3 | Weights | — | Gemma terms | ⚠️ review | — | — | HF API | Not planned |
| **CoReVAD** | Code | **No LICENSE file** (all rights reserved) | — | ❌ | — | — | github.com/Muk-00/CoReVAD | **Excluded** |
| DSANet | — | No code found | — | ❌ | — | — | arXiv:2511.10334 | Monitor |
| ICVD | Data | — | Not stated | [U] | — | OQ-L5 | arXiv:2608.23838 ; infantcaredataset.org | Enquire |
| UCF-Crime / XD-Violence / RWF-2000 / NTU RGB+D | Data | — | Research agreements | ❌ for product | — | — | Dataset sites | Not used |
| COCO | Data | — | Annotations CC-BY 4.0; images Flickr terms | ⚠️ | Attribution | OQ-L3 | cocodataset.org | Via pretrained weights |
| Kinetics | Data | — | YouTube-derived | ⚠️ | — | OQ-L3 | — | Via pretrained weights |
| OpenPose | Code | Non-commercial (per CMU license; verify) | — | ❌ | — | — | CMU | **Excluded** |
| MinIO | Code | AGPL-3.0 | — | ⚠️ | — | — | — | Not used (local FS instead) |
