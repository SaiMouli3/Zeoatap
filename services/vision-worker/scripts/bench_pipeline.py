"""CPU/GPU throughput of the full analysis pipeline (detector + tracker + pose
+ roles + held + observation build) on the adult test photo.

  uv run python scripts/bench_pipeline.py [--frames 30] [--threads N]

Reports frames/s for each detector/pose combination, plus FiDIP per-crop cost
(infant pose only runs on tracks classified as infant). Speed only; says
nothing about accuracy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from test_media import PHOTO  # noqa: E402
from zeoatap_vision.models import MODELS, available, default_model_dir, resolve  # noqa: E402
from zeoatap_vision.pipeline import PosePipeline  # noqa: E402

COMBOS = [("yolox-m-humanart", "rtmpose-m-body7"), ("yolox-tiny-humanart", "rtmpose-s-body7")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--threads", type=int, default=None)
    args = ap.parse_args()
    img = cv2.imread(str(default_model_dir() / PHOTO["file"]))[:, :, ::-1]
    h, w = img.shape[:2]
    rgb = np.ascontiguousarray(cv2.resize(img, (640, round(640 * h / w / 2) * 2)))
    results = []
    for det, pose in COMBOS:
        if not available([det, pose]):
            print(f"skip {det}+{pose}: not fetched")
            continue
        p = PosePipeline(det, pose, threads=args.threads, infant_pose_model=None)
        for i in range(3):
            p.analyze("bench", i * 0.2, None, rgb)
        n_people = len(p.analyze("bench", 0.8, None, rgb))
        p.total_ms_sum, p.frames_total = 0.0, 0
        t0 = time.perf_counter()
        for i in range(args.frames):
            p.analyze("bench", 1.0 + i * 0.2, None, rgb)
        dt = time.perf_counter() - t0
        results.append({"detector": det, "pose": pose, "provider": p.provider, "people": n_people,
                        "fps": round(args.frames / dt, 2), "ms_per_frame": round(dt / args.frames * 1e3, 1),
                        "last_ms": {k: round(v, 1) for k, v in p.last_ms.items()}})
        print(json.dumps(results[-1]))
    from zeoatap_vision.inference import InfantPoseEstimator, pick_providers

    for name in ("fidip-mobile", "fidip-hrnet-w48"):
        try:
            est = InfantPoseEstimator(resolve(name), MODELS[name].input_size, pick_providers(),
                                      softmax_output=name == "fidip-mobile", threads=args.threads)
        except Exception as e:
            print(f"skip {name}: {e}")
            continue
        box = np.array([[250, 130, 330, 340]], np.float32)
        est(rgb, box)
        t0 = time.perf_counter()
        for _ in range(10):
            est(rgb, box)
        print(json.dumps({"infant_pose": name, "ms_per_crop": round((time.perf_counter() - t0) / 10 * 1e3, 1)}))
    print(f"cpu_count={os.cpu_count()} loadavg={os.getloadavg()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
