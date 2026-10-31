# vision-worker

Camera ingest for the edge appliance. Phase 1 tasks 1.3 and 1.4 in [IMPLEMENTATION_PLAN](../../docs/IMPLEMENTATION_PLAN.md).

What it does today:
- Connects to each camera over RTSP (TCP) and **reconnects forever** with backoff (1 s → 30 s, jittered).
- Keeps a **rolling buffer of the original encoded packets** (default 120 s, capped by bytes), and cuts evidence clips by remuxing them to MP4 with **no re-encode**. A clip interrupted by an outage is still written and marked `partial`.
- Samples frames by time: **5 fps** normally, **15 fps** on `protected` (raised-surface) cameras. It drops the oldest frame when analysis falls behind and counts the drops.
- Checks image quality on a 160×90 thumbnail: **black** (3 s), **obstructed** / covered lens (5 s), **frozen** (identical frames for 30 s), with 5 s recovery hysteresis.
- Emits **observations** (contract: [`packages/contracts/observation.schema.json`](../../packages/contracts/observation.schema.json)) as JSON lines.
- Serves `/healthz`, `/readyz` (503 unless every camera is streaming), `/status` and Prometheus `/metrics` on 127.0.0.1:8081.

What it does **not** do yet: no detector, tracker or pose model (`--detector none` reports `ai_available=false`, so core shows **AI_UNAVAILABLE**, never green; `--detector mock` is labelled `MOCK` for pipeline tests only). No gRPC to core yet (JSONL instead), no clip encryption, no RTCP/NTP timestamps (`ts_source: "receive"`). Frozen detection is a heuristic and must be tuned on real cameras.

## Run
```bash
uv sync
export CAM1_RTSP_CREDS='user:password'   # from the secret store, never in the config file
uv run zeoatap-vision --config cameras.example.json --observations obs.jsonl --detector none
curl -s localhost:8081/status
```
Credentials are injected from the named environment variable and redacted (`rtsp://user:****@host`) in every log and error. The config loader rejects URLs that contain credentials.

## Test
```bash
uv run pytest -q
```
- `tests/test_units.py`: ring buffer, quality checks, sampling, backoff, secrets handling, contract validation.
- `tests/test_live.py`: real RTSP through [MediaMTX](https://github.com/bluenviron/mediamtx) with ffmpeg publishing **synthetic test patterns** (no people, no footage). Covers connect and sample rate, 15 fps protected cameras, camera outage → reconnect → recovery, clip remux (decodes cleanly), partial clips, black-camera detection, and vision-worker → Go event engine end to end. These tests are skipped unless MediaMTX is at `$MEDIAMTX_BIN` or `<repo>/.tools/mediamtx`, and ffmpeg (with libx264) is on PATH.
