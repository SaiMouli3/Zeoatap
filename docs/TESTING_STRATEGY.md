# Testing Strategy

**Date:** 2026-10-27

## 1. Test pyramid

| Level | Scope | Tools | Runs |
|---|---|---|---|
| Unit | Rules, state machine, dedup, idempotency, retention, RBAC policy | Go `testing`, pytest, Vitest | Every commit |
| Contract | Observation schema compatibility between vision-worker and core | JSON Schema / protobuf compatibility checks | Every commit |
| Integration | core + Postgres, vision-worker + RTSP simulator | docker compose, testcontainers | Every PR |
| Video replay | Recorded/synthetic videos → full pipeline → incidents compared with golden files | RTSP simulator (GStreamer `rtsp-server` / MediaMTX) | Nightly |
| Model tests | Accuracy on frozen validation set, regression vs. previous release, latency on target GPU | Benchmark harness | Per model release |
| Load | N cameras × fps sustained, burst candidates | Simulator fan-out | Weekly + pre-release |
| Security | SAST (gosec, semgrep), dependency scan (Grype/Trivy), DAST (ZAP), pen test | CI + external | Per PR / pre-pilot |
| Failure injection | See §7 | toxiproxy, `tc netem`, kill -9, disk-fill, fake NVML errors | Nightly + pre-release |
| E2E acceptance | User stories US-01..US-10 | Playwright | Pre-release |

## 2. Unit tests (core)
- Each rule: trigger, min-duration, gating → INCONCLUSIVE, merge window.
- Idempotency: the same observation stream replayed twice → identical incident set.
- Dedup: two cameras, the same room-zone event within merge gap → 1 incident, 2 evidence refs.
- Health: no observation > 3 s → CAMERA_UNAVAILABLE. Green only when heartbeat fresh.
- `validated_for_alerting=false` → never dispatches an alert.

## 3. Video replay tests
Golden scenarios (synthetic or consented): empty room, adult enters restricted zone, manikin roll-off, adult lifts infant (hard negative), camera unplugged, lights off (IR switch), lens covered.

## 4. Model tests
Metrics per SAFETY_VALIDATION §4 with CI. Release blocked if recall's lower bound drops > 2 pts or FA/camera-hour rises > 20% vs. the previous release.

## 5. Load tests
Measure: max cameras at protected-fps policy with < 1% drops, p95 observation latency, GPU memory headroom. Results fill COST_AND_DEPLOYMENT variables.

## 6. Security tests
Auth bypass, IDOR on clip IDs, token expiry, RBAC matrix enforcement, secrets-in-logs scan, TLS config, egress allow-list verification, update-signature tampering.

## 7. Failure-injection matrix
| Fault | Injection | Expected |
|---|---|---|
| Camera disconnect | Drop RTSP | CAMERA_UNAVAILABLE ≤ 3 s. UI red ≤ 5 s |
| Frozen stream | Repeat frame | Frozen detection ≤ 10 s |
| Packet loss 5% | netem | DEGRADED if drop thr exceeded |
| GPU worker crash | kill -9 | AI_UNAVAILABLE, restart, recovery logged |
| OOM | cgroup limit | Same |
| DB down | stop postgres | Incidents buffered in WAL and replayed, zero loss |
| Internet down | block egress | Local alerts continue. "Remote notifications offline" |
| Push provider 500s | mock | Retries → fallback → ops alert |
| Disk full | fallocate | Purge expired, alert, evidence state |
| Clock skew 5 s | fake camera NTP | CLOCK_DRIFT, cross-cam merge disabled |
| Bad model hash | corrupt file | Refuse to start, alert |
| Bad config | invalid YAML | Keep last-known-good |

## 8. Executed in this repository (updated 2026-10-31)
Go unit tests for `services/core/internal/eventengine` run with `go test ./...`. vision-worker: 16 unit tests plus 7 live tests over real RTSP (MediaMTX + ffmpeg synthetic test patterns), covering connect, 5 and 15 fps sampling, camera outage and recovery, clip remux, partial clips, black-camera detection, and vision-worker → Go engine end to end. The GPU inference-speed benchmark (`services/vision-worker/benchmark`) ran on an AWS GPU on synthetic inputs ([BENCHMARK_RESULTS](BENCHMARK_RESULTS.md)). No cameras, footage or trained models have been used. No accuracy has been measured.
