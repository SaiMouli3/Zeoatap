## Environment

- **gpu**: NVIDIA RTX PRO 4500 Blackwell Server Edition
- **gpu_mem_total_mib**: 32623.0
- **driver**: 595.91.07
- **driver_cuda**: 13.2
- **onnxruntime**: 1.31.1
- **tensorrt**: 10.16.1.11
- **torch**: 2.14.1+cu130
- **torch_cuda**: 13.0
- **cudnn**: 92400
- **python**: 3.12.16
- **kernel**: 6.8.0-1065-aws
- **warmup**: 50
- **iters**: 500
- **batches**: [1, 4, 8, 16]

## Latency tables (median and p95 over 500 timed runs after 50 warm-up runs)

Per-item = batch latency / batch size (detector & RTMO: per frame; RTMPose: per person crop). Memory = GPU memory of the benchmark process as reported by NVML (includes CUDA context, runtime arena / TensorRT workspace).

### rtmpose-s

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 1.276 | 1.289 | 1.276 | 1.289 | 784 | 274 |
| ORT CUDA FP32 | 4 | 1.174 | 1.267 | 0.294 | 0.317 | 3407 | 276 |
| ORT CUDA FP32 | 8 | 1.422 | 1.430 | 0.178 | 0.179 | 5626 | 280 |
| ORT CUDA FP32 | 16 | 2.022 | 2.028 | 0.126 | 0.127 | 7915 | 288 |
| TensorRT FP16 | 1 | 0.421 | 0.434 | 0.421 | 0.434 | 2375 | 330 |
| TensorRT FP16 | 4 | 0.413 | 0.417 | 0.103 | 0.104 | 9687 | 348 |
| TensorRT FP16 | 8 | 0.488 | 0.493 | 0.061 | 0.062 | 16393 | 348 |
| TensorRT FP16 | 16 | 0.788 | 0.796 | 0.049 | 0.050 | 20313 | 348 |

### rtmpose-m

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 1.769 | 1.793 | 1.769 | 1.793 | 565 | 274 |
| ORT CUDA FP32 | 4 | 1.875 | 1.883 | 0.469 | 0.471 | 2134 | 276 |
| ORT CUDA FP32 | 8 | 2.557 | 2.566 | 0.320 | 0.321 | 3129 | 282 |
| ORT CUDA FP32 | 16 | 3.947 | 3.960 | 0.247 | 0.248 | 4053 | 290 |
| TensorRT FP16 | 1 | 0.596 | 0.608 | 0.596 | 0.608 | 1679 | 330 |
| TensorRT FP16 | 4 | 0.694 | 0.700 | 0.174 | 0.175 | 5761 | 350 |
| TensorRT FP16 | 8 | 0.897 | 0.903 | 0.112 | 0.113 | 8920 | 350 |
| TensorRT FP16 | 16 | 1.609 | 1.616 | 0.101 | 0.101 | 9942 | 350 |

### rtmo-s

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 3.017 | 3.060 | 3.017 | 3.060 | 331 | 282 |
| ORT CUDA FP32 | 4 | 5.981 | 6.001 | 1.495 | 1.500 | 669 | 306 |
| ORT CUDA FP32 | 8 | 13.476 | 13.538 | 1.684 | 1.692 | 594 | 338 |
| ORT CUDA FP32 | 16 | 24.875 | 24.938 | 1.555 | 1.559 | 643 | 402 |
| TensorRT FP16 | 1 | 1.383 | 1.406 | 1.383 | 1.406 | 723 | 412 |
| TensorRT FP16 | 4 | 2.082 | 2.106 | 0.521 | 0.527 | 1921 | 410 |
| TensorRT FP16 | 8 | 3.546 | 3.563 | 0.443 | 0.445 | 2256 | 428 |
| TensorRT FP16 | 16 | 7.032 | 7.057 | 0.440 | 0.441 | 2275 | 466 |

