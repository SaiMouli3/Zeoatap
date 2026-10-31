# Zeoatap: AI Baby-Care CCTV Safety Intelligence Platform

A **safety-support** system for licensed infant-care facilities. It watches *authorized* CCTV feeds and helps staff notice situations that may need attention, such as monitoring outages, an adult in a restricted area, or an infant who appears to have left a raised surface. It records evidence for **human review**.

> ⚠️ This is **not** a substitute for supervision, **not** a medical device and **not** an abuse-detection or adjudication system. No output is a finding about any person. **No detection model in this repository has been trained or validated.**

## Status (2026-10-31)

| Component | Status |
|---|---|
| Research, architecture, validation, privacy and cost documentation | **Verified** sources, documented in `docs/` |
| Event engine (rules, temporal state machine, dedup, idempotency, monitoring states) | **Implemented and tested** (Go unit tests, mock data only) |
| Offline replay CLI | **Implemented and tested** with a **MOCK** observation fixture |
| Observation contract (JSON Schema) | Implemented, but not yet validated by contract tests |
| Postgres migration | Implemented, **not tested** (no DB in this environment) |
| GPU inference-speed benchmark (YOLOX, RT-DETR, RTMPose, RTMO; ORT + TensorRT) | **Run** on an AWS RTX PRO 4500 Blackwell with synthetic inputs. **Speed only, not accuracy.** See [BENCHMARK_RESULTS](docs/BENCHMARK_RESULTS.md) |
| Video ingest, detector, tracker, pose, dashboard, alerting | **Not implemented** (planned, see `docs/IMPLEMENTATION_PLAN.md`) |
| Infant-specific models (FiDIP, InfAct) | **Blocked by license** (non-commercial) |
| Breathing estimation | **Research only. Not a product feature** |

## Architecture in one paragraph
Edge-first hybrid ([ADR-001](docs/architecture/adr/ADR-001-edge-first-hybrid.md)). One GPU appliance per facility runs `vision-worker` (Python: RTSP → decode → detect → track → pose → *observations*), `core` (Go: event engine → incidents → alerts, API, health, audit, retention), `web` (Next.js) and PostgreSQL. Video stays on site. The cloud is a metadata-only control plane. Detectors *observe*, rules *decide*, the alerter *delivers* ([ADR-002](docs/architecture/adr/ADR-002-observation-decision-separation.md)).

## Quick start (what actually runs today)
Requires Go ≥ 1.24.
```bash
cd services/core
go test ./...                                   # event-engine unit tests
go run ./cmd/replay -config ../../tests/fixtures/cameras.json \
                    -observations ../../tests/fixtures/mock_observations.jsonl
```
The fixture is **synthetic MOCK detector output**. The replay prints a `WARNING` and every incident carries `"source":"MOCK"`.

## Repository layout
```
docs/                      Research, requirements, architecture, validation, privacy, cost, roadmap
docs/architecture/adr/     Architecture decision records
docs/architecture/diagrams Mermaid diagrams (validated to render)
packages/contracts/        Observation JSON Schema
services/core/             Go event engine + replay CLI
infra/migrations/          Postgres schema (untested)
services/vision-worker/benchmark/  GPU latency benchmark harness (uv-locked)
tests/fixtures/            MOCK observation streams
```

## Documentation
| Doc | Purpose |
|---|---|
| [RESEARCH_REPORT](docs/RESEARCH_REPORT.md) | Models, datasets, licenses, decision matrix |
| [HURDLES_AND_RISKS](docs/HURDLES_AND_RISKS.md) | Every expected obstacle and how hard it is to solve |
| [PRODUCT_REQUIREMENTS](docs/PRODUCT_REQUIREMENTS.md) | Personas, stories, feature matrix, dashboard design |
| [SYSTEM_ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md) | Diagrams, services, failure handling, scaling |
| [AI_MODEL_STRATEGY](docs/AI_MODEL_STRATEGY.md) | Event taxonomy, pipeline, decision engine, VLM policy |
| [SAFETY_VALIDATION](docs/SAFETY_VALIDATION.md) | Event-level metrics, phases, go/no-go |
| [PRIVACY_SECURITY_COMPLIANCE](docs/PRIVACY_SECURITY_COMPLIANCE.md) | Threat model, RBAC, retention, legal checklist |
| [COST_AND_DEPLOYMENT](docs/COST_AND_DEPLOYMENT.md) | Formulas and scenarios |
| [BENCHMARK_RESULTS](docs/BENCHMARK_RESULTS.md) | Measured detector/pose GPU latency and cameras-per-GPU math (speed only) |
| [FEATURE_ROADMAP](docs/FEATURE_ROADMAP.md) / [IMPLEMENTATION_PLAN](docs/IMPLEMENTATION_PLAN.md) | Phases, tasks, gates |
| [TESTING_STRATEGY](docs/TESTING_STRATEGY.md) | Test levels and failure injection |
| [LICENSING_REGISTER](docs/LICENSING_REGISTER.md) | Licenses for every component |
| [OPEN_QUESTIONS](docs/OPEN_QUESTIONS.md) | Unresolved decisions |

## System requirements (target, provisional)
x86-64 edge appliance with an NVIDIA GPU (≥16 GB VRAM), 32 GB RAM, 1 TB NVMe, TPM 2.0, wired PoE cameras on an isolated VLAN. The inference benchmark shows detector + pose need < 1 GB GPU memory and ≈46 cameras per GPU of inference capacity (YOLOX-M 640 + RTMPose-m, TensorRT FP16, on an RTX PRO 4500 Blackwell). Final sizing waits for the edge-SKU re-run and the end-to-end soak.

## Known limitations
No trained models. No cameras or footage were available, and none were used. A cloud GPU was used only for the inference-speed benchmark, on synthetic inputs. Legal review has not been done. Apart from the benchmark's latency numbers, all performance targets are engineering goals, not measurements.
