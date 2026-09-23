# AlfaGen PII Gateway

Reversible masking of personal data (PII) between bank systems and an LLM. The service
identifies sensitive fragments, replaces them with opaque reversible tokens, stores the
mapping in a scoped Vault, and restores the exact original text for authorized consumers.

All 17 required PII categories are implemented (ФИО, дата рождения, место рождения,
паспорт, гражданство, орган выдачи, код подразделения, дата выдачи, водительское
удостоверение, адрес, email, телефон, ИНН, номер карты, CVV, PIN, имя держателя карты),
with exact round-trip `unmask(mask(x)) == x`.

## Quick start

For the server, build and run the complete React + Python service:

```bash
docker compose up --build -d
```

Open <http://localhost:8000/>; the basic judge demo and `/process` require no API
key or `.env` file. The build downloads the pinned models once. See
[server deployment](docs/server-deployment.md) for update, verification, and
optional Redis instructions. The default is one worker with MemoryVault.

For local development without Docker:

```bash
(cd frontend && npm ci && npm run build)
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
npm ci
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

The official `POST /process` contract (Appendix A of `ds.pdf`, checked again
against `Модуль_безопасности_ПД (1).pdf`) is implemented in
`app/api/autocheck.py` and **verified by local contract tests**; the hidden
organizer test has not been run:

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

1. Add the system to `configs/consumers.yaml`, set `enabled`, a unique `namespace`, and `authentication: api_key` with its `api_key_env`.
2. Set `detect_types` to `all_required` or the required category list, and choose `default_action: tokenize_full` or `opaque_token_full`.
3. Set `allow_unmask` and `allow_llm_egress` for that system and configure its secret API key on the server.
4. Validate with `.venv/bin/python -c "from app.policies.loader import load_policy_config; load_policy_config('configs/consumers.yaml')"` and rebuild/restart the service so all workers load the same file.
5. Send a mask–restore request, check `/health` and `/metrics`, and use a new policy version for a configuration release.

Runtime UI updates affect one process and do not persist across restarts;
edit the source configuration and redeploy for durable or multiworker changes.
The competition exception applies to the isolated `autocheck` profile only.

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

## Requirements and verification

[Requirement-by-requirement status](docs/requirements-verification.md) separates
implemented behavior, executed checks, and remaining gaps. Test counts and local
synthetic scores are not evidence of 95% accuracy on the hidden judge dataset.
A public endpoint and source ZIP are both required for submission (§7).

`/metrics` exposes request counts, latency distributions, worker-local RPS,
estimated incoming-text TPS, and exact successful model-inference input TPS.
Model TPS includes NER window overlap/special tokens and NLI hypotheses; its
60-second window and token definition are returned with the values. Technical
logs contain generated request IDs, stages, and category counts, never text,
reversible tokens, mappings, payload IDs, or keys.
