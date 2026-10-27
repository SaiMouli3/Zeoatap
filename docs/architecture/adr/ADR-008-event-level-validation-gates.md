# ADR-008: Event-level metrics and shadow-mode gates before alerting
**Status:** Accepted · **Date:** 2026-10-27

## Decision
Every learned rule ships with `validated_for_alerting=false`. It can be flipped only by a signed release referencing a validation report that meets pre-registered event-level goals (recall lower CI bound, false alerts per camera-hour, latency) and a completed shadow-mode period.

## Consequences
Slower path to "smart" alerts. Much lower risk of alert fatigue and of unsafe claims.
