# ADR-006: Three processes + Postgres; no Redis, broker or Kubernetes in MVP
**Status:** Accepted (provisional) · **Date:** 2026-10-27

## Decision
`vision-worker` (Python, GPU) · `core` (Go: engine, incidents, alerts, API, health, audit, retention) · `web` (Next.js) · PostgreSQL. gRPC over a UNIX socket between vision-worker and core. Docker Compose + systemd.

## Triggers to revisit
- Introduce **NATS JetStream** when ≥2 independent observation consumers or multiple GPU nodes per site exist.
- Introduce **Kubernetes** only for the multi-tenant cloud control plane at fleet scale.
- Introduce **Redis** only for a measured need (e.g. cloud session store at scale).

## Why
Each extra stateful service is another thing to secure, monitor and fail. The edge box is a single machine.