### rtmo-m

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 4.307 | 4.343 | 4.307 | 4.343 | 232 | 282 |
| ORT CUDA FP32 | 4 | 11.285 | 11.325 | 2.821 | 2.831 | 354 | 306 |
| ORT CUDA FP32 | 8 | 23.274 | 23.370 | 2.909 | 2.921 | 344 | 338 |
| ORT CUDA FP32 | 16 | 49.329 | 49.479 | 3.083 | 3.092 | 324 | 402 |
| TensorRT FP16 | 1 | 1.590 | 1.611 | 1.590 | 1.611 | 629 | 410 |
| TensorRT FP16 | 4 | 3.704 | 3.720 | 0.926 | 0.930 | 1080 | 410 |
| TensorRT FP16 | 8 | 7.087 | 7.111 | 0.886 | 0.889 | 1129 | 428 |
| TensorRT FP16 | 16 | 14.632 | 14.705 | 0.915 | 0.919 | 1093 | 466 |

### yolox-s_640

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 1.910 | 1.924 | 1.910 | 1.924 | 524 | 280 |
| ORT CUDA FP32 | 4 | 5.536 | 5.546 | 1.384 | 1.386 | 723 | 304 |
| ORT CUDA FP32 | 8 | 13.209 | 13.234 | 1.651 | 1.654 | 606 | 336 |
| ORT CUDA FP32 | 16 | 23.639 | 23.684 | 1.477 | 1.480 | 677 | 400 |
| TensorRT FP16 | 1 | 0.769 | 0.773 | 0.769 | 0.773 | 1300 | 348 |
| TensorRT FP16 | 4 | 1.808 | 1.814 | 0.452 | 0.453 | 2212 | 360 |
| TensorRT FP16 | 8 | 3.324 | 3.332 | 0.415 | 0.416 | 2407 | 388 |
| TensorRT FP16 | 16 | 7.015 | 7.029 | 0.438 | 0.439 | 2281 | 448 |

### yolox-s_1280

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 5.498 | 5.509 | 5.498 | 5.509 | 182 | 304 |
| ORT CUDA FP32 | 4 | 23.453 | 23.506 | 5.863 | 5.876 | 171 | 400 |
| ORT CUDA FP32 | 8 | 53.218 | 53.317 | 6.652 | 6.665 | 150 | 528 |
| ORT CUDA FP32 | 16 | 113.233 | 113.637 | 7.077 | 7.102 | 141 | 784 |
| TensorRT FP16 | 1 | 1.807 | 1.814 | 1.807 | 1.814 | 553 | 360 |
| TensorRT FP16 | 4 | 6.953 | 6.964 | 1.738 | 1.741 | 575 | 448 |
| TensorRT FP16 | 8 | 14.823 | 14.857 | 1.853 | 1.857 | 540 | 566 |
| TensorRT FP16 | 16 | 32.052 | 32.143 | 2.003 | 2.009 | 499 | 804 |

### yolox-m_640

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 3.765 | 3.775 | 3.765 | 3.775 | 266 | 280 |
| ORT CUDA FP32 | 4 | 12.182 | 12.217 | 3.046 | 3.054 | 328 | 304 |
| ORT CUDA FP32 | 8 | 25.613 | 25.687 | 3.202 | 3.211 | 312 | 336 |
| ORT CUDA FP32 | 16 | 55.037 | 57.035 | 3.440 | 3.565 | 291 | 400 |
| TensorRT FP16 | 1 | 1.961 | 1.968 | 1.961 | 1.968 | 510 | 348 |
| TensorRT FP16 | 4 | 4.031 | 4.040 | 1.008 | 1.010 | 992 | 360 |
| TensorRT FP16 | 8 | 7.683 | 7.703 | 0.960 | 0.963 | 1041 | 388 |
| TensorRT FP16 | 16 | 16.441 | 16.484 | 1.028 | 1.030 | 973 | 448 |

### yolox-m_1280

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 12.785 | 12.808 | 12.785 | 12.808 | 78 | 304 |
| ORT CUDA FP32 | 4 | 55.654 | 58.110 | 13.914 | 14.527 | 72 | 400 |
| ORT CUDA FP32 | 8 | 123.163 | 130.294 | 15.395 | 16.287 | 65 | 528 |
| ORT CUDA FP32 | 16 | 259.525 | 269.795 | 16.220 | 16.862 | 62 | 784 |
| TensorRT FP16 | 1 | 4.258 | 4.267 | 4.258 | 4.267 | 235 | 360 |
| TensorRT FP16 | 4 | 16.445 | 16.490 | 4.111 | 4.123 | 243 | 448 |
| TensorRT FP16 | 8 | 35.447 | 35.532 | 4.431 | 4.442 | 226 | 566 |
| TensorRT FP16 | 16 | 75.049 | 75.489 | 4.691 | 4.718 | 213 | 804 |

