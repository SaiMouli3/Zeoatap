# ADR-003: No face recognition or biometric re-identification by default
**Status:** Accepted · **Date:** 2026-10-27

## Decision
Tracking uses short-lived, per-camera anonymous IDs. Cross-camera association uses room-level zones and time. Child identity, if needed, comes from staff-entered crib assignments, not vision.

## Why
Biometric law exposure (BIPA, CUBI, GDPR Art. 9), no evidence that infant appearance ReID is reliable, and no product need that outweighs the risk.

## Consequences
Cannot answer "which child" from vision alone. Any future opt-in ReID requires a DPIA + counsel + a separate ADR.
