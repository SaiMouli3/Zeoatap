"""zeoatap-vision: run camera ingest and emit observations.

  zeoatap-vision --config cameras.json --observations obs.jsonl --detector none
  ZEOATAP_INTERNAL_TOKEN=... zeoatap-vision --config cameras.json --sink core --detector pose
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from .config import WorkerConfig, redact
from .observation import JsonlSink, MockDetector, NoDetector
from .worker import serve_health, VisionWorker

TOKEN_ENV = "ZEOATAP_INTERNAL_TOKEN"


def build_detector(args):
    if args.detector == "mock":
        logging.warning("MOCK detector: observations are not real detections")
        return MockDetector()
    if args.detector == "pose":
        from .models import ModelMissing
        try:
            from .pipeline import PosePipeline
            det = PosePipeline(detector_model=args.det_model, pose_model=args.pose_model,
                               model_dir=args.model_dir, device=args.device, threads=args.threads,
                               frame_width=args.analysis_width)
        except (ModelMissing, ImportError) as e:
            # Keep running for camera health; core shows AI_UNAVAILABLE, never green.
            logging.error("pose pipeline unavailable (%s); running with ai_available=false", e)
            return NoDetector()
        logging.warning("pose pipeline loaded (%s). Models are public pretrained weights, "
                        "UNVALIDATED for infants and CCTV; roles and held state are heuristics.",
                        det.provider)
        return det
    return NoDetector()


def main(argv: list[str] | None = None) -> int:
    from .models import DEFAULT_DETECTOR, DEFAULT_POSE, MODELS

    ap = argparse.ArgumentParser(prog="zeoatap-vision")
    ap.add_argument("--config", required=True, help="camera config JSON")
    ap.add_argument("--sink", choices=["jsonl", "core"], default="jsonl",
                    help="'jsonl' = write --observations; 'core' = POST NDJSON batches to --core-url")
    ap.add_argument("--observations", default="-", help="output JSONL path, or - for stdout (--sink jsonl)")
    ap.add_argument("--core-url", default="http://127.0.0.1:8090",
                    help="core internal listener (--sink core); token from $" + TOKEN_ENV)
    ap.add_argument("--sink-buffer", type=int, default=5000, help="max observations buffered for core")
    ap.add_argument("--detector", choices=["none", "mock", "pose"], default="none",
                    help="'none' = health only (AI_UNAVAILABLE); 'mock' = labelled MOCK, for pipeline tests; "
                         "'pose' = YOLOX + ByteTrack + RTMPose pipeline (pretrained, UNVALIDATED)")
    ap.add_argument("--det-model", choices=[m for m, s in MODELS.items() if s.kind == "detector"],
                    default=DEFAULT_DETECTOR)
    ap.add_argument("--pose-model", choices=[m for m, s in MODELS.items() if s.kind == "pose"],
                    default=DEFAULT_POSE)
    ap.add_argument("--model-dir", default=None, help="default: $ZEOATAP_MODEL_DIR or services/vision-worker/models")
    ap.add_argument("--device", choices=["auto", "cpu"], default="auto",
                    help="auto = TensorRT > CUDA > CPU, whichever onnxruntime offers")
    ap.add_argument("--threads", type=int, default=None, help="ONNX Runtime intra-op threads (default: all cores)")
    ap.add_argument("--analysis-width", type=int, default=640, help="width of the RGB frame given to the detector")
    ap.add_argument("--health-host", default="127.0.0.1")
    ap.add_argument("--health-port", type=int, default=8081)
    ap.add_argument("--clip-dir", default=None,
                    help="if set, POST /v1/clips only writes inside this directory")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = WorkerConfig.load(args.config)
    token = os.environ.get(TOKEN_ENV) or None
    detector = build_detector(args)

    out = None
    if args.sink == "core":
        if not token:
            ap.error(f"--sink core needs ${TOKEN_ENV}")
        from .sink import CoreSink
        sink = CoreSink(args.core_url, token, max_buffer=args.sink_buffer)
        logging.info("sending observations to %s", redact(sink.url))
    else:
        out = sys.stdout if args.observations == "-" else open(args.observations, "a", buffering=1)
        sink = JsonlSink(out)
    if not token:
        logging.warning("$%s not set: POST /v1/clips is disabled", TOKEN_ENV)

    worker = VisionWorker(cfg, detector, sink)
    srv = serve_health(worker, args.health_host, args.health_port, token=token, clip_dir=args.clip_dir)

    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    worker.start()
    done.wait()
    logging.info("shutting down")
    worker.stop()
    srv.shutdown()
    if hasattr(sink, "close"):
        sink.close()
    if out is not None and out is not sys.stdout:
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
