"""Download the FiDIP infant pose checkpoints and export them to ONNX.

FiDIP (Huang et al., "Invariant Representation Learning for Infant Pose
Estimation with Small Data", FG 2021), https://github.com/ostadabbas/Infant-Pose-Estimation.
Used at the project owner's direction; the code is "non-commercial purpose only",
so a license must be obtained before sale (MODELS.md). Trained on SyRIP (home
photos + synthetic infants): UNVALIDATED on CCTV.

The checkpoints hold only the pose network (FiDIP's domain classifier is not
in them); they are loaded with strict=True so any unexpected key aborts.

Needs torch, onnx and gdown, which the runtime does not. One way:

  uv venv /tmp/fidip-export && VIRTUAL_ENV=/tmp/fidip-export uv pip install \
      --index-url https://download.pytorch.org/whl/cpu torch && \
  VIRTUAL_ENV=/tmp/fidip-export uv pip install onnx onnxscript gdown numpy
  /tmp/fidip-export/bin/python scripts/export_fidip.py [--models-dir models]

Writes models/fidip-mobile.onnx and models/fidip-hrnet-w48.onnx and records
the checkpoint and ONNX sha256 in models/manifest.json; the worker refuses to
load an ONNX file whose hash differs from that manifest entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fidip"))
sys.path.insert(0, str(HERE.parent / "src"))

from zeoatap_vision.models import MODELS, default_model_dir  # noqa: E402

DRIVE_FOLDER = "https://drive.google.com/drive/folders/108P-1SnTqaj3xNtjYZ1o7T8z6UvUYuiC"
CHECKPOINTS = {
    "fidip-mobile": {"drive_id": "1aHaBwO3XBYtFXI7cDMRIzMWRffIJzrQU", "file": "mobile_fidip.pth"},
    "fidip-hrnet-w48": {"drive_id": "19tBMoVS8wTza7VqVfPc6KBQ_POPAEIqb", "file": "hrnet_fidip.pth"},
}


class AttrDict(dict):
    __getattr__ = dict.__getitem__


HRNET_CFG = AttrDict(MODEL=AttrDict(
    NUM_JOINTS=17, INIT_WEIGHTS=False, PRETRAINED="",
    EXTRA=AttrDict(
        PRETRAINED_LAYERS=["*"], FINAL_CONV_KERNEL=1,
        STAGE2=dict(NUM_MODULES=1, NUM_BRANCHES=2, BLOCK="BASIC", NUM_BLOCKS=[4, 4],
                    NUM_CHANNELS=[48, 96], FUSE_METHOD="SUM"),
        STAGE3=dict(NUM_MODULES=4, NUM_BRANCHES=3, BLOCK="BASIC", NUM_BLOCKS=[4, 4, 4],
                    NUM_CHANNELS=[48, 96, 192], FUSE_METHOD="SUM"),
        STAGE4=dict(NUM_MODULES=3, NUM_BRANCHES=4, BLOCK="BASIC", NUM_BLOCKS=[4, 4, 4, 4],
                    NUM_CHANNELS=[48, 96, 192, 384], FUSE_METHOD="SUM"),
    ),
))


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(name: str):
    import torch

    if name == "fidip-mobile":
        from pose_mobile import PoseMobileNet

        net = PoseMobileNet(None)

        class HeatmapsOnly(torch.nn.Module):  # drop the feature output used for domain adaptation
            def __init__(self, m):
                super().__init__()
                self.m = m

            def forward(self, x):
                return self.m(x)[1]

        return net, HeatmapsOnly(net)
    from pose_hrnet import PoseHighResolutionNet

    net = PoseHighResolutionNet(HRNET_CFG)
    return net, net


def main() -> int:
    import torch

    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=str(default_model_dir()))
    ap.add_argument("--only", choices=list(CHECKPOINTS))
    args = ap.parse_args()
    out = Path(args.models_dir)
    src = out / "src"
    src.mkdir(parents=True, exist_ok=True)
    mf = out / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}

    for name, ck in CHECKPOINTS.items():
        if args.only and name != args.only:
            continue
        spec = MODELS[name]
        pth = src / ck["file"]
        if not pth.exists() or sha256(pth) != spec.archive_sha256:
            import gdown

            gdown.download(id=ck["drive_id"], output=str(pth), quiet=True)
        got = sha256(pth)
        if got != spec.archive_sha256:
            raise SystemExit(f"{name}: checkpoint sha256 {got} != pinned {spec.archive_sha256}")
        net, export = build(name)
        state = torch.load(pth, map_location="cpu", weights_only=True)
        net.load_state_dict(state, strict=True)
        export.eval()
        w, h = spec.input_size
        dst = out / spec.filename
        torch.onnx.export(export, torch.zeros(1, 3, h, w), str(dst), opset_version=17,
                          input_names=["input"], output_names=["heatmaps"], dynamo=False,
                          dynamic_axes={"input": {0: "batch"}, "heatmaps": {0: "batch"}})
        manifest[name] = {
            "kind": spec.kind,
            "file": spec.filename,
            "source": f"{DRIVE_FOLDER} ({ck['file']}, drive id {ck['drive_id']})",
            "code": "https://github.com/ostadabbas/Infant-Pose-Estimation @ a76b4f418107b8d3548d9779e94702e2508fbef1",
            "checkpoint_sha256": got,
            "onnx_sha256": sha256(dst),
            "exported_with": f"torch {torch.__version__}, opset 17",
            "input_size_wh": [w, h],
            "version": spec.version,
            "code_license": "FiDIP repo: non-commercial purpose only (model files carry MIT headers from Microsoft)",
            "weights": "SyRIP-trained infant pose; used at owner's direction, license to be obtained before sale",
            "validated_for_infants_or_cctv": False,
        }
        mf.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"exported {name} -> {dst} sha256 {manifest[name]['onnx_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
