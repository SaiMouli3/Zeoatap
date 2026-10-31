# Models and packages used by the vision-worker

The project owner clears every license below before product use. This file lists what the pose pipeline (`--detector pose`) downloads, exports and runs, with sources and hashes, so that can be done.

> **Every model here is a public pretrained model. None is trained or validated on CCTV of a childcare room, and the adult models are not validated on infants.** The pipeline output is labelled that way (`model_versions.validation = "UNVALIDATED-…"`). The infant/adult role classifier and the held-state logic are hand-written heuristics, also UNVALIDATED.

Weights are not in git (`services/vision-worker/models/` is git-ignored). `scripts/fetch_models.py` checks the sha256 of each archive and each extracted ONNX against the values pinned in `src/zeoatap_vision/models.py`. `scripts/export_fidip.py` checks the checkpoint sha256 and records the ONNX hash it produced in `models/manifest.json`. The worker checks the ONNX hash again before it loads a model.

## Models

| Model (registry name) | Role | Code license | Weights / training data | Source | Archive / checkpoint sha256 | ONNX sha256 |
|---|---|---|---|---|---|---|
| `yolox-m-humanart` (default) | person detector, 640×640, NMS in the graph | Apache-2.0 (open-mmlab/mmpose, mmdetection; Megvii YOLOX) | COCO + **Human-Art** (IDEA-Research). Human-Art's dataset terms need a separate check. | https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip (mirror: huggingface.co/Tau-J/RTMPose) | `a000224fd8ba283202bc62d4a5fcdfe353adb9f468777dbac1ea2ada2093adde` | `3dea6513388889f0fff4b77bf7a26013600321b9eb9ceb0e9a400a82572f5f23` |
| `yolox-tiny-humanart` | person detector, 416×416 (faster, misses small or far people) | Apache-2.0 | same as above | …/onnx_sdk/yolox_tiny_8xb8-300e_humanart-6f3252f9.zip | `36e09ca555916253fa1b5d51bec01e48e98e1f0af966c51ce3e844d8c8fc4cfc` | `ceb11c07298f95c50d7c5abeb906d03340c85f23aa79e3e66966e7fb6c307250` |
| `rtmpose-m-body7` (default) | top-down pose, 17 COCO keypoints, 256×192 | Apache-2.0 (open-mmlab/mmpose) | **Body7**: COCO, AI Challenger, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18. Each dataset has its own terms, some non-commercial. | …/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip | `f7fbb6c5c11a1bb70f3d445e4ddec5d144ea89ad5649c081ef976f9b24a0b741` | `5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c` |
| `rtmpose-s-body7` | top-down pose, 256×192 (faster) | Apache-2.0 | Body7 (as above) | …/onnx_sdk/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip | `7673922e531014906ca4f0f239b7e233b740146a10b632deaa2a28d45470d802` | `9aeb635b83f86aea45cf45d85798f7eba1a162de8e0d721c44e54fe5eebaf47d` |
| `fidip-mobile` (default infant pose) | FiDIP Pose-MobileNet (MobileNetV2 + DUC), 224×224, 17 COCO keypoints. Runs only on tracks classified as infant. | **"This code is for non-commercial purpose only"** (FiDIP README). Model files carry MIT headers from Microsoft. **Used at the owner's direction. A license must be obtained before sale.** | SyRIP (real and synthetic infant images, home photos), fine-tuned from COCO. Reported 79.2 AP on SyRIP Test100. Not validated on CCTV. | https://github.com/ostadabbas/Infant-Pose-Estimation (commit `a76b4f418107b8d3548d9779e94702e2508fbef1`). Weights: public Google Drive folder https://drive.google.com/drive/folders/108P-1SnTqaj3xNtjYZ1o7T8z6UvUYuiC (`mobile_fidip.pth`, drive id `1aHaBwO3XBYtFXI7cDMRIzMWRffIJzrQU`) | `bd0b7548b1f1158f0a8a97d022305547343c835e7d4027727a978d2d7a4cbca1` | `4b00aa6e21596542f90598550f146a3f8aeca796d7df986ac29939ed59b9d194` (torch 2.x CPU, opset 17; a re-export can differ, and the manifest records whatever was produced) |
| `fidip-hrnet-w48` | FiDIP DarkPose HRNet-W48, 384×288 (GPU option; about 0.36 s per crop on this CPU) | same as above | SyRIP. Reported 93.6 AP on SyRIP Test100. | same folder (`hrnet_fidip.pth`, drive id `19tBMoVS8wTza7VqVfPc6KBQ_POPAEIqb`) | `bfbf9df225674614652d5dbdd066daf71c9b9d7f3c31976b7ed33bb96af55d07` | `1ced16b30af50d905cd94b6899de0e3534d24087a3f77d7ba2f89543577ee4bc` |

