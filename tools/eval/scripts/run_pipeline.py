#!/usr/bin/env python3
"""End-to-end offline replay of one local video file:

  video file --(ffmpeg -re, RTSP)--> MediaMTX --> vision-worker --> observations.jsonl
    --> Go replay (services/core/cmd/replay) --> incidents.jsonl --> zeoatap-eval report

Run from tools/eval:

  uv run python scripts/run_pipeline.py --video clip.mp4 --detector mock \
      --ground-truth gt.json --session-id S-0001 --out out/S-0001

The video is published with MediaMTX `runOnDemand`, so playback starts only
when vision-worker connects and no frames are lost to the reconnect backoff.
The wall time at which ffmpeg starts reading is recorded as the session's t0,
and ground-truth start_s/end_s offsets are resolved against it. Expect about
0.1-0.5 s of alignment error (ffmpeg start-up plus RTSP latency); the report
records the measured offset of the first observation.

Synthetic or consented footage only. Never real children without the consent
and authorization flow in PRIVACY_SECURITY_COMPLIANCE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
VISION = REPO / "services" / "vision-worker"
CORE = REPO / "services" / "core"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(cond, timeout: float, step: float = 0.2) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def detector_choices(uv: str) -> list[str]:
    """Ask vision-worker which detectors it accepts (another team adds real ones)."""
    out = subprocess.run([uv, "run", "--project", str(VISION), "zeoatap-vision", "--help"],
                         capture_output=True, text=True, timeout=300).stdout
    m = re.search(r"--detector\s+\{([^}]+)\}", out)
    return m.group(1).split(",") if m else []


def probe(video: Path) -> dict:
    p = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate:format=duration", "-of", "json", str(video)]))
    st = p["streams"][0]
    num, den = (st.get("avg_frame_rate") or "0/1").split("/")
    return {"codec": st.get("codec_name"), "width": st.get("width"), "height": st.get("height"),
            "fps": float(num) / float(den) if float(den) else None, "duration_s": float(p["format"]["duration"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    ap.add_argument("--detector", default="mock", help="passed to zeoatap-vision --detector (checked against its --help)")
    ap.add_argument("--worker-arg", action="append", default=[],
                    help="extra argument for zeoatap-vision, repeatable (e.g. --worker-arg=--model=/m.onnx)")
    ap.add_argument("--camera-id", help="camera id (default: the GT session's camera_id, else cam1)")
    ap.add_argument("--protected", action="store_true", help="raised-surface camera: 15 fps sampling")
    ap.add_argument("--core-cameras", type=Path,
                    help='core camera/zone config for the replay: {"cameras":[{"id","room_id","zones":[...]}]}')
    ap.add_argument("--ground-truth", type=Path, help="GT file; times as start_s/end_s from video frame 0")
    ap.add_argument("--session-id", help="GT session that this video is (default: the first)")
    ap.add_argument("--goals", type=Path)
    ap.add_argument("--transcode", choices=["auto", "copy", "x264"], default="auto",
                    help="copy H.264 as-is, or re-encode with libx264 (auto: copy if H.264)")
    ap.add_argument("--lead-in-s", type=float, default=3.0,
                    help="prepend this many seconds of the frozen first frame, so the worker is connected and "
                         "decoding before video frame 0 (the first ~1-2 s of a stream are otherwise lost while "
                         "the reader attaches). Forces re-encoding. Default 3")
    ap.add_argument("--tail-s", type=float, default=4.0, help="keep the worker running this long after the video ends")
    ap.add_argument("--mediamtx", default=os.environ.get("MEDIAMTX_BIN") or str(REPO / ".tools" / "mediamtx"))
    args = ap.parse_args()

    uv = shutil.which("uv") or sys.exit("uv not found")
    for tool in ("ffmpeg", "ffprobe", "go"):
        shutil.which(tool) or sys.exit(f"{tool} not found on PATH")
    Path(args.mediamtx).exists() or sys.exit(f"MediaMTX not found at {args.mediamtx} (set MEDIAMTX_BIN)")
    video = args.video.resolve()
    video.exists() or sys.exit(f"no such video: {video}")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    choices = detector_choices(uv)
    if choices and args.detector not in choices:
        sys.exit(f"vision-worker does not accept --detector {args.detector}; it accepts: {', '.join(choices)}")

    gt_doc, sess = None, None
    if args.ground_truth:
        gt_doc = json.loads(args.ground_truth.read_text())
        sessions = gt_doc["sessions"]
        sess = next((s for s in sessions if s["session_id"] == args.session_id), None) if args.session_id else sessions[0]
        sess or sys.exit(f"session {args.session_id} not in {args.ground_truth}")
    cam_id = args.camera_id or (sess["camera_id"] if sess else "cam1")

    info = probe(video)
    print(f"video: {info}", file=sys.stderr)
    lead = max(0.0, args.lead_in_s)
    if lead and args.transcode == "copy":
        sys.exit("--lead-in-s needs re-encoding; use --lead-in-s 0 with --transcode copy")
    copy = args.transcode == "copy" or (args.transcode == "auto" and info["codec"] == "h264" and not lead)
    enc = ["-c:v", "copy"] if copy else [
        *(["-vf", f"tpad=start_duration={lead}:start_mode=clone"] if lead else []),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-g", str(max(1, round(info["fps"] or 15))), "-pix_fmt", "yuv420p"]

    rtsp_port, health_port = free_port(), free_port()
    url = f"rtsp://127.0.0.1:{rtsp_port}/{cam_id}"
    for f in ("t0", "done", "started", "publisher.pids"):
        (out / f).unlink(missing_ok=True)
    ff = " ".join(shlex.quote(x) for x in
                  ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-i", str(video), "-an", *enc,
                   "-f", "rtsp", "-rtsp_transport", "tcp", url])
    publisher = out / "publish.sh"
    # Publish once: if MediaMTX re-runs the on-demand command, it only idles.
    publisher.write_text(f"""#!/bin/sh
