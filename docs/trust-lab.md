# Trust Lab (G7) — «Лаборатория доверия»

This document describes the **Trust Lab**, the distinguishing feature of the
AlfaGen PII Gateway (master prompt section 17). It is a set of real, runnable
checks that let a jury verify **reproducibility**, **safe rule changes**, and
**failure behavior** on the same working system — without exposing raw PII and
without claiming results that were not actually measured.

The Trust Lab is **not** a demo that draws green results. It runs the real
shared engine and reports real outcomes, including failures (R67).

---

## 1. Purpose (section 17.1)

The jury must be able to verify, on the same working system:

- **Reproducibility** — the same input, seed, build and policy produce the same
  decisions and the same exact round-trip.
- **Safe rule changes** — a candidate policy is compared against the active one
  on a labeled regression set *before* it is activated; regressions are shown,
  not hidden.
- **Failure behavior** — when a mandatory component (detector or Vault) is
  unavailable, processing stops and no request is sent upstream.

All checks run against the **shared engine** (`app/core/engine.py`), not a
separate lightweight UI detector, so what the jury sees is what actually serves
traffic.

---

## 2. The three actions

### 2.1 Action A — «Проверить на новом тексте»

Endpoint: `POST /trust-lab/action-a`

Input: arbitrary text (and an optional consumer). The response shows:

- **Decisions**: detector, rule, context type, and the related entity — without
  revealing extraneous data.
- **Transformed text** and the **exact restoration** with a **character-level
  comparison** (`exact_round_trip`).
- **Real stage timings** (`mask`, `unmask`).
- **Config / detector versions** (`policy_version`, `detector_manifest_version`).

In **synthetic labeled mode** (`labeled: true`), the response additionally runs
**«Вариации»**: 10 predefined transformations (uppercase, lowercase,
NBSP/spaces, dash variants, bracket wrap, field reorder, prefix/suffix, repeated
value, chunk boundary, emoji/combining marks) with a **reproducible seed** and
**transferred gold spans**. It shows **real results including failures** — a
variation that misses an entity or breaks round-trip is reported as `failed`,
not hidden.

For **arbitrary text without labeling** (`labeled: false`), the system does
**not** compute a fake accuracy. It reports:

> «Эталонная разметка не задана; проверены round-trip и перечисленные инварианты»

### 2.2 Action B — «Проверить изменение политики до включения»

Endpoint: `POST /trust-lab/action-b`

The candidate policy is compared against the **active** policy on the **same
labeled regression set** (`app/trust_lab/regression.py`, 17 categories). The
response shows:

- **Misses / overmasks / round-trip / time deltas** between active and candidate.
- **Regressions in red** — not just improvements. A candidate that removes a
  category from `detect_types` shows `missed entities +N`.
- A note that only the specific tests run were checked; it does **not** claim the
  candidate is «safe for any text».

The **previous policy continues serving** until explicit activation — Action B
never activates the candidate.

### 2.3 Action C — «Проверить отказ»

Endpoints: `POST /trust-lab/action-c` and `POST /trust-lab/action-c/recover`
(both **admin-only**).

This is a **synthetic demo context** that makes a mandatory detector or the
Vault temporarily unavailable. The response shows:

- `Статус: обработка остановлена`
- `Причина: обязательный компонент недоступен`
- `Вызовов транспортного адаптера LLM в этом сценарии: 0`
- `Исходный запрос наружу не отправлялся этим адаптером`

The call count comes from the **actual instrumented transport**
(`app/trust_lab/transport.py`), not a UI constant.

**Safety guarantees:**

- Fault injection is **admin-only** (`X-Admin-Key` header matching
  `PII_TRUST_LAB_ADMIN_KEY`) **and** requires `PII_TRUST_LAB_FAULTS=1`.
- It is **disabled in the normal evaluator profile** (returns `403`).
- It is **not reachable via `/process`** — there is no fault-injection query
  parameter on the public endpoint (R52).

---

## 3. The «Паспорт проверки» safe report

