# vision-worker

Camera ingest and analysis for the edge appliance. See Phase 1 tasks 1.3 and 1.4 in [IMPLEMENTATION_PLAN](../../docs/IMPLEMENTATION_PLAN.md) and the pipeline in [AI_MODEL_STRATEGY §3](../../docs/AI_MODEL_STRATEGY.md).

It helps staff notice situations that need review. It never identifies anyone: there is no face recognition, track ids are anonymous and short-lived, and video stays on site.

## What it does
- Connects to each camera over RTSP (TCP) and **reconnects forever** with backoff (1 s → 30 s, jittered).
- Keeps a **rolling buffer of the original encoded packets** (default 120 s, capped by bytes). Evidence clips are cut by remuxing those packets to MP4 with **no re-encode**. A clip interrupted by an outage is still written and marked `partial`.
- Samples frames by time: **5 fps** normally and **15 fps** on `protected` (raised-surface) cameras. When analysis falls behind, it drops the oldest frame and counts the drop (`frames_dropped_total`).
- Checks image quality on a 160×90 thumbnail: **black** (3 s), **obstructed** or covered lens (5 s), **frozen** (identical frames for 30 s), with 5 s recovery hysteresis. **Degraded frames never run detection.**
- Emits **observations** (contract: [`packages/contracts/observation.schema.json`](../../packages/contracts/observation.schema.json)) to a JSONL file, or to core as NDJSON batches.
- Serves `/healthz`, `/readyz` (503 unless every camera is streaming), `/status`, Prometheus `/metrics`, and the clip control endpoint `POST /v1/clips`. It binds to **127.0.0.1:8081** by default.

## Detectors (`--detector`)
| Value | What runs | `ai_available` | `source` |
|---|---|---|---|
| `none` (default) | camera health only | `false`, so core shows **AI_UNAVAILABLE**, never green | `none` |
| `mock` | no model, for pipeline tests only | `true` | `MOCK` (never present it as detection) |
| `pose` | YOLOX person detector → ByteTrack-style tracker → RTMPose → infant/adult role → FiDIP infant pose for infant tracks → held state | `true` only while the models are loaded and running; `false` on any frame whose inference failed, and after 3 consecutive failures (retried every 5 s) | `zeoatap-pose-p0`, with every model version in `model_versions` |

> ⚠️ **All `pose` models are public pretrained weights. None is trained or validated on CCTV of a childcare room, and the adult models are not validated on infants.** The **infant/adult role** classifier is a body-proportion heuristic (head width vs shoulder width and torso length, plus relative box size), and the **held** state is a geometric rule (§3.1). Both are **UNVALIDATED**. Observations say so in `model_versions` (`role`, `held`, `validation`). Licenses, sources and sha256 hashes are in [MODELS.md](MODELS.md).

Pipeline details:
- **Frames:** with `pose`, the camera thread also queues a ~640-px-wide RGB frame (`--analysis-width`). The queue holds 4 frames per camera (about 0.7 MB each at 640×360) and drops the oldest.
- **Detector:** `yolox-m-humanart` by default, or `yolox-tiny-humanart` (`--det-model`). It runs on ONNX Runtime, which picks TensorRT, then CUDA, then CPU, whichever is installed (`--device auto|cpu`, `--threads`).
- **Tracker:** ByteTrack two-stage association: high-score detections first, then low-score ones, to keep partly occluded people. It uses a Kalman filter and greedy IoU matching. Ids look like `3f9a-7`: a random per-process prefix plus a counter, per camera, gone 2 s after a person is lost. A track is emitted only after 2 hits, which acts as the track-age gate, because the schema has no `track_age` field.
- **Pose:** RTMPose-m (or `-s`) on all person crops in one batch. For tracks classified as infant, keypoints come from FiDIP (`--infant-pose-model fidip-mobile` by default on CPU, `fidip-hrnet-w48` for GPU, `none` to use RTMPose for everyone). If the FiDIP export is missing, RTMPose is used and `model_versions.infant_pose` says so.
- **Outputs:** `pose_quality` is the mean confidence over visible keypoints (score ≥ 0.3). `visibility` is the fraction of the 17 keypoints that are visible. `box` is normalized `[x1,y1,x2,y2]` in [0,1]. `held` is set only on infants, with 2-frame-on and 3-frame-off hysteresis.

### Getting the models
```bash
uv sync --extra pose
uv run python scripts/fetch_models.py --test-media   # YOLOX-m + RTMPose-m (+ adult test photo); --all for -tiny/-s
# FiDIP (infant pose): needs torch/gdown in a throwaway venv, see the docstring of scripts/export_fidip.py
```
Models go to `models/` (git-ignored; override with `$ZEOATAP_MODEL_DIR`). Every file is checked by sha256 when it is downloaded and again when it is loaded. If a model is missing, `--detector pose` logs an error and keeps running with `ai_available=false`.

### CPU throughput (this 4-vCPU dev container, no GPU)
Measured with `scripts/bench_pipeline.py` on the adult test photo at 640×390, with 7–9 people in view. Each figure covers one analysis thread across all cameras.