cd {shlex.quote(str(out))}
echo $$ >> publisher.pids
if [ -e started ]; then exec sleep 100000; fi
touch started
python3 -c 'import time; print(f"{{time.time():.6f}}")' > t0
{ff} 2> publish.log
echo $? > done
exec sleep 100000
""")
    publisher.chmod(0o755)
    (out / "mediamtx.yml").write_text(
        f"logLevel: warn\nrtspAddress: 127.0.0.1:{rtsp_port}\nrtsp: yes\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\n"
        "api: no\nmetrics: no\npprof: no\nplayback: no\n"
        f"paths:\n  {cam_id}:\n    runOnDemand: {publisher}\n    runOnDemandStartTimeout: 20s\n"
        "    runOnDemandCloseAfter: 600s\n")
    (out / "vision-cameras.json").write_text(json.dumps(
        {"cameras": [{"id": cam_id, "url": url, "protected": bool(args.protected)}]}, indent=2))

    procs: list[subprocess.Popen] = []

    def cleanup():
        for p in reversed(procs):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for p in reversed(procs):
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        # MediaMTX starts runOnDemand commands in their own process group, so
        # they outlive it unless killed explicitly.
        pids = out / "publisher.pids"
        for line in (pids.read_text().split() if pids.exists() else []):
            for target in (lambda: os.killpg(int(line), signal.SIGTERM), lambda: os.kill(int(line), signal.SIGTERM)):
                try:
                    target()
                except (ProcessLookupError, PermissionError, ValueError):
                    pass

    obs_path = out / "observations.jsonl"
    obs_path.write_text("")
    try:
        procs.append(subprocess.Popen([args.mediamtx, str(out / "mediamtx.yml")], start_new_session=True,
                                      stdout=open(out / "mediamtx.log", "w"), stderr=subprocess.STDOUT))
        wait_for(lambda: port_open(rtsp_port), 15) or sys.exit("MediaMTX did not start")
        # Build the replay binary while the video plays.
        replay_bin = out / "replay"
        build = subprocess.Popen(["go", "build", "-o", str(replay_bin), "./cmd/replay"], cwd=CORE)
        worker_cmd = [uv, "run", "--project", str(VISION), "zeoatap-vision", "--config", str(out / "vision-cameras.json"),
                      "--observations", str(obs_path), "--detector", args.detector,
                      "--health-port", str(health_port), *args.worker_arg]
        print("worker:", " ".join(worker_cmd), file=sys.stderr)
        procs.append(subprocess.Popen(worker_cmd, start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=open(out / "vision-worker.log", "w")))
        wait_for(lambda: (out / "t0").exists() and (out / "t0").read_text().strip(), 120) or sys.exit(
            "publisher never started (see vision-worker.log / mediamtx.log)")
        t_pub = float((out / "t0").read_text())
        t0 = t_pub + lead  # wall time of video frame 0
        print(f"t0 = {iso(t0)}; playing {info['duration_s']:.1f} s", file=sys.stderr)
        wait_for(lambda: (out / "done").exists(), info["duration_s"] + lead + 120, 0.5) or sys.exit("publisher did not finish")
        rc = (out / "done").read_text().strip()
        if rc != "0":
            print(f"WARNING: ffmpeg publisher exited {rc}: {(out / 'publish.log').read_text()[-500:]}", file=sys.stderr)
        t_end = time.time()
        time.sleep(args.tail_s)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{health_port}/status", timeout=5) as resp:
                status = json.loads(resp.read())
            (out / "worker-status.json").write_text(json.dumps(status, indent=2))
        except Exception as e:  # noqa: BLE001
            print(f"could not read /status: {e}", file=sys.stderr)
        if build.wait(300) != 0:
            sys.exit("go build ./cmd/replay failed")
    finally:
        cleanup()

    obs = [json.loads(l) for l in obs_path.read_text().splitlines() if l.strip()]
    if not obs:
        sys.exit("no observations were written")
    first_ts = min(datetime.fromisoformat(o["capture_ts"].replace("Z", "+00:00")).timestamp() for o in obs)

    # Core replay.
    if args.core_cameras:
        core_cfg = json.loads(args.core_cameras.read_text())
    else:
        core_cfg = {"cameras": [{"id": cam_id, "room_id": (sess or {}).get("room_id", "room-1"), "zones": []}]}
        print("WARNING: no --core-cameras: the replay has no zones, so zone rules cannot fire", file=sys.stderr)
    (out / "core-cameras.json").write_text(json.dumps(core_cfg, indent=2))
    with open(out / "incidents.jsonl", "w") as f:
        r = subprocess.run([str(out / "replay"), "-config", str(out / "core-cameras.json"),
                            "-observations", str(obs_path)], stdout=f, stderr=subprocess.PIPE, text=True)
    (out / "replay.stderr.log").write_text(r.stderr)
    if r.returncode != 0:
        sys.exit(f"replay failed: {r.stderr}")
    lines = [json.loads(l) for l in (out / "incidents.jsonl").read_text().splitlines() if l.strip()]
    kinds = {}
    for l in lines:
        kinds[l["kind"]] = kinds.get(l["kind"], 0) + 1

    run_info = {"video": str(video), "video_info": info, "detector": args.detector, "camera_id": cam_id,
                "protected": args.protected, "publish_start": iso(t_pub), "lead_in_s": lead, "t0": iso(t0), "publish_end": iso(t_end),
                "first_observation_after_publish_start_s": round(first_ts - t_pub, 3),
                "first_observation_relative_to_frame0_s": round(first_ts - t0, 3), "observations": len(obs),
                "observation_sources": sorted({o["source"] for o in obs}), "replay_output_kinds": kinds,
                "replay_warnings": r.stderr.strip()}
    (out / "run.json").write_text(json.dumps(run_info, indent=2))
    print(json.dumps(run_info, indent=2))

    if gt_doc is None:
        return 0
    # Resolve GT offsets against the measured t0 and evaluate.
    sess["t0"] = iso(t0)
    if "start_s" in sess["operating_span"] and sess["operating_span"].get("end_s") is None:
        sess["operating_span"]["end_s"] = info["duration_s"]
    gt_doc["sessions"] = [sess]
    (out / "ground_truth.resolved.json").write_text(json.dumps(gt_doc, indent=2))
    sys.path.insert(0, str(HERE.parents[1] / "src"))
    from zeoatap_eval.__main__ import main as eval_main  # noqa: E402

    argv = ["evaluate", "--ground-truth", str(out / "ground_truth.resolved.json"),
            "--incidents", str(out / "incidents.jsonl"), "--observations", str(obs_path),
            "--title", f"Replay evaluation: {video.name} (detector={args.detector})",
            "--out-md", str(out / "report.md"), "--out-json", str(out / "report.json")]
    if args.goals:
        argv += ["--goals", str(args.goals)]
    eval_main(argv)
    print(f"report: {out / 'report.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
