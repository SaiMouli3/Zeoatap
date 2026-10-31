"""Turn results/results.jsonl into markdown tables + the cameras-per-GPU capacity math.

Capacity uses docs/COST_AND_DEPLOYMENT.md §2:
  F = N × ((1−p_hi)·f_base + p_hi·f_hi)          analyzed frames/s
  G = F × (t_det + P × t_pose) [+ t_win term]     GPU ms per second (t_win = 0: no window classifier in P0)
  GPUs = ceil(G / (1000 × U))
For one-stage pose (RTMO) the pose cost is per frame, not per person: G = F × (t_det + t_rtmo).

  uv run python report.py > results/RESULTS.md
"""
import json
import math
from pathlib import Path

R = Path(__file__).parent / "results"
F_BASE, F_HI, P_HI, P, U = 5, 15, 0.5, 3, 0.6
FPS_AVG = (1 - P_HI) * F_BASE + P_HI * F_HI  # 10 analyzed fps per camera on average
CAP_BATCH = 8  # cross-camera batch used for capacity (latency at b=8 stays far below the 50 ms batching cap)


def load():
    rows = [json.loads(l) for l in (R / "results.jsonl").read_text().splitlines()]
    return {(r["model"], r["backend"], r["batch"]): r for r in rows}


def t(res, model, backend, batch=CAP_BATCH):
    r = res.get((model, backend, batch))
    return None if r is None or "error" in r else r["per_item_ms_median"]


def cams_per_gpu(cost_per_frame_ms):
    return math.floor(1000 * U / (FPS_AVG * cost_per_frame_ms))


def main():
    res = load()
    env = json.loads((R / "env.json").read_text())
    models = list(dict.fromkeys(k[0] for k in res))
    print("## Environment\n")
    for k, v in env.items():
        print(f"- **{k}**: {v}")

    print("\n## Latency tables (median and p95 over", env["iters"], "timed runs after", env["warmup"], "warm-up runs)\n")
    print("Per-item = batch latency / batch size (detector & RTMO: per frame; RTMPose: per person crop). "
          "Memory = GPU memory of the benchmark process as reported by NVML (includes CUDA context, runtime arena / TensorRT workspace).\n")
    for m in models:
        print(f"### {m}\n")
        print("| Backend | Batch | Batch median ms | Batch p95 ms | Per-item median ms | Per-item p95 ms | Items/s | GPU mem MiB |")
        print("|---|---|---|---|---|---|---|---|")
        for be in ("ort", "trt"):
            for b in env["batches"]:
                r = res.get((m, be, b))
                name = "ORT CUDA FP32" if be == "ort" else "TensorRT FP16"
                if r is None:
                    continue
                if "error" in r:
                    print(f"| {name} | {b} | **FAILED** (see results.jsonl) | | | | | |")
                    continue
                print(f"| {name} | {b} | {r['batch_ms_median']:.3f} | {r['batch_ms_p95']:.3f} | {r['per_item_ms_median']:.3f} | "
                      f"{r['per_item_ms_p95']:.3f} | {r['items_per_s']:.0f} | {r['gpu_mem_mib']:.0f} |")
        print()

    errs = [r for r in res.values() if "error" in r]
    if errs:
        print("## Failures\n")
        for r in errs:
            print(f"- {r['model']} / {r['backend']} / batch {r['batch']}:\n\n```\n{r['error'][-1500:]}\n```")

    print(f"\n## Capacity (P={P} persons/frame, U={U}, f_base={F_BASE}, f_hi={F_HI}, p_hi={P_HI} → {FPS_AVG:g} fps/camera average; "
          f"t values = per-item median at batch {CAP_BATCH})\n")
    print("| Backend | Detector | Pose | t_det ms | t_pose ms | GPU ms per camera-second | Cameras per GPU | G (4 cams) | GPUs | G (16 cams) | GPUs | G (64 cams) | GPUs |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for be in ("trt", "ort"):
        for det in [m for m in models if m.startswith(("yolox", "rtdetr"))]:
            for pose in [m for m in models if m.startswith(("rtmpose", "rtmo"))]:
                td, tp = t(res, det, be), t(res, pose, be)
                if td is None or tp is None:
                    continue
                per_frame = td + (P * tp if pose.startswith("rtmpose") else tp)
                cells = []
                for n in (4, 16, 64):
                    g = n * FPS_AVG * per_frame
                    cells += [f"{g:.0f}", str(math.ceil(g / (1000 * U)))]
                tp_s = f"{tp:.3f}" + (" ×3" if pose.startswith("rtmpose") else " /frame")
                print(f"| {'TRT FP16' if be == 'trt' else 'ORT FP32'} | {det} | {pose} | {td:.3f} | {tp_s} | {FPS_AVG * per_frame:.1f} | "
                      f"**{cams_per_gpu(per_frame)}** | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
