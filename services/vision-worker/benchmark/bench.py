"""GPU latency benchmark: ONNX Runtime (CUDA EP, FP32) and TensorRT (FP16) on synthetic inputs.

Measures SPEED ONLY. Inputs are seeded random tensors (no images, no video, no footage).
Timing covers the model forward pass with input already on the GPU (no decode, resize,
host<->device copy, NMS or keypoint decoding).

  uv run python bench.py all                 # every config -> results/results.jsonl + results/env.json
  uv run python bench.py one MODEL BACKEND B # single config, prints one JSON line (used by `all`)
"""
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
MODELS, ENGINES, RESULTS = ROOT / "models", ROOT / "engines", ROOT / "results"
BATCHES = (1, 4, 8, 16)
WARMUP, ITERS = 50, 500
# RTMO's in-graph post-processing (Where…keypoints foreign node) fails to build with a dynamic batch profile in
# TensorRT 10.16: its only tactic requests more than the whole workspace cap (4.3 GB at a 4 GB cap, 12.9 GB at 12 GB;
# Error Code 10 "Could not find any implementation"). Static engines with a 4 GB cap fail too for batch ≥4.
# What builds: one fixed-shape engine per batch size with the workspace left uncapped.
STATIC_BATCH_MODELS = ("rtmo",)


def input_shape(model: str, batch: int):
    meta = json.loads((MODELS / "manifest.json").read_text())[model]
    (name, dims), = meta["inputs"].items()
    return name, (batch, *dims[1:])


def proc_gpu_mem_mib() -> float:
    import pynvml
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h):
        if p.pid == os.getpid():
            return p.usedGpuMemory / 2**20
    return float("nan")


def time_loop(step):
    for _ in range(WARMUP):
        step()
    t = np.empty(ITERS)
    for i in range(ITERS):
        t0 = time.perf_counter()
        step()
        t[i] = (time.perf_counter() - t0) * 1e3
    return t


def run_ort(model, batch):
    import onnxruntime as ort
    ort.preload_dlls()
    sess = ort.InferenceSession(str(MODELS / f"{model}.onnx"), providers=["CUDAExecutionProvider"])
    assert sess.get_providers()[0] == "CUDAExecutionProvider", sess.get_providers()
    name, shape = input_shape(model, batch)
    x = ort.OrtValue.ortvalue_from_numpy(np.random.default_rng(0).random(shape, dtype=np.float32), "cuda", 0)
    b = sess.io_binding()
    b.bind_ortvalue_input(name, x)
    for o in sess.get_outputs():
        b.bind_output(o.name, "cuda")

    def step():
        sess.run_with_iobinding(b)
        b.synchronize_outputs()
    return time_loop(step), {}


def build_engine(model, static_batch=None):
    """One FP16 engine per model, optimization profile batch 1..16 (opt 8).
    static_batch: build a fixed-shape engine instead (used for RTMO, see STATIC_BATCH_MODELS)."""
    import tensorrt as trt
    plan = ENGINES / (f"{model}.b{static_batch}.fp16.plan" if static_batch else f"{model}.fp16.plan")
    if plan.exists():
        return plan, None
    ENGINES.mkdir(exist_ok=True)
    log = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(log)
    net = builder.create_network(0)
    parser = trt.OnnxParser(net, log)
    if not parser.parse_from_file(str(MODELS / f"{model}.onnx")):
        raise RuntimeError("; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    cfg = builder.create_builder_config()
    cfg.set_flag(trt.BuilderFlag.FP16)
    if not static_batch:  # RTMO: leave the workspace uncapped (TensorRT default = device memory), see STATIC_BATCH_MODELS
        cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 4 << 30)
    name = input_shape(model, 1)[0]
    lo, opt, hi = (static_batch,) * 3 if static_batch else (1, 8, max(BATCHES))
    prof = builder.create_optimization_profile()
    prof.set_shape(name, input_shape(model, lo)[1], input_shape(model, opt)[1], input_shape(model, hi)[1])
    cfg.add_optimization_profile(prof)
    t0 = time.time()
    ser = builder.build_serialized_network(net, cfg)
    if ser is None:
        raise RuntimeError("TensorRT engine build failed")
    plan.write_bytes(bytes(ser))
    return plan, time.time() - t0