Not obtained: the newer FiDIP model trained on the expanded SyRIP set (https://drive.google.com/file/d/1UHQC63mSEL4Vcuenqg9R3OaNP0Wv_6gd). Google Drive returned a sign-in page, so the file is not publicly shared.

FiDIP notes:
- The checkpoints contain only the pose network. The domain classifier from FiDIP's adaptive training is not included. They load with `strict=True`.
- The model definitions needed for export are vendored in `scripts/fidip/`. `scripts/fidip/README.txt` lists the provenance and the two small changes.
- Decoding is heatmap argmax plus the quarter-pixel shift. DarkPose's distribution-aware refinement is not implemented.
- Confidence for HRNet is the heatmap peak. Pose-MobileNet outputs flat-softmax heatmaps, so its confidence is the probability mass in the 3×3 window around the peak.
- Sanity check on adults (`tests/test_detection_smoke.py`): HRNet-FiDIP keypoints are within 5% of box height of RTMPose's. This checks the export and the decoding. It is not an accuracy result.

## Test media

| File | Content | License | Source | sha256 |
|---|---|---|---|---|
| `models/test_adults_nasa_crew1.jpg` | still photo of **adults only**: the SpaceX Crew-1 walkout, 4 astronauts and staff | NASA media, public domain in the US. Identifiable people must not be used to imply endorsement, so it is used for tests only. | https://images.nasa.gov/details/NHQ202011150013 (file: `https://images-assets.nasa.gov/image/NHQ202011150013/NHQ202011150013~medium.jpg`) | `0ce236d6e9b007d509daefdcb53eece58fa79bbc37c7b9a3bb73ab6eb0d77ed8` |

No footage of children, and no footage at all, is used.

## Python packages (runtime extra `pose`; versions as locked in `uv.lock`)

| Package | Version | License | Source |
|---|---|---|---|
| onnxruntime | 1.31.1 | MIT | https://github.com/microsoft/onnxruntime |
| rtmlib | 0.0.16 | Apache-2.0 | https://github.com/Tau-J/rtmlib (pre/post-processing helpers for RTMPose) |
| opencv-python, opencv-contrib-python | 5.0.0.93 | Apache-2.0 (OpenCV 4.5+). The wheels bundle FFmpeg (LGPL) and Qt (LGPL). | https://github.com/opencv/opencv-python (pulled in by rtmlib) |
| numpy | 2.x | BSD-3-Clause | https://numpy.org |
| av (PyAV) | 15.x | BSD-3-Clause. The wheels bundle FFmpeg (LGPL/GPL build options need a check). | https://github.com/PyAV-Org/PyAV |
| protobuf, flatbuffers, tqdm | locked | BSD-3 / Apache-2.0 / MPL-2.0+MIT | transitive (onnxruntime, rtmlib) |

Export-time only (not installed in the worker): torch (BSD-3), onnx (Apache-2.0), onnxscript (MIT), gdown (MIT).

## Not used

- **Ultralytics YOLO**: AGPL-3.0, and it would need torch at runtime. The YOLOX/RTMPose ONNX models already run on ONNX Runtime with TensorRT, CUDA or CPU.
- **OWLv2 and Grounding DINO** (open-vocabulary "baby"/"adult"): not tried. These are large ViT/Swin models (OWLv2-B/16 runs at 960×960 input), and the estimate, not measured, is about 1 s or more per frame on this 4-vCPU CPU. That is too slow for a per-frame role classifier. They are a candidate for offline labelling or a GPU role verifier later.