### rtdetr-r18_640

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 5.056 | 5.080 | 5.056 | 5.080 | 198 | 282 |
| ORT CUDA FP32 | 4 | 13.858 | 13.899 | 3.464 | 3.475 | 289 | 306 |
| ORT CUDA FP32 | 8 | 29.230 | 29.991 | 3.654 | 3.749 | 274 | 338 |
| ORT CUDA FP32 | 16 | 61.188 | 62.172 | 3.824 | 3.886 | 261 | 402 |
| TensorRT FP16 | 1 | 1.561 | 1.566 | 1.561 | 1.566 | 641 | 402 |
| TensorRT FP16 | 4 | 4.088 | 4.106 | 1.022 | 1.027 | 979 | 402 |
| TensorRT FP16 | 8 | 7.083 | 7.098 | 0.885 | 0.887 | 1129 | 420 |
| TensorRT FP16 | 16 | 15.496 | 15.579 | 0.968 | 0.974 | 1033 | 478 |

### rtdetr-r18_1280

| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |
|---|---|---|---|---|---|---|---|
| ORT CUDA FP32 | 1 | 15.477 | 15.589 | 15.477 | 15.589 | 65 | 306 |
| ORT CUDA FP32 | 4 | 59.980 | 60.408 | 14.995 | 15.102 | 67 | 402 |
| ORT CUDA FP32 | 8 | 120.148 | 125.499 | 15.019 | 15.687 | 67 | 530 |
| ORT CUDA FP32 | 16 | 246.769 | 256.371 | 15.423 | 16.023 | 65 | 786 |
| TensorRT FP16 | 1 | 3.708 | 3.717 | 3.708 | 3.717 | 270 | 406 |
| TensorRT FP16 | 4 | 13.271 | 13.302 | 3.318 | 3.326 | 301 | 458 |
| TensorRT FP16 | 8 | 25.990 | 26.109 | 3.249 | 3.264 | 308 | 532 |
| TensorRT FP16 | 16 | 53.944 | 54.207 | 3.372 | 3.388 | 297 | 702 |


## Capacity (P=3 persons/frame, U=0.6, f_base=5, f_hi=15, p_hi=0.5 → 10 fps/camera average; t values = per-item median at batch 8)

