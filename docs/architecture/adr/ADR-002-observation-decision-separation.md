# ADR-002: Detectors observe, rules decide, alerter delivers
**Status:** Accepted · **Date:** 2026-10-27

## Decision
Models emit `Observation`s (timestamps, tracks, scores, quality, model versions). The Go event engine applies explicit, versioned, per-event rules. The alert dispatcher handles delivery, acknowledgement and escalation. No safety decision lives inside a model or prompt.

## Why
Testability (deterministic replay), auditability (which rule version decided), safe model swaps, and per-event validation gating (`validated_for_alerting`).

## Consequences
Needs a stable contract (`packages/contracts`). The rules need tuning work, but that work is visible and reviewable.
