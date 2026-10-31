"""Download the pretrained pose-pipeline models into services/vision-worker/models/.

Every archive and every extracted ONNX file is checked against the sha256
pinned in zeoatap_vision/models.py; a mismatch aborts and nothing is written.
Only end2end.onnx is read from each archive (no extractall). A manifest with
source URL, license and hashes is written to models/manifest.json.

  uv run python scripts/fetch_models.py                 # default detector + pose
  uv run python scripts/fetch_models.py --all           # also the -tiny / -s variants
  uv run python scripts/fetch_models.py --test-media    # also the adult test photo (tests only)

The models are public pretrained weights, UNVALIDATED for infants or CCTV.
Licenses: see MODELS.md.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zeoatap_vision.models import (DEFAULT_DETECTOR, DEFAULT_POSE, MODELS,  # noqa: E402
                                   default_model_dir, sha256_file)

TIMEOUT_S = 60
MAX_BYTES = 300 * 1024 * 1024

LICENSES = {
    "detector": {
        "code_license": "Apache-2.0 (open-mmlab/mmpose, mmdetection; Megvii YOLOX)",
        "weights": "COCO + Human-Art trained person detector (Human-Art dataset terms apply; see MODELS.md)",
    },
    "pose": {
        "code_license": "Apache-2.0 (open-mmlab/mmpose; rtmlib)",
        "weights": "Body7 trained (COCO, AIC, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18; see MODELS.md)",
    },
}

def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "zeoatap-fetch-models/1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"{url}: larger than {MAX_BYTES} bytes")
    return data


def fetch_model(name: str, out: Path, manifest: dict) -> None:
    spec = MODELS[name]
    dst = out / spec.filename
    if dst.exists() and sha256_file(dst) == spec.onnx_sha256:
        print(f"ok      {name} (already present, sha256 verified)")
    else:
        data, used, errors = None, None, []
        for url in spec.urls:
            try:
                data, used = download(url), url
                break
            except Exception as e:  # try the mirror
                errors.append(f"{url}: {e}")
        if data is None:
            raise SystemExit(f"{name}: download failed: {errors}")
        got = hashlib.sha256(data).hexdigest()
        if got != spec.archive_sha256:
            raise SystemExit(f"{name}: archive sha256 {got} != pinned {spec.archive_sha256}")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [m for m in z.namelist() if m.endswith("/end2end.onnx")]
            if len(members) != 1:
                raise SystemExit(f"{name}: expected one end2end.onnx in archive, got {members}")
            onnx = z.read(members[0])
        got = hashlib.sha256(onnx).hexdigest()
        if got != spec.onnx_sha256:
            raise SystemExit(f"{name}: onnx sha256 {got} != pinned {spec.onnx_sha256}")
        tmp = dst.with_suffix(".part")
        tmp.write_bytes(onnx)
        tmp.replace(dst)
        print(f"fetched {name} from {used}")
    manifest[name] = {
        "kind": spec.kind,
        "file": spec.filename,
        "source": spec.urls[0],
        "mirror": spec.urls[1],
        "archive_sha256": spec.archive_sha256,
        "onnx_sha256": spec.onnx_sha256,
        "input_size_wh": list(spec.input_size),
        "version": spec.version,
        **LICENSES[spec.kind],
        "validated_for_infants_or_cctv": False,
    }


def fetch_test_media(out: Path) -> None:
    from test_media import PHOTO  # scripts/test_media.py

    dst = out / PHOTO["file"]
    if dst.exists() and sha256_file(dst) == PHOTO["sha256"]:
        print(f"ok      {PHOTO['file']} (already present)")
        return
    data = download(PHOTO["url"])
    got = hashlib.sha256(data).hexdigest()
    if got != PHOTO["sha256"]:
        raise SystemExit(f"test photo sha256 {got} != pinned {PHOTO['sha256']}")
    dst.write_bytes(data)
    print(f"fetched test photo {PHOTO['file']} ({PHOTO['license']}, {PHOTO['page']})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(default_model_dir()))
    ap.add_argument("--all", action="store_true", help="fetch every registered model")
    ap.add_argument("--test-media", action="store_true", help="also fetch the adult test photo")
    args = ap.parse_args()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    mf = out / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}
    # FiDIP (kind infant_pose) is exported by scripts/export_fidip.py, not fetched here.
    names = [n for n, s in MODELS.items() if s.onnx_sha256] if args.all else [DEFAULT_DETECTOR, DEFAULT_POSE]
    for n in names:
        fetch_model(n, out, manifest)
        mf.write_text(json.dumps(manifest, indent=2) + "\n")
    if args.test_media:
        sys.path.insert(0, str(Path(__file__).parent))
        fetch_test_media(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
