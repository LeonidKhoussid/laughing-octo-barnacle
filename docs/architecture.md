# Architecture — AlfaGen PII Gateway

Final architecture of the AlfaGen PII Gateway (G1–G8). This describes the
implemented system as it actually runs, including trust boundaries, the
public-autocheck exception, protected consumers, local detection, the Vault,
policy snapshots, the optional provider, and failure behavior.

## 1. Overview

The service is a FastAPI application (`app/main.py`) that sits between bank
systems and an LLM. It identifies sensitive fragments in text, replaces them
with opaque reversible tokens, stores the mapping in a scoped Vault, and
restores the exact original text for authorized consumers.

Two shells wrap one shared engine:

- **Autocheck** (`POST /process`, `app/api/autocheck.py`) — the competition
  endpoint. Runs locally, no LLM, no authentication (competition exception).
- **Demo** (`app/api/demo.py`) — protected consumer API with per-consumer
  policies, unmask, and an optional stub provider.

## 2. Components

| Component | Module | Responsibility |
| --- | --- | --- |
| Engine | `app/core/engine.py` | Orchestrates detect → resolve → mask → store → unmask. |
| Detectors | `app/detectors/` | 17 category detectors + registry (`registry.py`). |
| Normalization | `app/core/normalization.py` | Unicode normalization with explicit offset mapping. |
| Span resolver | `app/core/span_resolver.py` | Resolves evidence spans to final sensitive spans. |
| Tokens | `app/core/tokens.py` | Opaque, context-scoped, cryptographically random tokens. |
| Detokenizer | `app/core/detokenizer.py` | Single-pass exact substitution for unmask. |
| Vault | `app/vault/` | MemoryVault (single-process) and RedisVault (multiworker), AEAD crypto, lifecycle. |
| Policies | `app/policies/` | Per-consumer policy schema and loader. |
| Providers | `app/providers/` | Provider interface and explicit stub. |
| Security | `app/security/` | Auth, egress guard, limits. |
| Observability | `app/observability/` | Safe logging and bounded metrics. |
| Trust Lab | `app/trust_lab/` | Reproducibility, safe rule-change, and failure checks. |

## 3. Trust boundaries

- **Public autocheck exception**: `POST /process` is the only unauthenticated
  endpoint (competition exception, R37). Its namespace (`autocheck`) is isolated
  from protected consumer namespaces.
- **Protected consumers**: `support_demo`, `analytics_demo`, `combination_demo`,
  `demo_chat` require an API key (`X-API-Key`). `X-System-ID` alone is not
  authorization (R38).
- **Admin-only**: Trust Lab fault injection (Action C) requires `X-Admin-Key`
  and `PII_TRUST_LAB_FAULTS=1`; it is disabled by default and not reachable via
  `/process` (R52).
- **No raw PII egress**: raw PII is never sent to an external model. Only a
  masked payload passes through the single controlled egress point (R44). The
  default provider is an explicitly-marked stub (R48).

## 4. Local detection

All 17 categories are detected locally by rule-based detectors
(`app/detectors/`). Each detector implements a common interface
(`app/detectors/base.py`) and is registered in `configs/detectors.yaml` and the
registry. Detectors are independent of the core engine; a new simple detector is
added through the config/registry interface without rewriting the core (R42).

## 5. Vault and lifecycle

- **MemoryVault** (`app/vault/memory.py`) — single-process local mode. Forbidden
  with `PII_WORKERS>1` (fail fast, no silent fallback, R34).
- **RedisVault** (`app/vault/redis.py`) — shared Vault for multiworker. Requires
  `PII_VAULT_KEY` (AEAD); startup fails if the key is missing (R49).
- **Lifecycle** (`app/vault/lifecycle.py`) — state machine ensuring the mapping
  is fully committed before a successful mask response (R30), with atomic
  commit, TTL, replay grace, and capacity limits.
- **Crypto** (`app/vault/crypto.py`) — AEAD (AES-GCM) with correct nonce and
  associated data; keys come from env, never from the repo (R49).

## 6. Policies

Per-consumer policies are defined in `configs/consumers.yaml` and loaded by
`app/policies/`. Each consumer has `detect_types`, `default_action`,
`allow_unmask`, `allow_llm_egress`, and optional `combination_rules`. Policies
are versioned (`policy_version`) and validated. A new simple type is added
without rewriting the core (R42).

## 7. Optional provider

The provider interface (`app/providers/base.py`) is implemented by an explicit
stub (`app/providers/stub.py`). The stub is clearly marked as a stub; there is
no hidden substitution of a real response and no fallback to a forbidden
provider (R48). AlfaGen is not integrated.

## 8. Failure behavior

- **Mandatory detector failure blocks egress**: if a required detector is
  unavailable, processing stops and no request is sent upstream (R46).
- **Overload**: limits (`app/security/limits.py`) enforce a UTF-8-aware body
  size limit, in-flight backpressure, and a per-request deadline. On overload
  the routers return a retryable `503` with a valid integer-seconds
  `Retry-After` (R57).
- **Vault miss/expiry**: on expiry/miss the system does not invent an original
  and does not report the operation as successfully restored (R36).

## 9. Observability

- **SafeLogger** (`app/observability/logs.py`) logs only an allowlist of fields
  and never logs original/masked body, vault mappings, keys, auth headers, or
  raw `payload_id` (R51).
- **Metrics** (`app/observability/metrics.py`) use bounded-cardinality label
  enums; any out-of-enum label value is dropped (R51).

## 10. Trust Lab

The Trust Lab (`app/trust_lab/`) provides three actions: Action A (check a new
text with variations), Action B (compare a candidate policy before activation),
and Action C (inject a controlled failure). It runs the real shared engine and
reports real outcomes including failures (R67). See `docs/trust-lab.md`.
