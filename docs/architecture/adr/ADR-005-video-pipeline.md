# ADR-005: Video pipeline — GStreamer/PyAV + TensorRT first, DeepStream after benchmark
**Status:** Proposed · **Date:** 2026-10-27

## Context
DeepStream offers high camera density but is proprietary, version-locked to JetPack/CUDA/TensorRT (DS 8.0: Jetson Thor yes, Orin not listed; CUDA 13 + Triton issues reported), and harder to debug.

## Decision
Phase 1 builds on GStreamer (or PyAV) with NVDEC decode and TensorRT/ONNX Runtime inference. In parallel, benchmark DeepStream 8/9 on the target GPU. **Adopt DeepStream if it gives ≥1.5× camera density at equal accuracy and its license is acceptable (OQ-L4).**

## Consequences
Simpler first build. Possible migration later. The observation contract insulates core from that change.
