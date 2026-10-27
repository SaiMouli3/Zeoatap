# ADR-001: Edge-first hybrid deployment
**Status:** Accepted (provisional, revisit after Phase 1 benchmark) · **Date:** 2026-10-27

## Context
Requirement #9: detection must continue offline. Video of infants is highly sensitive. Facility uplinks are often limited: sending 16 cameras to the cloud needs ~32–64 Mbps sustained upstream.

## Options
A. Fully local · B. Cloud GPU · C. Edge processing + cloud control plane

## Decision
**C.** All inference, decisions, alerting, evidence and dashboard run on a facility appliance. The cloud handles fleet management, signed releases, metadata telemetry and push relay. No video leaves by default.

## Consequences
+ Offline operation, privacy, low latency, low egress.
− Hardware per site, field failures need a spare-unit process (H-31), and GPU sizing must be measured.
