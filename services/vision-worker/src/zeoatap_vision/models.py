"""Registry of the pretrained models the pose pipeline can load.

Every entry is pinned by URL and sha256 (of the downloaded archive AND of the
extracted ONNX file). scripts/fetch_models.py downloads and verifies them into
services/vision-worker/models/ (git-ignored); the pipeline re-verifies the
ONNX sha256 before loading, so a swapped file never runs silently.

All weights are public pretrained weights (COCO / Human-Art / Body7). They
are NOT trained or validated on infants or on CCTV footage. See MODELS.md for
licenses; the project owner clears them before product use.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

OPENMMLAB = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
# rtmlib's own fallback mirror for the same files.
HF_MIRROR = "https://huggingface.co/Tau-J/RTMPose/resolve/main/rtmposev1/onnx_sdk/"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str  # "detector" | "pose"
    archive: str  # file name under OPENMMLAB / HF_MIRROR
    archive_sha256: str  # downloaded archive / checkpoint
    onnx_sha256: str | None  # None: exported locally, verified against models/manifest.json
    input_size: tuple[int, int]  # (w, h)
    version: str  # what goes into observation.model_versions

    @property
    def urls(self) -> list[str]:
        return [OPENMMLAB + self.archive, HF_MIRROR + self.archive]

    @property
    def filename(self) -> str:
        return f"{self.name}.onnx"


MODELS: dict[str, ModelSpec] = {m.name: m for m in [
    ModelSpec("yolox-m-humanart", "detector", "yolox_m_8xb8-300e_humanart-c2c7a14a.zip",
              "a000224fd8ba283202bc62d4a5fcdfe353adb9f468777dbac1ea2ada2093adde",
              "3dea6513388889f0fff4b77bf7a26013600321b9eb9ceb0e9a400a82572f5f23",
              (640, 640), "yolox-m-humanart-c2c7a14a"),
    ModelSpec("yolox-tiny-humanart", "detector", "yolox_tiny_8xb8-300e_humanart-6f3252f9.zip",
              "36e09ca555916253fa1b5d51bec01e48e98e1f0af966c51ce3e844d8c8fc4cfc",
              "ceb11c07298f95c50d7c5abeb906d03340c85f23aa79e3e66966e7fb6c307250",
              (416, 416), "yolox-tiny-humanart-6f3252f9"),
    ModelSpec("rtmpose-m-body7", "pose", "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
              "f7fbb6c5c11a1bb70f3d445e4ddec5d144ea89ad5649c081ef976f9b24a0b741",
              "5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c",
              (192, 256), "rtmpose-m-body7-256x192-e48f03d0"),
    ModelSpec("rtmpose-s-body7", "pose", "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip",
              "7673922e531014906ca4f0f239b7e233b740146a10b632deaa2a28d45470d802",
              "9aeb635b83f86aea45cf45d85798f7eba1a162de8e0d721c44e54fe5eebaf47d",
              (192, 256), "rtmpose-s-body7-256x192-acd4a1ef"),
    # FiDIP infant pose (used at the owner's direction; non-commercial code license, see MODELS.md).
    # Exported to ONNX by scripts/export_fidip.py; "archive" is the PyTorch checkpoint.
    ModelSpec("fidip-mobile", "infant_pose", "mobile_fidip.pth",
              "bd0b7548b1f1158f0a8a97d022305547343c835e7d4027727a978d2d7a4cbca1", None,
              (224, 224), "fidip-posemobilenet-224-bd0b7548"),
    ModelSpec("fidip-hrnet-w48", "infant_pose", "hrnet_fidip.pth",
              "bfbf9df225674614652d5dbdd066daf71c9b9d7f3c31976b7ed33bb96af55d07", None,
              (288, 384), "fidip-hrnet-w48-384x288-bfbf9df2"),
]}

DEFAULT_DETECTOR = "yolox-m-humanart"
DEFAULT_POSE = "rtmpose-m-body7"
DEFAULT_INFANT_POSE = "fidip-mobile"


def default_model_dir() -> Path:
    env = os.environ.get("ZEOATAP_MODEL_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "models"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ModelMissing(RuntimeError):
    pass


def resolve(name: str, model_dir: str | Path | None = None, verify: bool = True) -> Path:
    """Path of a fetched, hash-verified ONNX file. Raises ModelMissing."""
    spec = MODELS[name]
    path = Path(model_dir or default_model_dir()) / spec.filename
    if not path.exists():
        raise ModelMissing(f"{path} not found; run scripts/fetch_models.py")
    if verify:
        expected = spec.onnx_sha256
        if expected is None:  # locally exported: trust only the hash the exporter recorded
            mf = path.parent / "manifest.json"
            try:
                entry = json.loads(mf.read_text()).get(name, {})
            except (OSError, ValueError):
                entry = {}
            if entry.get("checkpoint_sha256") != spec.archive_sha256 or not entry.get("onnx_sha256"):
                raise ModelMissing(f"{name}: no verified export recorded in {mf}; run scripts/export_fidip.py")
            expected = entry["onnx_sha256"]
        if sha256_file(path) != expected:
            raise ModelMissing(f"{path}: sha256 mismatch (expected {expected[:12]}…); re-fetch / re-export it")
    return path


def available(names: list[str], model_dir: str | Path | None = None) -> bool:
    d = Path(model_dir or default_model_dir())
    return all((d / MODELS[n].filename).exists() for n in names)
