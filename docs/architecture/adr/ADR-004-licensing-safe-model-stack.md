# ADR-004: Licensing-safe model stack
**Status:** Accepted · **Date:** 2026-10-27

## Decision
Ship only code under Apache-2.0/MIT/BSD (or commercially licensed proprietary runtimes like TensorRT), with weights trained or fine-tuned on data we have rights to. Exclude AGPL (Ultralytics, BoxMOT), CC BY-NC (VideoMAE, Sapiens), research-only (FiDIP, ACLab datasets, Qwen2.5-VL-3B) and unlicensed code (CoReVAD) from the product.

## Consequences
We forgo the best-known infant-specific models and must build our own infant data. Non-commercial assets may be used only for internal research benchmarking where their terms allow it. See LICENSING_REGISTER.md.
