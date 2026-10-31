"""Checks report.py's capacity math against the worked example in docs/COST_AND_DEPLOYMENT.md §3.
Run: uv run python test_capacity.py"""
import math

from report import FPS_AVG, U, cams_per_gpu

per_frame = 2.0 + 3 * 0.8  # doc example: t_det = 2.0 ms, t_pose = 0.8 ms, P = 3
G16 = 16 * FPS_AVG * per_frame
assert FPS_AVG == 10
assert round(G16) == 704  # doc: G = 160 × (2.0 + 3 × 0.8) = 704 ms/s
assert math.ceil(G16 / (1000 * U)) == 2  # doc: ≈1.2 GPUs → 2
assert cams_per_gpu(per_frame) == 13  # 600 / (10 × 4.4) = 13.6
print("ok")
