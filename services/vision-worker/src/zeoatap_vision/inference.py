"""ONNX Runtime wrappers for the person detector (YOLOX, mmdeploy end2end
export with NMS in the graph) and the top-down pose model (RTMPose, SimCC).

Execution providers are picked automatically: TensorRT, then CUDA, then CPU,
using whichever the installed onnxruntime build offers. Pre/post-processing
for RTMPose reuses rtmlib's reference functions so behaviour matches upstream.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

PREFERRED_PROVIDERS = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]


def pick_providers(requested: str = "auto") -> list[str]:
    import onnxruntime as ort

    avail = ort.get_available_providers()
    if requested == "cpu":
        return ["CPUExecutionProvider"]
    chosen = [p for p in PREFERRED_PROVIDERS if p in avail]
    return chosen or ["CPUExecutionProvider"]


def make_session(path: str | Path, providers: list[str], threads: int | None = None):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = threads or (os.cpu_count() or 1)
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    sess = ort.InferenceSession(str(path), sess_options=so, providers=providers)
    log.info("loaded %s with %s", Path(path).name, sess.get_providers()[0])
    return sess


class PersonDetector:
    """YOLOX person detector. Input: RGB uint8 HxWx3. Output: (N,4) xyxy boxes
    in input pixels and (N,) scores, after the in-graph NMS."""

    def __init__(self, onnx_path: str | Path, input_size: tuple[int, int], providers: list[str],
                 score_thr: float = 0.3, threads: int | None = None):
        self.session = make_session(onnx_path, providers, threads)
        self.input_name = self.session.get_inputs()[0].name
        self.w, self.h = input_size
        self.score_thr = score_thr
        self.provider = self.session.get_providers()[0]

    def preprocess(self, rgb: np.ndarray) -> tuple[np.ndarray, float]:
        import cv2

        ih, iw = rgb.shape[:2]
        ratio = min(self.h / ih, self.w / iw)
        nw, nh = int(round(iw * ratio)), int(round(ih * ratio))
        padded = np.full((self.h, self.w, 3), 114, dtype=np.uint8)
        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        padded[:nh, :nw] = resized[:, :, ::-1]  # mmdet YOLOX expects BGR, pad bottom/right
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        return blob, ratio

    def __call__(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        blob, ratio = self.preprocess(rgb)
        dets, labels = self.session.run(None, {self.input_name: blob})
        dets, labels = dets[0], labels[0]
        keep = (dets[:, 4] >= self.score_thr) & (labels == 0)  # class 0 = person
        dets = dets[keep]
        boxes = dets[:, :4] / ratio
        ih, iw = rgb.shape[:2]
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, iw)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, ih)
        ok = (boxes[:, 2] - boxes[:, 0] >= 2) & (boxes[:, 3] - boxes[:, 1] >= 2)
        return boxes[ok].astype(np.float32), dets[ok, 4].astype(np.float32)


class PoseEstimator:
    """RTMPose top-down: one batched forward pass over all person crops.
    Returns keypoints (N,17,2) in input pixels and scores (N,17) in [0,1]
    (COCO-17 order)."""

    MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
    STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

    def __init__(self, onnx_path: str | Path, input_size: tuple[int, int], providers: list[str],
                 threads: int | None = None, max_batch: int = 16):
        self.session = make_session(onnx_path, providers, threads)
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = input_size  # (w, h)
        self.max_batch = max_batch

    def __call__(self, rgb: np.ndarray, boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        from rtmlib.tools.pose_estimation.post_processings import get_simcc_maximum
        from rtmlib.tools.pose_estimation.pre_processings import bbox_xyxy2cs, top_down_affine

        n = len(boxes)
        if n == 0:
            return np.zeros((0, 17, 2), np.float32), np.zeros((0, 17), np.float32)
        boxes = boxes[: self.max_batch]
        crops, centers, scales = [], [], []
        for b in boxes:
            center, scale = bbox_xyxy2cs(np.asarray(b, dtype=np.float32), padding=1.25)
            img, scale = top_down_affine(self.input_size, scale, center, rgb)  # RGB, as the model was trained
            crops.append(((img.astype(np.float32) - self.MEAN) / self.STD).transpose(2, 0, 1))
            centers.append(center)
            scales.append(scale)
        batch = np.ascontiguousarray(np.stack(crops), dtype=np.float32)
        simcc_x, simcc_y = self.session.run(None, {self.input_name: batch})
        locs, scores = get_simcc_maximum(simcc_x, simcc_y)
        kpts = locs / 2.0  # simcc split ratio
        kpts = kpts / np.array(self.input_size, np.float32) * np.stack(scales)[:, None, :] \
            + np.stack(centers)[:, None, :] - np.stack(scales)[:, None, :] / 2
        return kpts.astype(np.float32), np.clip(scores, 0.0, 1.0).astype(np.float32)


def decode_heatmaps(hm: np.ndarray, softmax_output: bool) -> tuple[np.ndarray, np.ndarray]:
    """Heatmaps (N,K,H,W) -> coords in heatmap pixels (N,K,2) and confidence (N,K).

    Argmax + quarter-pixel shift towards the higher neighbour (the classic
    SimpleBaseline/HRNet decoding). Confidence:
      - Gaussian-MSE heatmaps (HRNet): the peak value, clipped to [0,1].
      - flat-softmax heatmaps (FiDIP Pose-MobileNet): the probability mass in
        the 3x3 window around the peak, which is already in [0,1].
    """
    n, k, h, w = hm.shape
    flat = hm.reshape(n, k, -1)
    idx = flat.argmax(-1)
    xs, ys = (idx % w).astype(np.float32), (idx // w).astype(np.float32)
    if softmax_output:
        pad = np.pad(hm, ((0, 0), (0, 0), (1, 1), (1, 1)))
        conf = np.zeros((n, k), np.float32)
        for dy in range(3):
            for dx in range(3):
                conf += np.take_along_axis(
                    pad[:, :, dy:dy + h, dx:dx + w].reshape(n, k, -1), idx[..., None], -1)[..., 0]
    else:
        conf = np.take_along_axis(flat, idx[..., None], -1)[..., 0]
    xi, yi = idx % w, idx // w
    for a in range(n):
        for j in range(k):
            x, y, m = int(xi[a, j]), int(yi[a, j]), hm[a, j]
            if 0 < x < w - 1:
                xs[a, j] += 0.25 * np.sign(m[y, x + 1] - m[y, x - 1])
            if 0 < y < h - 1:
                ys[a, j] += 0.25 * np.sign(m[y + 1, x] - m[y - 1, x])
    return np.stack([xs, ys], -1), np.clip(conf, 0.0, 1.0).astype(np.float32)


class InfantPoseEstimator:
    """FiDIP top-down infant pose (Pose-MobileNet 224x224 or HRNet-W48
    384x288), exported to ONNX by scripts/export_fidip.py. Same interface as
    PoseEstimator: (N,17,2) keypoints in input pixels and (N,17) scores.
    Preprocessing as in FiDIP's test script: RGB, /255, ImageNet mean/std,
    bbox padded 1.25x and warped to the input aspect ratio."""

    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, onnx_path: str | Path, input_size: tuple[int, int], providers: list[str],
                 softmax_output: bool, threads: int | None = None, max_batch: int = 16):
        self.session = make_session(onnx_path, providers, threads)
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = input_size  # (w, h)
        self.softmax_output = softmax_output
        self.max_batch = max_batch

    def __call__(self, rgb: np.ndarray, boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        from rtmlib.tools.pose_estimation.pre_processings import bbox_xyxy2cs, top_down_affine

        if len(boxes) == 0:
            return np.zeros((0, 17, 2), np.float32), np.zeros((0, 17), np.float32)
        boxes = boxes[: self.max_batch]
        crops, centers, scales = [], [], []
        for b in boxes:
            center, scale = bbox_xyxy2cs(np.asarray(b, dtype=np.float32), padding=1.25)
            img, scale = top_down_affine(self.input_size, scale, center, rgb)
            crops.append(((img.astype(np.float32) / 255.0 - self.MEAN) / self.STD).transpose(2, 0, 1))
            centers.append(center)
            scales.append(scale)
        hm = self.session.run(None, {self.input_name: np.ascontiguousarray(np.stack(crops), np.float32)})[0]
        locs, conf = decode_heatmaps(hm, self.softmax_output)
        hh, hw = hm.shape[2:]
        w, h = self.input_size
        locs = locs * np.array([w / hw, h / hh], np.float32)  # heatmap -> model input pixels
        sc, ce = np.stack(scales)[:, None, :], np.stack(centers)[:, None, :]
        kpts = locs / np.array(self.input_size, np.float32) * sc + ce - sc / 2
        return kpts.astype(np.float32), conf