def run_trt(model, batch):
    import tensorrt as trt
    import torch
    plan, build_s = build_engine(model, batch if model.startswith(STATIC_BATCH_MODELS) else None)
    eng = trt.Runtime(trt.Logger(trt.Logger.WARNING)).deserialize_cuda_engine(plan.read_bytes())
    ctx = eng.create_execution_context()
    name, shape = input_shape(model, batch)
    ctx.set_input_shape(name, shape)
    torch.manual_seed(0)
    bufs = {}
    for i in range(eng.num_io_tensors):
        n = eng.get_tensor_name(i)
        dt = {trt.float32: torch.float32, trt.float16: torch.float16, trt.int32: torch.int32,
              trt.int64: torch.int64, trt.bool: torch.bool}[eng.get_tensor_dtype(n)]
        oshape = tuple(ctx.get_tensor_shape(n))
        if n != name and -1 in oshape:
            bufs[n] = None  # data-dependent shape: allocated by Grow below
            continue
        bufs[n] = (torch.rand(*shape, device="cuda") if n == name
                   else torch.empty(oshape, dtype=dt, device="cuda"))
        ctx.set_tensor_address(n, bufs[n].data_ptr())
    stream = torch.cuda.Stream()

    class Grow(trt.IOutputAllocator):  # outputs with data-dependent shapes (RTMO's detection count)
        def __init__(self):
            super().__init__()
            self.buf = None

        def reallocate_output_async(self, name, memory, size, alignment, stream):
            if self.buf is None or self.buf.numel() < size:
                self.buf = torch.empty(max(size, 1), dtype=torch.uint8, device="cuda")
            return self.buf.data_ptr()

        def notify_shape(self, name, shape):
            pass

    allocs = {n: Grow() for n in list(bufs) if n not in (name,) and bufs[n] is None}
    for n, a in allocs.items():
        ctx.set_output_allocator(n, a)

    def step():
        ctx.execute_async_v3(stream.cuda_stream)
        stream.synchronize()
    return time_loop(step), {"engine_build_s": build_s, "trt_engine_bytes": plan.stat().st_size}


def one(model, backend, batch):
    t, extra = (run_ort if backend == "ort" else run_trt)(model, batch)
    med, p95 = float(np.median(t)), float(np.percentile(t, 95))
    print(json.dumps(dict(model=model, backend=backend, batch=batch, warmup=WARMUP, iters=ITERS,
                          batch_ms_median=med, batch_ms_p95=p95, batch_ms_mean=float(t.mean()),
                          per_item_ms_median=med / batch, per_item_ms_p95=p95 / batch,
                          items_per_s=batch * 1000 / med, gpu_mem_mib=proc_gpu_mem_mib(), **extra)))


def env():
    import onnxruntime, pynvml, tensorrt, torch
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout
    cuda = next((l.split("CUDA Version:")[1].split("|")[0].strip() for l in smi.splitlines() if "CUDA Version" in l), None)
    return dict(gpu=pynvml.nvmlDeviceGetName(h), gpu_mem_total_mib=pynvml.nvmlDeviceGetMemoryInfo(h).total / 2**20,
                driver=pynvml.nvmlSystemGetDriverVersion(), driver_cuda=cuda, onnxruntime=onnxruntime.__version__,
                tensorrt=tensorrt.__version__, torch=torch.__version__, torch_cuda=torch.version.cuda,
                cudnn=torch.backends.cudnn.version(), python=platform.python_version(), kernel=platform.release(),
                warmup=WARMUP, iters=ITERS, batches=BATCHES)


def all_configs():
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "env.json").write_text(json.dumps(env(), indent=2))
    models = list(json.loads((MODELS / "manifest.json").read_text()))
    out = RESULTS / "results.jsonl"
    done = {(r["model"], r["backend"], r["batch"]) for r in map(json.loads, out.read_text().splitlines())} if out.exists() else set()
    for backend in ("ort", "trt"):
        for m in models:
            for b in BATCHES:
                if (m, backend, b) in done:
                    continue
                p = subprocess.run([sys.executable, __file__, "one", m, backend, str(b)], capture_output=True, text=True)
                line = p.stdout.strip().splitlines()[-1] if p.returncode == 0 and p.stdout.strip() else None
                rec = json.loads(line) if line else dict(model=m, backend=backend, batch=b, error=(p.stderr or p.stdout)[-2000:])
                with out.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                print(m, backend, b, rec.get("batch_ms_median", "ERROR"), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "all":
        all_configs()
    else:
        one(sys.argv[2], sys.argv[3], int(sys.argv[4]))
