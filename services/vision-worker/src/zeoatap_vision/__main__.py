"""zeoatap-vision: run camera ingest and emit observations as JSON lines.

  zeoatap-vision --config cameras.json --observations obs.jsonl --detector none
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import WorkerConfig
from .observation import JsonlSink, MockDetector, NoDetector
from .worker import VisionWorker, serve_health


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zeoatap-vision")
    ap.add_argument("--config", required=True, help="camera config JSON")
    ap.add_argument("--observations", default="-", help="output JSONL path, or - for stdout")
    ap.add_argument("--detector", choices=["none", "mock"], default="none",
                    help="'none' = health only (AI_UNAVAILABLE); 'mock' = labelled MOCK, for pipeline tests")
    ap.add_argument("--health-host", default="127.0.0.1")
    ap.add_argument("--health-port", type=int, default=8081)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = WorkerConfig.load(args.config)
    detector = MockDetector() if args.detector == "mock" else NoDetector()
    if args.detector == "mock":
        logging.warning("MOCK detector: observations are not real detections")

    out = sys.stdout if args.observations == "-" else open(args.observations, "a", buffering=1)
    worker = VisionWorker(cfg, detector, JsonlSink(out))
    srv = serve_health(worker, args.health_host, args.health_port)

    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    worker.start()
    done.wait()
    logging.info("shutting down")
    worker.stop()
    srv.shutdown()
    if out is not sys.stdout:
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
