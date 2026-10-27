# ADR-007: VLM is local, event-triggered and advisory only
**Status:** Accepted · **Date:** 2026-10-27

## Decision
A local open-weight VLM (Qwen3-VL-4B/8B, Apache-2.0) may summarize an existing incident clip into a constrained JSON schema. It cannot create, escalate or close incidents, and it is never required for an alert. No continuous video goes to any external provider.

## Ship condition
An A/B test shows reduced reviewer time without lower disposition accuracy (Experiment E5).
