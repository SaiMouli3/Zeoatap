"""Download and export the benchmark models to ONNX, recording source, license and sha256.

Only models marked commercially usable (code) in docs/LICENSING_REGISTER.md are allowed here.
Pretrained weights are COCO/Body7-trained (register: weights ⚠️, OQ-L3). They are used to
measure SPEED only. Nothing here is a trained or validated Zeoatap model.

Usage: uv run python fetch_models.py   ->  models/*.onnx + models/manifest.json
"""
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import onnx

ROOT = Path(__file__).parent
OUT = ROOT / "models"
SRC = OUT / "src"

# Pinned upstream revisions.
YOLOX_COMMIT = "6ddff4824372906469a7fae2dc3206c7aa4bbaee"  # Megvii-BaseDetection/YOLOX main at benchmark time
YOLOX_WEIGHTS = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/{}.pth"
RTDETR_REPO, RTDETR_REV = "PekingU/rtdetr_r18vd", "ac77a11ff0170a41b771c03264987f8ce2b0d753"
RTMLIB = "https://download.openmmlab.com/mmpose/v1/projects/"
POSE = {
    "rtmpose-s": RTMLIB + "rtmposev1/onnx_sdk/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip",
    "rtmpose-m": RTMLIB + "rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
    "rtmo-s": RTMLIB + "rtmo/onnx_sdk/rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.zip",
    "rtmo-m": RTMLIB + "rtmo/onnx_sdk/rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip",
}
LIC = {
    "yolox": ("Apache-2.0", "https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/main/LICENSE",
              "COCO-trained weights released in the same repo (register: weights ⚠️, OQ-L3)"),
    "rtdetr": ("Apache-2.0", "https://huggingface.co/api/models/PekingU/rtdetr_r18vd (cardData.license) + github.com/lyuwenyu/RT-DETR LICENSE",
               "COCO (+Objects365 pretrain per upstream) weights (register: weights ⚠️, Objects365 terms)"),
    "mmpose": ("Apache-2.0", "https://raw.githubusercontent.com/open-mmlab/mmpose/main/LICENSE ; rtmlib 0.0.16 Apache-2.0",
               "Body7-trained weights (COCO, AIC, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18) (register: weights ⚠️, OQ-L3)"),
}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dims(p: Path):
    m = onnx.load(str(p), load_external_data=False)
    return {i.name: [d.dim_param or d.dim_value for d in i.type.tensor_type.shape.dim] for i in m.graph.input}


def make_batch_dynamic(p: Path):
    """mmdeploy SDK exports usually already have a symbolic batch axis. Force it if not."""
    m = onnx.load(str(p))
    for t in list(m.graph.input) + list(m.graph.output):
        d0 = t.type.tensor_type.shape.dim[0]
        if not d0.dim_param:
            d0.ClearField("dim_value")
            d0.dim_param = "batch"
    onnx.save(m, str(p))


def fetch_pose(manifest):
    from rtmlib.tools.file import download_checkpoint  # the same downloader rtmlib uses at runtime

    for name, url in POSE.items():
        dst = OUT / f"{name}.onnx"
        if not dst.exists():
            shutil.copy(download_checkpoint(url), dst)
            make_batch_dynamic(dst)
        manifest[name] = dict(kind="pose_topdown" if name.startswith("rtmpose") else "pose_onestage",
                              source=url, via="rtmlib 0.0.16 download_checkpoint",
                              code_license=LIC["mmpose"][0], license_source=LIC["mmpose"][1],
                              weights=LIC["mmpose"][2], onnx_sha256=sha256(dst), inputs=dims(dst))


def fetch_yolox(manifest):
    import torch

    repo = SRC / "YOLOX"
    if not repo.exists():
        subprocess.run(["git", "clone", "-q", "https://github.com/Megvii-BaseDetection/YOLOX.git", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", YOLOX_COMMIT], check=True)
    sys.path.insert(0, str(repo))
    from yolox.exp import get_exp

    for arch in ("yolox_s", "yolox_m"):
        pth = SRC / f"{arch}.pth"
        if not pth.exists():
            urllib.request.urlretrieve(YOLOX_WEIGHTS.format(arch), pth)
        exp = get_exp(None, arch)
        model = exp.get_model().eval()
        model.load_state_dict(torch.load(pth, map_location="cpu", weights_only=True)["model"])
        model.head.decode_in_inference = True  # box decode in-graph; NMS is post-processing, not timed
        for size in (640, 1280):
            dst = OUT / f"{arch.replace('_', '-')}_{size}.onnx"
            if not dst.exists():
                torch.onnx.export(model, torch.zeros(1, 3, size, size), str(dst), opset_version=17,
                                  input_names=["images"], output_names=["output"], dynamo=False,
                                  dynamic_axes={"images": {0: "batch"}, "output": {0: "batch"}})
            manifest[dst.stem] = dict(kind="detector", input_size=size, source=f"{YOLOX_WEIGHTS.format(arch)} @ YOLOX {YOLOX_COMMIT}",
                                      code_license=LIC["yolox"][0], license_source=LIC["yolox"][1],
                                      weights=LIC["yolox"][2], weights_sha256=sha256(pth),
                                      onnx_sha256=sha256(dst), inputs=dims(dst))


def fetch_rtdetr(manifest):
    import torch
    from transformers import RTDetrForObjectDetection

    model = RTDetrForObjectDetection.from_pretrained(RTDETR_REPO, revision=RTDETR_REV).eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            o = self.m(pixel_values=x)
            return o.logits, o.pred_boxes

    for size in (640, 1280):
        dst = OUT / f"rtdetr-r18_{size}.onnx"
        if not dst.exists():
            torch.onnx.export(Wrap(model), torch.zeros(1, 3, size, size), str(dst), opset_version=17,
                              input_names=["images"], output_names=["logits", "boxes"], dynamo=False,
                              dynamic_axes={"images": {0: "batch"}, "logits": {0: "batch"}, "boxes": {0: "batch"}})
        manifest[dst.stem] = dict(kind="detector", input_size=size, source=f"huggingface.co/{RTDETR_REPO}@{RTDETR_REV} (transformers RTDetrForObjectDetection)",
                                  code_license=LIC["rtdetr"][0], license_source=LIC["rtdetr"][1],
                                  weights=LIC["rtdetr"][2], onnx_sha256=sha256(dst), inputs=dims(dst))


if __name__ == "__main__":
    SRC.mkdir(parents=True, exist_ok=True)
    mf = OUT / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}
    for step in (fetch_pose, fetch_yolox, fetch_rtdetr):
        step(manifest)
        mf.write_text(json.dumps(manifest, indent=2))
    for k, v in manifest.items():
        print(k, v["inputs"], v["code_license"])