| Detector + pose | fps (quiet machine) | fps (shared machine, load average 9–12) |
|---|---|---|
| YOLOX-m + RTMPose-m (default) | 2.1 | 0.7–1.5 |
| YOLOX-tiny + RTMPose-s | 6.4 | 2.2–4.2 |

FiDIP-mobile adds about 15 ms per infant crop (43 ms under load), and FiDIP-HRNet-W48 about 360 ms (1.1 s under load), so HRNet is for GPU only. A live run (1280×778 RTSP stream of the photo, decode in the same process, shared machine) analysed about 0.8 fps with the default models. On CPU the default pipeline cannot keep up with even one camera at 5 fps: frames are dropped and counted, never queued without bound. Production sizing assumes a GPU and TensorRT (see [BENCHMARK_RESULTS](../../docs/BENCHMARK_RESULTS.md)).

## Sinks (`--sink`)
- `jsonl` (default): one observation per line to `--observations` (a path, or `-` for stdout).
- `core`: `POST {--core-url}/internal/v1/observations` (default `http://127.0.0.1:8090`) as NDJSON (`Content-Type: application/x-ndjson`) with `Authorization: Bearer $ZEOATAP_INTERNAL_TOKEN`.
  - Batches hold up to 200 lines or 200 ms.
  - Connect timeout is 2 s and read timeout is 5 s.
  - Network errors, 5xx, 503 and 401/403 are retried with jittered backoff (0.25 s → 10 s), and batch order is kept.
  - On a `400`, the bad line (at index `accepted`) is dropped and counted, and the rest is resent.
  - The buffer is bounded (`--sink-buffer`, default 5000). When it is full, the **oldest** observation is dropped and counted. The analysis thread never waits on the network.
  - Counters are in `/status` (`sink`) and `/metrics` (`zeoatap_sink_*`). The token never appears in logs or status.

## Clip endpoint (`POST /v1/clips`, core → vision)
Request `{camera_id, start, end, path}`, with start and end as unix seconds. The response is `200 {path, sha256, partial}`, as in [API.md](../../packages/contracts/API.md).

| Status | When |
|---|---|
| 401 | the bearer token does not match `$ZEOATAP_INTERNAL_TOKEN` (compared in constant time) |
| 503 | no token is configured (endpoint disabled), or more than 8 clip requests are already in progress |
| 415 | the request is not `application/json` |
| 400 | invalid JSON, fields or window: end ≤ start, longer than 600 s, more than 600 s in the future, a path that is not absolute `.mp4`, or a path outside `--clip-dir` when that is set |
| 404 | unknown camera |
| 422 | no buffered video covers the window |
| 504 | the clip was not written by end + 120 s + 15 s |

The request blocks until the clip is written. The server listens on 127.0.0.1 only unless `--health-host` says otherwise, and it logs a warning for a non-loopback address.

## Run
```bash
uv sync --extra pose
export CAM1_RTSP_CREDS='user:password'          # from the secret store, never in the config file
export ZEOATAP_INTERNAL_TOKEN=...               # shared with core; enables /v1/clips and --sink core
uv run zeoatap-vision --config cameras.example.json --detector pose --sink core --clip-dir /var/lib/zeoatap/spool
uv run zeoatap-vision --config cameras.example.json --observations obs.jsonl --detector none   # health only
curl -s localhost:8081/status
```
Credentials are injected from the named environment variable and redacted (`rtsp://user:****@host`) in every log and error. The config loader rejects URLs that contain credentials.

Not done yet:
- clip encryption (core does it)
- RTCP/NTP timestamps (`ts_source: "receive"`)
- batching frames from several cameras on the GPU
- a role detector fine-tuned on infant/adult
- DarkPose distribution-aware decoding for FiDIP

Frozen detection is a heuristic and must be tuned on real cameras.

## Test
```bash
uv sync --extra pose
uv run pytest -q
```
- `tests/test_units.py`: ring buffer, quality checks, sampling, backoff, secrets handling, contract validation.
- `tests/test_analysis_units.py`: tracker (stable ids, low-score second stage, expiry), held geometry and hysteresis, role heuristic, pose metrics, heatmap decoding, model hash checks. It also checks that pipeline output passes schema validation, that degraded frames skip detection, and that failed inference gives `ai_available=false`.
- `tests/test_sink.py`: CoreSink against a local fake core: batching and order, retries on 503/500/timeouts, 400 bad-line handling, a wrong token, and backpressure (drop-oldest, never blocks).
- `tests/test_clip_api.py`: `/v1/clips` auth, validation, 404 and 422, and a real clip over live RTSP.
- `tests/test_detection_smoke.py`: the full pipeline on a **public-domain photo of adults** (NASA), plus a check of the FiDIP export and decoding. Skipped unless the models and the photo were fetched. These are not accuracy tests.
- `tests/test_live.py`: real RTSP through [MediaMTX](https://github.com/bluenviron/mediamtx), with ffmpeg publishing **synthetic test patterns** (no people, no footage). It is skipped unless MediaMTX is at `$MEDIAMTX_BIN` or `<repo>/.tools/mediamtx` and ffmpeg (with libx264) is on PATH. MediaMTX runs TCP-only, so it does not clash with another instance on UDP 8000/8001.