| Backend | Detector | Pose | t_det ms | t_pose ms | GPU ms per camera-second | Cameras per GPU | G (4 cams) | GPUs | G (16 cams) | GPUs | G (64 cams) | GPUs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TRT FP16 | yolox-s_640 | rtmpose-s | 0.415 | 0.061 ×3 | 6.0 | **100** | 24 | 1 | 96 | 1 | 383 | 1 |
| TRT FP16 | yolox-s_640 | rtmpose-m | 0.415 | 0.112 ×3 | 7.5 | **79** | 30 | 1 | 120 | 1 | 481 | 1 |
| TRT FP16 | yolox-s_640 | rtmo-s | 0.415 | 0.443 /frame | 8.6 | **69** | 34 | 1 | 137 | 1 | 550 | 1 |
| TRT FP16 | yolox-s_640 | rtmo-m | 0.415 | 0.886 /frame | 13.0 | **46** | 52 | 1 | 208 | 1 | 833 | 2 |
| TRT FP16 | yolox-s_1280 | rtmpose-s | 1.853 | 0.061 ×3 | 20.4 | **29** | 81 | 1 | 326 | 1 | 1303 | 3 |
| TRT FP16 | yolox-s_1280 | rtmpose-m | 1.853 | 0.112 ×3 | 21.9 | **27** | 88 | 1 | 350 | 1 | 1401 | 3 |
| TRT FP16 | yolox-s_1280 | rtmo-s | 1.853 | 0.443 /frame | 23.0 | **26** | 92 | 1 | 367 | 1 | 1469 | 3 |
| TRT FP16 | yolox-s_1280 | rtmo-m | 1.853 | 0.886 /frame | 27.4 | **21** | 110 | 1 | 438 | 1 | 1753 | 3 |
| TRT FP16 | yolox-m_640 | rtmpose-s | 0.960 | 0.061 ×3 | 11.4 | **52** | 46 | 1 | 183 | 1 | 732 | 2 |
| TRT FP16 | yolox-m_640 | rtmpose-m | 0.960 | 0.112 ×3 | 13.0 | **46** | 52 | 1 | 207 | 1 | 830 | 2 |
| TRT FP16 | yolox-m_640 | rtmo-s | 0.960 | 0.443 /frame | 14.0 | **42** | 56 | 1 | 225 | 1 | 898 | 2 |
| TRT FP16 | yolox-m_640 | rtmo-m | 0.960 | 0.886 /frame | 18.5 | **32** | 74 | 1 | 295 | 1 | 1182 | 2 |
| TRT FP16 | yolox-m_1280 | rtmpose-s | 4.431 | 0.061 ×3 | 46.1 | **13** | 185 | 1 | 738 | 2 | 2953 | 5 |
| TRT FP16 | yolox-m_1280 | rtmpose-m | 4.431 | 0.112 ×3 | 47.7 | **12** | 191 | 1 | 763 | 2 | 3051 | 6 |
| TRT FP16 | yolox-m_1280 | rtmo-s | 4.431 | 0.443 /frame | 48.7 | **12** | 195 | 1 | 780 | 2 | 3119 | 6 |
| TRT FP16 | yolox-m_1280 | rtmo-m | 4.431 | 0.886 /frame | 53.2 | **11** | 213 | 1 | 851 | 2 | 3403 | 6 |
| TRT FP16 | rtdetr-r18_640 | rtmpose-s | 0.885 | 0.061 ×3 | 10.7 | **56** | 43 | 1 | 171 | 1 | 684 | 2 |
| TRT FP16 | rtdetr-r18_640 | rtmpose-m | 0.885 | 0.112 ×3 | 12.2 | **49** | 49 | 1 | 195 | 1 | 782 | 2 |
| TRT FP16 | rtdetr-r18_640 | rtmo-s | 0.885 | 0.443 /frame | 13.3 | **45** | 53 | 1 | 213 | 1 | 850 | 2 |
| TRT FP16 | rtdetr-r18_640 | rtmo-m | 0.885 | 0.886 /frame | 17.7 | **33** | 71 | 1 | 283 | 1 | 1134 | 2 |
| TRT FP16 | rtdetr-r18_1280 | rtmpose-s | 3.249 | 0.061 ×3 | 34.3 | **17** | 137 | 1 | 549 | 1 | 2196 | 4 |
| TRT FP16 | rtdetr-r18_1280 | rtmpose-m | 3.249 | 0.112 ×3 | 35.9 | **16** | 143 | 1 | 574 | 1 | 2294 | 4 |
| TRT FP16 | rtdetr-r18_1280 | rtmo-s | 3.249 | 0.443 /frame | 36.9 | **16** | 148 | 1 | 591 | 1 | 2363 | 4 |
| TRT FP16 | rtdetr-r18_1280 | rtmo-m | 3.249 | 0.886 /frame | 41.3 | **14** | 165 | 1 | 662 | 2 | 2646 | 5 |
| ORT FP32 | yolox-s_640 | rtmpose-s | 1.651 | 0.178 ×3 | 21.8 | **27** | 87 | 1 | 350 | 1 | 1398 | 3 |
| ORT FP32 | yolox-s_640 | rtmpose-m | 1.651 | 0.320 ×3 | 26.1 | **22** | 104 | 1 | 418 | 1 | 1670 | 3 |
| ORT FP32 | yolox-s_640 | rtmo-s | 1.651 | 1.684 /frame | 33.4 | **17** | 133 | 1 | 534 | 1 | 2135 | 4 |
| ORT FP32 | yolox-s_640 | rtmo-m | 1.651 | 2.909 /frame | 45.6 | **13** | 182 | 1 | 730 | 2 | 2919 | 5 |
| ORT FP32 | yolox-s_1280 | rtmpose-s | 6.652 | 0.178 ×3 | 71.9 | **8** | 287 | 1 | 1150 | 2 | 4599 | 8 |
| ORT FP32 | yolox-s_1280 | rtmpose-m | 6.652 | 0.320 ×3 | 76.1 | **7** | 304 | 1 | 1218 | 3 | 4871 | 9 |
| ORT FP32 | yolox-s_1280 | rtmo-s | 6.652 | 1.684 /frame | 83.4 | **7** | 333 | 1 | 1334 | 3 | 5336 | 9 |
| ORT FP32 | yolox-s_1280 | rtmo-m | 6.652 | 2.909 /frame | 95.6 | **6** | 382 | 1 | 1530 | 3 | 6119 | 11 |
| ORT FP32 | yolox-m_640 | rtmpose-s | 3.202 | 0.178 ×3 | 37.3 | **16** | 149 | 1 | 598 | 1 | 2390 | 4 |
| ORT FP32 | yolox-m_640 | rtmpose-m | 3.202 | 0.320 ×3 | 41.6 | **14** | 166 | 1 | 666 | 2 | 2663 | 5 |
| ORT FP32 | yolox-m_640 | rtmo-s | 3.202 | 1.684 /frame | 48.9 | **12** | 195 | 1 | 782 | 2 | 3127 | 6 |
| ORT FP32 | yolox-m_640 | rtmo-m | 3.202 | 2.909 /frame | 61.1 | **9** | 244 | 1 | 978 | 2 | 3911 | 7 |
| ORT FP32 | yolox-m_1280 | rtmpose-s | 15.395 | 0.178 ×3 | 159.3 | **3** | 637 | 2 | 2549 | 5 | 10194 | 17 |
| ORT FP32 | yolox-m_1280 | rtmpose-m | 15.395 | 0.320 ×3 | 163.5 | **3** | 654 | 2 | 2617 | 5 | 10467 | 18 |
| ORT FP32 | yolox-m_1280 | rtmo-s | 15.395 | 1.684 /frame | 170.8 | **3** | 683 | 2 | 2733 | 5 | 10931 | 19 |
| ORT FP32 | yolox-m_1280 | rtmo-m | 15.395 | 2.909 /frame | 183.0 | **3** | 732 | 2 | 2929 | 5 | 11715 | 20 |
| ORT FP32 | rtdetr-r18_640 | rtmpose-s | 3.654 | 0.178 ×3 | 41.9 | **14** | 167 | 1 | 670 | 2 | 2680 | 5 |
| ORT FP32 | rtdetr-r18_640 | rtmpose-m | 3.654 | 0.320 ×3 | 46.1 | **13** | 185 | 1 | 738 | 2 | 2952 | 5 |
| ORT FP32 | rtdetr-r18_640 | rtmo-s | 3.654 | 1.684 /frame | 53.4 | **11** | 214 | 1 | 854 | 2 | 3416 | 6 |
| ORT FP32 | rtdetr-r18_640 | rtmo-m | 3.654 | 2.909 /frame | 65.6 | **9** | 263 | 1 | 1050 | 2 | 4200 | 8 |
| ORT FP32 | rtdetr-r18_1280 | rtmpose-s | 15.019 | 0.178 ×3 | 155.5 | **3** | 622 | 2 | 2488 | 5 | 9953 | 17 |
| ORT FP32 | rtdetr-r18_1280 | rtmpose-m | 15.019 | 0.320 ×3 | 159.8 | **3** | 639 | 2 | 2556 | 5 | 10226 | 18 |
| ORT FP32 | rtdetr-r18_1280 | rtmo-s | 15.019 | 1.684 /frame | 167.0 | **3** | 668 | 2 | 2672 | 5 | 10690 | 18 |
| ORT FP32 | rtdetr-r18_1280 | rtmo-m | 15.019 | 2.909 /frame | 179.3 | **3** | 717 | 2 | 2868 | 5 | 11474 | 20 |
