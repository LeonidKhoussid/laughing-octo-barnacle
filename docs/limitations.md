# Limitations — AlfaGen PII Gateway

Real, measured limitations of this solution. These are not a generic template;
each item reflects an actual constraint of the implemented system.

## 1. Performance

- **100k-token mask is above the 1 s SLA.** A single 100k-token document is
  processed functionally (no silent truncation, end-of-document fragment masked,
  exact round-trip), but the mask latency is ~1.74 s p50 at low rate and ~8 s at
  high offered rate. The engine is single-pass and not chunked, so a single
  large document is CPU-bound.
- **RedisVault is much slower than MemoryVault.** `ensure_capacity()` performs a
  full Redis `SCAN` over all keys on every mask (O(N) per request). The
  multiworker path sustains ~108 RPS vs ~957 RPS for MemoryVault (1 worker).
- **MemoryVault degrades as it accumulates entries.** `_purge_expired()` is O(N)
  on every operation; throughput drops from ~957 RPS on a fresh server to ~418
  RPS after many entries accumulate.
- **Long-text starvation.** Mixing long and short requests degrades short-request
  latency significantly (short p50 rises from ~3 ms to ~92 ms).

## 2. Detection quality

- **Synthetic fixtures are not proof of real-world 100%.** The per-category
  metrics (P/R/F1 = 1.000) are measured on a template-generated synthetic corpus
  (82 examples, seed 20260922). This does **not** prove 100% accuracy on real
  data and is **not** the organizers' official formula.
- **FULL_NAME is a heuristic** (dictionary + context), not a full NER; uncommon
  names or unusual grammar may be missed.
- **ADDRESS handles a fixed set of component patterns**; complex addresses
  (buildings, fractions, "8 Марта" streets) are only partially covered.
- **Historical-person detection for BIRTH_PLACE** relies on a profession-word
  guard and may not cover all historical contexts.
- Ambiguous bare values, rare name forms, obfuscations, unknown document types,
  and distant cross-chunk relations are not exhaustively covered.

## 3. Contract and integration

- **Official contract UNVERIFIED.** Appendix A (the official `POST /process`
  JSON schema) is missing. The `/process` endpoint uses an assumed project
  schema marked `UNVERIFIED`. This blocks only confirmation of official adapter
  compatibility, not the independent core, demo API, detectors, tokenization,
  Vault, round-trip, or tests.
- **No full bank integration.** AlfaGen is not integrated; the demo uses an
  explicitly-marked stub provider. Real bank systems, real data, and an
  information-security retention policy are required for production.
- **No compliance certificate.** The system does not certify the absence of any
  unknown PII and does not promise absolute anonymity (R45, R06).

## 4. Storage

- **Limited TTL and capacity.** Vault entries expire after a configurable TTL
  (default 3600 s) and are bounded by `max_entries` (100k) and `max_bytes`
  (1 GB). After expiry/miss the original is not recoverable (R36).
- **No physical RAM erasure.** The system does not promise physical erasure of
  Python RAM on TTL; persistence/backups and real storage boundaries are the
  actual limits (R50).