Endpoint: `POST /trust-lab/report`

A compact, safe report (`app/trust_lab/report.py`) with these fields:

`run_id`, `created_at`, `build_version`, `policy_version`,
`detector_manifest_version`, `dataset_or_scenario_id`, `seed`, `input_length`,
`token_counter_type`, `detected_counts`, `masked_counts`, `check_statuses`,
`exact_round_trip`, `upstream_calls`, `timings`, `degraded_components`,
`known_limitations`, `include_synthetic`, `disclaimer`.

**By default the report excludes raw PII**: no token values, no original text,
no raw hashes of personal strings, no Vault mapping, no API keys.

**Synthetic examples are exportable only** via `include_synthetic: true`, which
is a separately-marked mode.

The report is **not** a cryptographic proof of anonymity, a compliance
certificate, or formal verification — this is stated in the `disclaimer` field.

---

## 4. Acceptance criteria (section 17.4) and how each is met

| Criterion | How it is met |
| --------- | ------------- |
| Reproducible decisions | Same seed/build/policy → same variation statuses and masked counts (tested). |
| Exact round-trip | `exact_round_trip` computed by character-level comparison of restored vs original (tested). |
| No fake accuracy on unlabeled text | `labeled: false` returns the «Эталонная разметка не задана» note, no accuracy (tested). |
| Variations show real failures | At least one of the 10 mutations fails (lowercase name / en-dash phone / emoji) (tested). |
| Candidate compared on same labeled set | Action B runs active and candidate on the same regression set (tested). |
| Regressions shown, not hidden | Removing a category yields `delta_missed > 0` and a red regression (tested). |
| Previous policy keeps serving | Action B does not activate the candidate; `/demo/mask` still masks FULL_NAME (tested). |
| Failure stops processing, zero upstream | Action C reports `status: stopped`, `upstream_calls: 0`, `request_sent_out: false` (tested). |
| Call count from real transport | Counted by `TransportCounter` wrapping the stub provider (tested). |
| Fault injection admin-only + disabled by default | Requires `X-Admin-Key` + `PII_TRUST_LAB_FAULTS=1`; `403` otherwise (tested). |
| Not reachable via `/process` | `/process?fault=detector` still works and masks normally (tested). |
| Safe report excludes raw PII | Canary secret and PII values absent from report JSON (tested). |
| Report has required fields | All documented fields present (tested). |
| Worker change keeps tokens valid | Mask on worker 1, unmask on worker 2 with a shared Vault restores exactly (tested). |

---

## 5. How to run the Trust Lab

### Endpoints

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/trust-lab/action-a` | «Проверить на новом тексте» |
| POST | `/trust-lab/action-b` | «Проверить изменение политики до включения» |
| POST | `/trust-lab/action-c` | «Проверить отказ» (admin-only) |
| POST | `/trust-lab/action-c/recover` | Clear injected faults (admin-only) |
| POST | `/trust-lab/report` | «Паспорт проверки» (safe report) |

### Environment variables

- `PII_TRUST_LAB_ADMIN_KEY` — admin key required for Action C and recover.
- `PII_TRUST_LAB_FAULTS=1` — enables fault injection. Without it, Action C
  returns `403` even with a valid admin key.

### Example

```bash
# Action A on arbitrary text (no fake accuracy)
curl -s -X POST http://localhost:8000/trust-lab/action-a \
  -H 'Content-Type: application/json' \
  -d '{"text":"Клиент Иван Петров, email ivan@example.com","consumer":"support_demo","labeled":false}'

# Action C (admin-only, faults enabled)
curl -s -X POST http://localhost:8000/trust-lab/action-c \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Key: <PII_TRUST_LAB_ADMIN_KEY>' \
  -d '{"consumer":"support_demo","text":"Клиент Иван Петров","fault_type":"detector","detector_id":"full_name"}'
```

The Trust Lab is also wired into the web UI (`app/static/index.html` +
`app/static/app.js`) served at `/`.
