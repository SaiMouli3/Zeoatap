# GPU benchmark harness (Phase 1, task 1.9)

Measures **inference speed only** for the candidate detector and pose models, to size how many
cameras one GPU can serve. It says **nothing about detection or pose accuracy**. These are
COCO/Body7-pretrained public weights, not trained or validated Zeoatap models.

Results: [`docs/BENCHMARK_RESULTS.md`](../../../docs/BENCHMARK_RESULTS.md).

## Models (all Apache-2.0 code; see `models/manifest.json` after fetching)
| Model | Role | Source |
|---|---|---|
| YOLOX-S, YOLOX-M | detector, 640 and 1280 input | Megvii-BaseDetection/YOLOX release 0.1.1rc0 weights, exported here to ONNX |
| RT-DETR-R18 | detector, 640 and 1280 input | `PekingU/rtdetr_r18vd` (Hugging Face, apache-2.0), exported via `transformers` |
| RTMPose-s, RTMPose-m | top-down pose, 256×192 person crop | MMPose ONNX SDK files, downloaded with rtmlib |
| RTMO-s, RTMO-m | one-stage pose, 640×640 frame | MMPose ONNX SDK files, downloaded with rtmlib |

Excluded by `docs/LICENSING_REGISTER.md`: Ultralytics YOLO, BoxMOT, VideoMAE, Sapiens, FiDIP and any non-commercial/AGPL model.

## Inputs
Seeded random tensors only. No images, video or footage of any kind are used or needed.

## Run (Linux, NVIDIA driver supporting CUDA 13)
```bash
uv sync                                      # pinned by uv.lock (CUDA 13 wheels)
uv run python fetch_models.py                # downloads + exports ONNX, writes models/manifest.json
uv run python bench.py all                   # 10 models × {ORT, TRT} × batch {1,4,8,16} → results/results.jsonl
uv run python report.py > results/RESULTS.md # tables + cameras-per-GPU math
uv run python test_capacity.py               # checks the capacity formula against the doc's worked example
```
`bench.py all` resumes: configs already in `results/results.jsonl` are skipped.

## Method
- **Backends:** ONNX Runtime CUDA EP (FP32 model as exported) and TensorRT FP16 (`BuilderFlag.FP16`, one engine per
  model with an optimization profile for batch 1–16, opt 8). TensorRT is pinned to 10.x because TensorRT 11 removed the
  FP16 builder flag. Exception: RTMO fails to build with a dynamic batch profile (Error Code 10), so it gets one
  fixed-shape engine per batch size with an uncapped workspace (see `STATIC_BATCH_MODELS` in `bench.py`).
- **Timing:** 50 warm-up + 500 timed runs per config, wall-clock around one synchronous forward pass with the input
  already in GPU memory. Reported: median and p95 batch latency, per-item latency (batch / size), items/s.
- **Not timed:** video decode, resize/normalize, host↔device copies, NMS, keypoint decoding, tracking. These need
  their own measurement in the end-to-end soak (task 1.10).
- **Memory:** NVML per-process GPU memory after the timed loop (includes CUDA context and runtime workspace).
- Each config runs in its own process so memory figures and allocator state don't leak between configs.

`models/` and `engines/` are git-ignored (large, machine-specific). The raw measurements are committed in `results/`
(`results.jsonl`, `env.json`, `RESULTS.md`) and summarized in `docs/BENCHMARK_RESULTS.md`. TensorRT engines are GPU- and version-specific and must be rebuilt on the target GPU.
