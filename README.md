# AlfaGen PII Gateway

Reversible masking of personal data (PII) between bank systems and an LLM. The service
identifies sensitive fragments, replaces them with opaque reversible tokens, stores the
mapping in a scoped Vault, and restores the exact original text for authorized consumers.

All 17 required PII categories are implemented (ФИО, дата рождения, место рождения,
паспорт, гражданство, орган выдачи, код подразделения, дата выдачи, водительское
удостоверение, адрес, email, телефон, ИНН, номер карты, CVV, PIN, имя держателя карты),
with exact round-trip `unmask(mask(x)) == x`.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/download_semantic_models.py
.venv/bin/uvicorn app.main:app --port 8000
```

Open <http://localhost:8000/> — the React web interface.

The preparation command downloads pinned, SHA-256-verified local models (about
1.16 GB). Runtime inference uses RuBERT-tiny2 NER and mDeBERTa context classification
on the CPU; submitted text is never sent to a model service. Models load once.
Missing or corrupted mandatory model files stop startup instead of silently
falling back to regex-only protection. `--verify-only` checks installed assets
without downloading. See [semantic verification](docs/semantic-check.md) for
measured results and limitations; public context is not proof that an arbitrary
fact is public knowledge.

Run tests:

```bash
.venv/bin/pytest -q
```

## React frontend

The web interface is a React + TypeScript + Vite application in `frontend/`.
It is served by FastAPI at the service root.

### Install frontend dependencies

```bash
cd frontend
npm install
```

### Run the React development server (with API proxying to :8000)

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173/>. The dev server proxies `/process`, `/demo`,
`/config`, `/health`, `/metrics` and `/trust-lab` to the FastAPI backend on
`:8000` (start the backend first).

### Launch backend + frontend together (one command)

```bash
cd frontend
npm run dev:all
```

This starts the FastAPI backend on `:8000` and the Vite dev server on
`:5173` simultaneously (via `concurrently`). Open <http://localhost:5173/>.
The dev server proxies API calls to the backend on `:8000`.

### Build the frontend

```bash
cd frontend
npm run build
```

This produces `frontend/dist/`. FastAPI serves this build at the service root
(`/`) with SPA fallback, so refresh and client-side navigation work. The
official `/process` and protected demo/admin routes are preserved.

### TypeScript check

```bash
cd frontend
npm run typecheck
```

## Official contract status

The official `POST /process` contract (Appendix A of `ds.pdf`) is **VERIFIED** and
implemented in `app/api/autocheck.py`:

```
POST /process
{ "payload": "<string>", "payload_id": "<string>" }  → 200  { "result": "<string>" }
```

One endpoint handles both masking and demasking, correlated by `payload_id`. Routing is
content-based (not a blind toggle) so retries are safe: new ID + original → mask; same ID +
same original → same mask; same ID + returned mask → restore; repeated demask → same
original; conflicting input → 409; original == masked (no PII) → no false conflict. The
endpoint works locally without an LLM and without authentication (competition exception).
Managed overload returns 429 with a valid Retry-After (Appendix B). The demo API uses an
explicitly-marked stub provider (no real LLM call).

## Setup (5 sentences)

1. Add a system to `configs/consumers.yaml` and set its allowed access method.
2. Choose the PII types and the full-mask strategy for each of them.
3. Specify whether unmasking and LLM egress are allowed.
4. Validate the configuration with the project command and activate the new version.
5. Send a test request and check the policy version, result, and metrics.

## Adding a new data type without rewriting the core (C4)

A new sensitive data type can be added purely via `configs/detectors.yaml` using a
config-driven regex detector — no code change:

```yaml
detectors:
  - id: account_number
    type: regex
    category: ACCOUNT_NUMBER
    pattern: "\\b\\d{20}\\b"
    context: "(?i)(счёт|счет|account)"
    context_window: 40
    enabled: true
```

The detector matches `pattern` and, if a `context` marker is within the window,
emits a Detection with the matched span as sensitive. `detect_types` in
`configs/consumers.yaml` must include the new category for a consumer to mask it.
Invalid `detect_types` (e.g. a typo like `FULL_NMAE`) fails at load time rather
than silently disabling detection.

## Structure

- `app/core/` — engine, models, normalization, span resolver, tokens, detokenizer.
- `app/vault/` — Vault interface, MemoryVault, RedisVault, AEAD crypto helper.
- `app/detectors/` — detector interface, registry, structured/dates/person/address detectors, config-driven regex detector.
- `app/policies/` — consumer policy schema and loader.
- `app/api/` — demo and autocheck routers, schemas, errors.
- `app/providers/` — provider interface and explicit stub.
- `app/security/` — auth, egress guard, limits.
- `app/observability/` — safe logging and bounded metrics.
- `app/trust_lab/` — Trust Lab (Actions A/B/C, safe report).
- `tests/` — unit, integration, and property tests.
