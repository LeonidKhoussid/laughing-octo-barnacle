# Latest overload-isolation repair

See [overload-repair.md](overload-repair.md) for the current change and new
before/after measurements. The repaired four-CPU container reached 67.06 correct
RPS in a five-minute diagnostic at 330 scheduled RPS, with remaining 429 and
transport errors. This does not establish 1000 successful RPS. Earlier reports
below describe their recorded code/configuration, not the current repair.

# Current performance verification

## Final source-ZIP verification

After the postcode fix and admission limit of eight, the source-only ZIP built
successfully into image
`sha256:d20175faae43781ec5937d2838d4c0d4a726c7f6b1b51de00b68e365522f4779`.
The final container still used Linux/arm64, four-CPU quota, 6 GiB memory,
one worker, MemoryVault, both original models and the unchanged privacy thresholds.
Fresh IDs/text and ready restorations were used; client concurrency was eight.

| Final workload | Duration | Correct / attempted | Correct RPS inside window | Overall HTTP p95 | Mask HTTP p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Required synthetic fields, all 17 categories | 20 s | 9587 / 9587 | 478.95 | 31.66 ms | 34.0 ms |
| Required fields + public references + mixed records | 30 s | 407 / 407 | 13.30 | 2833.56 ms | 2932.1 ms |

All counters reconcile. Neither run had correctness, HTTP, malformed-response,
timeout or transport failures. Each finished eight additional requests during
drain, excluded from the RPS column, and left nine successful masks without a
sampled restore. This verifies the corrected synthetic cases under closed load;
it does **not** show that the earlier fixed-rate overload is resolved, reproduce
the judge's ramp/retries, or establish 1000 successful RPS or representative
95% quality. The required-field and context-heavy rates are different workloads,
not a before/after speedup.

Evidence: `artifacts/docker-source-zip-final-closed20s-required.json`,
`artifacts/docker-source-zip-final-closed30s-mixed.json`, and
`artifacts/docker-source-zip-final-metadata.json`.
One additional 100,082-token HTTP pair completed with mask 200 in **8.3486 s**,
restore 200 in **0.5631 s**, correct concealment and exact restoration. No load
ran concurrently. That is one functional observation within the tester's
10-second timeout, not the one-second target or a percentile:
`artifacts/requirements-container-100k.json`.

## Requirements recheck: container baseline, 23 September 2026

The initial requirements-check image was built from the application and production React sources,
including the new request deadlines, stage telemetry, field regressions and
body-size guard, before the postcode/admission follow-up documented below.
The developer machine is Apple Silicon; the Docker workload
used Linux/arm64 with a **four-CPU quota and a 6 GiB memory limit** inside an
8 GiB Docker VM. This is not a measurement on the user's 4 × 3.3 GHz, 8 GB server.
The generator ran on the same physical machine, outside the container.

The 20-second closed run used eight concurrent HTTP requests, fresh input and
IDs, all 17 required types (`required`), public references and mixed records,
and a ready restoration on alternate slots. It returned **281/281 correct
responses**, with 273 completions within the window: **13.65 successful RPS**.
Overall HTTP p95 was **2719.22 ms**, p99 **2890.86 ms**. Eight responses completed
during drain; nine masks remained awaiting an unsampled restore. No HTTP errors,
malformed results or incorrect returned masks/restorations occurred. Evidence:
`artifacts/docker-requirements-check-closed20s.json` and its metadata sidecar.
This result fails the throughput target and exceeds the one-second latency
guideline; passing functional tests does not change that finding.

Two bounded configuration experiments used the same workload and image:

| NER configuration | Successful RPS in window | HTTP 200 / attempts | Mask p95 |
| --- | ---: | ---: | ---: |
| 4 intra-op threads, normal spinning (current) | 13.65 | 281 / 281 | 2782.8 ms |
| 1 intra-op thread | 13.65 | 281 / 281 | 2923.5 ms |
| 4 threads, NER-only spinning disabled (temporary file mount) | 14.50 | 298 / 298 | 2866.4 ms |

The one-thread setting brought no throughput gain. The no-spin result is one
small throughput difference with no better mask p95; it has **not** been adopted.
These tests do not establish thread oversubscription as the principal cause.
The mask/context path remains expensive; restoration and HTTP dispatch are much
cheaper. Neither experiment bypassed the models, cached raw text between requests,
changed thresholds, or modified the active model weights. Evidence:
`artifacts/docker-requirements-ner1-closed20s.json` and
`artifacts/docker-requirements-ner4-nospin-closed20s.json`.
For the meaning of the tested thread controls, see the
[ONNX Runtime threading documentation](https://onnxruntime.ai/docs/performance/tune-performance/threading.html).

The large-document fixture is now measured by the pinned NER tokenizer:
**100,082 tokens**, 659,119 characters and 1,223,987 UTF-8 bytes. The previous
chars/4 fixture actually contained 60,897 tokens with this tokenizer. Both
new end-to-end tests pass with beginning/middle/end PII and exact restoration.
Four NER threads reduced the isolated NER measurement from 6.778 to 2.427 seconds
on the Mac, with span parity at batch size eight; alternative batch sizes were
rejected because their output spans changed. Request-local name-role memoization
reduced its isolated detector measurement from 1.159 to 0.912 seconds. These
are local component timings, **not** HTTP throughput or one-second-SLA evidence.
See `artifacts/ner-thread-batch-benchmark-2026-09-23.{json,md}` and
`artifacts/requirements-detection-person-{baseline,after}.json`.

### Five-minute overload diagnostic and follow-up repair

The initial image (before the postcode boundary fix, admission limit 64) was
also tested for 300 seconds at a configured 1000 RPS, 200 client connections,
`required,public,mixed`, with ready restorations and a 10-second client timeout.
It scheduled 300,000 slots but launched only **25,018 HTTP requests**; 29,531
slots were late and 245,451 exceeded client capacity. There were **22,661 HTTP
429 responses, 878 timeouts, 3 transport errors**, and 1476 HTTP 200 responses.
Of those 200 responses, **29 failed the correctness oracle**. Only 1404 correct
responses finished within the window (**4.68 successful RPS**); 1447 were correct
including drain. The container stayed healthy with no OOM events. This is a
**failed load/correctness run**, not a performance pass. Evidence:
`artifacts/docker-requirements-open1000-5min.json` and its metadata sidecar.

A 30-second reproduction recorded seven failing ADDRESS masks. Every one also
failed sequentially: the postcode regex incorrectly treated six digits inside
an alphanumeric control identifier as a postcode. All six real address values
were masked, but an extra identifier fragment was masked too. The direct
substring check used in the first replay diagnosis was invalid because a short
value such as `8` can occur inside an opaque token; that diagnosis was corrected.
The generator, expected values and footer remain unchanged. The application now
uses Unicode-aware identifier boundaries, with Latin/Cyrillic identifier and
legitimate-postcode regressions. The original five-minute report retains all
29 failures; the seven-case reproduction does not retrospectively identify
every one of those 29 responses.

The benchmark now keeps bounded safe examples separately for each failure type,
so early 429 responses cannot hide later correctness failures. It stores no
request/response bodies. A separate 30-second admission-limit comparison on the
old image reduced the limit from 64 to 8: no timeouts were recorded, versus 107
in the preceding 30-second run at 64, but six transport errors and 3028 HTTP 429
responses remained. Only 181 correct responses finished within the window
(6.03 RPS; p95 including drain 5360 ms). Source and workload were not an identical
paired experiment, so this is a configuration diagnostic, not a precise speedup.
The new default of **8 in-flight requests** bounds concurrent model work on the
four-CPU baseline. It does not solve the 1000-RPS requirement. Evidence:
`artifacts/docker-admission8-open1000-30s.json` and its metadata.

The sections below document earlier source states and narrower workloads; their
RPS values must not be substituted for the current container measurement above.

Organizer clarification reviewed after these measurements: [chat review](chat-review-2026-09-23.md), messages #666–667, describes ramped load averaging roughly 330 RPS with peaks of 1000, equal mask/restore counts, and up to 200 connections. HTTP 429 is recorded separately and is not considered an invalid-request error by the organizer. The harness's generic `errors` count includes non-200 responses; use `status_counts` to separate 429 from invalid responses. Neither rejected nor unissued requests count as successful throughput. Exact judge traffic remains unspecified; the fixed-rate mask-heavy runs below remain stress diagnostics, not an exact reproduction of that schedule.

**The 1000 RPS target is not demonstrated.** The tables below labelled G3/G5/G8
are historical reports from before the local RuBERT and mDeBERTa pipeline was
enabled. They do not measure the current service and must not be used as its
capacity or latency claim. Historical clean-install/container results also do
not verify the current package. Current model quality and sequential checks
are described in [semantic-check.md](semantic-check.md).

## Measured optimization results — 2026-09-23

Paired HTTP tests used Apple M1 Pro (8 CPU cores), 16 GiB RAM, macOS 15.5
arm64, Python 3.14.6, one Uvicorn worker, MemoryVault, and one ONNX inference
thread. The load generator ran on the same machine. The semantic detector was
enabled throughout: RuBERT NER plus the unchanged **FP32 mDeBERTa** context model.
Server in-flight capacity was 64. Source/config hashes and measured settings are
recorded in [performance-server-after.json](../artifacts/performance-server-after.json);
the server was restarted after source capture. The older baseline reports have
`metadata.server.verified: false` and therefore do not independently attest
the baseline process identity; they were collected before the source restart
in the same local comparison.

These are short developer-machine comparisons, not a sustained acceptance test
or a capacity claim for an x86 VPS. Fresh IDs and unique synthetic text prevent
replay from being counted as fresh masking. The private workload contains a
client name, phone and email (roughly 140 characters). The mixed workload adds
a public scientific/literary sentence (roughly 207 characters).

### Closed load: 10 seconds, 8 concurrent requests, masking only

RPS below counts correct responses **completed inside the 10-second window**.
Each run also completed 8 final requests during drain; these are excluded from
that RPS. p95 includes every successful request's HTTP duration, including those
finishing during drain. No HTTP errors or correctness failures occurred in
these four runs.

| Workload | Before successful RPS | After successful RPS | Before p95 | After p95 | Total correct requests before / after |
|---|---:|---:|---:|---:|---:|
| Private client details | 502.4 | 649.2 | 24.12 ms | 17.70 ms | 5032 / 6500 |
| Public context plus private details | 44.2 | 80.5 | 418.43 ms | 192.12 ms | 450 / 813 |

Evidence: [before private](../artifacts/performance-before-private.json),
[after private](../artifacts/performance-after-private.json),
[before mixed](../artifacts/performance-before-mixed.json),
[after mixed](../artifacts/performance-after-mixed.json). The observed increases
are about 29% and 82%, respectively; neither workload reached 1000 RPS.

### Open-load attempt: configured 1000 requests/s, 10 seconds

The generator scheduled 10,000 HTTP slots per run with a client in-flight limit
of 128. **This did not produce 1000 actual arriving requests/s.** The same-host
generator dropped most slots as late or over its own capacity. The configured
rate must not be presented as traffic delivered to the server.

| Measurement | Before | After |
|---|---:|---:|
| Scheduled request slots | 10000 | 10000 |
| Generator late drops | 7789 | 9110 |
| Client capacity drops | 1811 | 0 |
| HTTP attempts | 400 | 890 |
| Correct responses, including drain | 400 | 863 |
| HTTP 429 responses | 0 | 27 |
| Correct completions within window | 366 | 786 |
| Correct completions/s within window | 36.6 | 78.6 |
| Completed HTTP requests during drain | 34 | 101 |
| Successful HTTP latency p95 | 3912.53 ms | 817.39 ms |
| Successful latency p95 from scheduled slot | 3963.33 ms | 866.39 ms |

Both reports reconcile all request/drop counters. There were no malformed
responses or incorrect masks among returned 200 responses. The after-run still
had **27 real HTTP 429 errors**, and its successful HTTP p99 was **1045.66 ms**.
This is an overload diagnostic, **not a passed 1000 RPS / one-second SLA test**.
Evidence: [before open load](../artifacts/performance-before-1000-mixed.json),
[after open load](../artifacts/performance-after-1000-mixed.json). A separate
load-generator host and a longer run are required to evaluate actual offered
load without this co-located scheduling limitation.

### Mask/restoration mix: 20 seconds, 8 concurrent requests

The final mixed run used all three cases (private, public, mixed) and offered a
ready restoration on alternate slots. Actual operations were **2079 fresh masks
and 2070 restorations**, totaling **4149 correct responses**, with zero HTTP
errors and zero correctness failures. Four early restoration slots had no
completed mask available and therefore generated fresh masks. Nine successful
masks remained without a sampled restoration; those are not claimed as verified
round trips.

There were 4141 correct completions inside the window: **207.05 requests/s**,
with 8 further completions during drain. Overall HTTP p95 was **137.85 ms**;
mask p95 **152.93 ms**, restore p95 **22.95 ms**. This higher mixed RPS includes
cheap restores and must not be substituted for fresh-mask capacity. Evidence:
[performance-after-restoration-mix.json](../artifacts/performance-after-restoration-mix.json).

### What changed without removing semantic protection

- MemoryVault now tracks expiry with a generation-checked heap and maintains
  capacity counters, instead of scanning all live entries on each operation.
  Expiry, overwrite, byte accounting and lifecycle behavior remain tested. Heap
  compaction is amortized; expiry of a large cohort still requires processing
  that cohort. The isolated [vault microbenchmark](../artifacts/vault-performance.json)
  is not an HTTP throughput result.
- Context inference stops testing a span's remaining hypotheses once the
  existing acceptance condition has succeeded. This preserves the same logical
  decision rather than weakening thresholds or skipping mandatory NER. Across
  96 regression cases, output spans were identical in 96/96 comparisons and
  both versions restored every case exactly. Two passes reduced evaluated NLI
  pairs from **342 to 156**; NER still ran **192 times** in each version. Evidence:
  [semantic-short-circuit-comparison.json](../artifacts/semantic-short-circuit-comparison.json).
  This is regression equivalence on that corpus, not proof for all future text.
- Metrics retain at most the **8192 most recent observations per histogram**.
  Counts and means cover the full process lifetime; exposed p50/p95/p99 cover
  only that recent window (`percentile_scope: most_recent_observations`). The
  benchmark's percentiles instead use all observations from the individual run.
- `/process` acquires bounded admission before worker-pool queuing. Cancellation
  before work starts releases capacity and prevents later execution; cancellation
  after work starts retains capacity until its worker finishes. This prevents
  abandoned requests from bypassing admission. Queuing time is included in the
  deadline, and overload remains an explicit retryable response.

An INT8 mDeBERTa candidate was evaluated separately and **rejected**: its output
spans differed from FP32 in 44/96 regression cases and introduced additional
overmasking. The running service retains FP32; no speed number depends on
accepting that quality regression. Evidence:
[nli-quantization-comparison.json](../artifacts/nli-quantization-comparison.json).

## Current HTTP benchmark

Use `scripts/benchmark_process.py` against a separately started server with the
normal semantic detector enabled. Use a separate load-generator machine for
server-capacity measurements; co-located runs share CPU and memory. For example:

```bash
.venv/bin/python scripts/benchmark_process.py --base-url http://127.0.0.1:8012 --mode closed --duration 30 --concurrency 8 --mix private,public,mixed --output artifacts/process-closed.json
.venv/bin/python scripts/benchmark_process.py --base-url http://127.0.0.1:8012 --mode open --duration 30 --rps 1000 --concurrency 128 --mix private,public,mixed --output artifacts/process-open-1000.json
```

These are reproduction commands, not evidence that the target passed.

- Closed mode measures completed work at fixed concurrency. Open mode schedules
  actual HTTP request slots independently of response completion. `--rps 1000`
  is a requested arrival rate, not a successful-throughput claim.
- Client concurrency is bounded. Late generator slots and slots dropped at the
  client capacity limit are reported separately from HTTP requests and errors.
- Fresh masks use unique synthetic text and `payload_id`; there are no HTTP
  retries or reused cached masks. `--restore-every 1` offers ready restorations
  on alternate slots and records the actual mask/restore mix.
- The oracle checks complete replacement of the designated private fragments,
  exact preservation of all other text, and exact restoration. Malformed 200
  responses and wrong output are errors, not successful RPS. This narrow
  synthetic oracle is not representative accuracy measurement.
- Successful completions within the measurement window and completions during
  drain are separate. Latencies of successful responses exclude fast HTTP
  failures; scheduled latency also includes generator dispatch delay.
- Reports include request mix/lengths, generator hardware, local model/config
  hashes and optional `--server-metadata` JSON. Local hashes are not proof of
  the remote server configuration; record and verify that server separately.

The older `loadtest.py --profile process` and `process_sustained` profiles are
**legacy sequential contract diagnostics**. They ignore `--workers` and have
one request in flight. Their `--rps` caps mask-loop starts, with extra restore
requests, so it is not independent offered HTTP load. They now report
`offered_rps: null`, `load_model: legacy_sequential`, and the configured mask
start ceiling separately. They cannot establish a 1000 RPS capacity.

---

# Historical independent quality benchmark (G3, before BERT)

This document describes the independent quality evaluation of the AlfaGen PII
Gateway against labeled synthetic fixtures. It is a **local diagnostic** tool,
not the organizers' official scoring formula (see master prompt section 16.2).

## 1. Methodology

We measure detection quality on an **independent labeled synthetic corpus** whose
ground truth is produced from templates, **not** from detector predictions
(master prompt 16.1, R62). The oracle is therefore independent of the detector
logic.

Pipeline:

1. `scripts/generate_fixtures.py` — builds the labeled corpus and writes
   `artifacts/fixtures.json`.
2. `scripts/evaluate.py` — runs the engine on every example, computes per-category
   and overall metrics, and writes `artifacts/evaluation_report.json`.

A fixed seed (`20260922`) makes the corpus reproducible.

## 2. Fixture generator

`scripts/generate_fixtures.py` produces examples for all 17 categories. Each
example carries:

- `text`: the synthetic input string.
- `gold_spans`: list of `{start, end, category}` — the TRUE sensitive spans in
  Unicode code-point coordinates.
- `category`: the primary category under test.
- `scenario`: a short label (`positive`, `negative`, `case_variant`, `nonspace`,
  `overlap`, `bare_value`, `typo_checksum`, `historical_person`,
  `office_vs_residential`).

The corpus includes:

- **Positive** examples per category with several variants (case, spaces/NBSP,
  dashes, words vs digits, Cyrillic/Latin where relevant).
- **Negative** examples that must NOT be masked (ordinary dates, ordinary country
  mentions, "забыл PIN-код", port/year/order codes, trading organizations,
  historical person "Пушкин" in non-client context, office address).
- **Context disambiguation** (birth date vs issue date vs ordinary date in one text).
- **Overlap** (multiple PII types in one text; FULL_NAME vs CARDHOLDER_NAME).
- **Bare-value** examples where the type is reasonably determinable.
- **Explicitly-signed values with typos / invalid checksum** (must still be masked).
- **Historical person vs client namesake**; **office vs residential address**.

All values are synthetic; no real PII is used.

## 3. Metrics (LOCAL DIAGNOSTIC — not the official formula)

These formulas are our diagnostic tool (master prompt 16.2), **not** the
documented hackathon formula. Empty denominators are handled explicitly.

Let `G` be the set of gold sensitive character positions and `P` the set of
actually-masked character positions.

```
concealment_recall      = |G ∩ P| / |G|
span_character_precision = |G ∩ P| / |P|
non_pii_overmask_rate    = |P \ G| / |all_positions \ G|
```

Additional metrics:

- **Entity precision / recall / F1** at exact span match, per category and
  macro-aggregated.
- **Boundary accuracy**: fraction of predicted spans that exactly match a gold span.
- **Round-trip accuracy**: `unmask(mask(x)) == x` for every positive example.
- **Fully / partially missed** sensitive entities.
- **Fraction of requests** with at least one missed labeled sensitive fragment.

## 4. Per-category results

Run: `.venv/bin/python scripts/evaluate.py` (seed `20260922`, 82 examples).

| Category             | P     | R     | F1    | Conceal | SpanP |
|----------------------|-------|-------|-------|---------|-------|
| FULL_NAME            | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| BIRTH_DATE           | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| BIRTH_PLACE          | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| PASSPORT             | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| CITIZENSHIP          | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| PASSPORT_ISSUER      | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| DEPARTMENT_CODE      | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| PASSPORT_ISSUE_DATE  | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| DRIVER_LICENSE       | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| ADDRESS              | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| EMAIL                | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| PHONE                | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| INN                  | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| CARD                 | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| CVV                  | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| PIN                  | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| CARDHOLDER_NAME      | 1.000 | 1.000 | 1.000 | 1.000   | 1.000 |
| **MACRO**            | 1.000 | 1.000 | 1.000 | —       | —     |

Global character-level (local diagnostic):

- concealment_recall = 1.000
- span_character_precision = 1.000
- non_pii_overmask_rate = 0.000

Round-trip accuracy: 82/82 = 1.000. Fully missed entities: 0, partially missed: 0.
Requests with ≥1 missed fragment: 0/82. Boundary accuracy: 77/77 = 1.000.

## 5. Detector bugs found and fixed

During the independent evaluation the following detector bugs were found and
fixed (each with a regression test in `tests/unit/test_detectors.py`):

1. **FULL_NAME** — the sensitive span included the service word "Клиент"
   (violates R13). Fixed to start at the actual name. Also added support for
   all-uppercase names (`ИВАН ИВАНОВИЧ ПЕТРОВ`) and stopped claiming names in
   cardholder context (those belong to CARDHOLDER_NAME).
2. **BIRTH_DATE** — the wide context window marked the passport issue date as a
   birth date in "дата рождения 12.04.1990, паспорт выдан 15.03.2015". Fixed to
   only fire on the nearest date to a birth context.
3. **PASSPORT** — the series regex required 4 consecutive digits, missing a
   series with a space ("45 12"). Fixed to allow a space/dash in the series.
4. **PASSPORT_ISSUER** — the "г." abbreviation truncated the issuing authority
   name ("УФМС России по г. Москве" → "УФМС России по г"). Fixed to treat
   abbreviations as tokens.
5. **PASSPORT_ISSUE_DATE** — an ordinary date with neutral context ("встреча
   20.06.2020") was misclassified as an issue date. Added a neutral-context guard.
6. **ADDRESS** — one "офис" marker suppressed a later client address in the same
   text (violates master prompt 9.1). Fixed so a personal-address marker between
   the office marker and the component overrides the office classification.
7. **BIRTH_PLACE** — "Поэт ... Пушкин родился в Москве" was masked as a birth
   place (historical context, violates R15). Added a historical-context guard.

## 6. Historical detector limitations

The following describes the earlier detector-only build; the current NER/context
pipeline and its limitations are documented in `semantic-check.md`.

- The corpus is synthetic and template-based; it does not cover every real-world
  phrasing. A perfect score here does **not** prove safety on arbitrary input.
- The metrics are **local diagnostic** and are not the organizers' official
  formula (master prompt 16.2).
- Historical-person detection for BIRTH_PLACE relies on a profession-word guard
  and may not cover all historical contexts.
- FULL_NAME is a heuristic (dictionary + context), not a full NER; uncommon names
  or unusual grammar may be missed.
- The ADDRESS detector handles a fixed set of component patterns; complex
  addresses (buildings, fractions, "8 Марта" streets) are only partially covered.

---

# Historical G5 — Safe Telemetry, 100k Handling, and Load Testing

This section documents the G5 work: safe logs/metrics/limits, 100k-token
handling, and the reproducible local load-testing tool. All numbers below are
**LOCAL DIAGNOSTIC** measurements on this machine, **not** the organizers'
official scoring formula (master prompt 14.5, 16.2).

## 1. Safe telemetry (logs / metrics / limits)

- **SafeLogger** (`app/observability/logs.py`) logs only an allowlist of fields
  (`request_id` server-generated, `operation`, `consumer`, `policy_version`,
  `stage`, `detected_counts`, `degraded`, `result`). It never logs original or
  masked body, vault mappings, keys, auth headers, or raw `payload_id`.
- **Metrics** (`app/observability/metrics.py`) uses bounded-cardinality label
  enums (`operation`, `status`, `type`, `stage`, `reason`, `provider`,
  `result`). Any label value outside the enum is dropped, so a stray caller
  cannot inflate cardinality or leak content into a label. Histograms now report
  mean/p50/p95/p99.
- **Limits** (`app/security/limits.py`): UTF-8-aware body size limit,
  in-flight backpressure, and a deadline. On overload the routers return a
  retryable `503` with a valid integer-seconds `Retry-After` (R57); a body over
  the limit returns `413`. Validation errors never reflect input values (R52).

Canary tests in `tests/integration/test_g5_safety.py` inject a synthetic canary
into a request and assert it never appears in logs, metric label values, error
responses, or tracebacks. `tests/unit/test_limits.py` covers the limits.

## 2. 100k-token handling

The engine processes the whole document in one pass (no silent truncation).
`tests/integration/test_100k.py` builds a ~100k-token document (approximate
tokenizer **chars/4, marked `estimated`** — not the organizers' tokenizer, R61)
with sensitive fragments at the beginning, middle, and end, and asserts:

- the document is processed without crashing;
- all sensitive fragments (start/mid/end) are masked;
- round-trip `unmask(mask(x)) == x` is exact.

Result: **PASSED** — the 100k document is fully processed, end-of-document
fragment is masked, and round-trip is exact. This is a **functional** result,
separate from throughput (R60).

## 3. Load-test tool

`scripts/loadtest.py` is a reproducible local load-testing tool:

```
.venv/bin/python scripts/loadtest.py --profile smoke
.venv/bin/python scripts/loadtest.py --profile uniform --rps 330 --duration 10
.venv/bin/python scripts/loadtest.py --profile ramp
.venv/bin/python scripts/loadtest.py --profile sustained --rps 1000 --duration 15
.venv/bin/python scripts/loadtest.py --profiles smoke,uniform,mix,retries
```

Features:

- seed, config, and an aggregated report saved to `artifacts/load_report.json`;
- keep-alive connections (each worker owns a dedicated `httpx.Client`);
- distinguishes **closed-model** load (fixed concurrency, measures max
  throughput) from **fixed-intensity** load (rate-limited, measures latency
  under offered load);
- profiles: `smoke`, `uniform` (~330 RPS), `ramp` (up to 1000 RPS, ~200
  connections), `sustained` (1000 RPS), `mask_heavy`, `unmask_heavy`, `mix`,
  `large`, `mixed_length`, `retries`;
- unmask contexts are pre-created so unmask-heavy profiles do not measure
  "context missing" errors as work (14.5.5);
- latency mean/p50/p95/p99 are aggregated across **all** observations, never
  averaged per worker (14.5).

## 4. Historical reported measurements (before BERT; not current capacity)

Environment: macOS 15.5 arm64, 8 CPU, 16 GiB RAM, Python 3.14.6, 1 uvicorn
worker (MemoryVault) unless noted, policy `baseline-001`, in-flight limit 64 (default `PII_MAX_IN_FLIGHT`; configurable via env).

| Profile | Attempts | Success | Errors | 429 | Completed RPS | Mask p50/p95/p99 (s) | Unmask p50/p95/p99 (s) |
|---|---|---|---|---|---|---|---|
| smoke | 6 | 6 | 0 | 0 | 588 | 0.005 / 0.005 / 0.005 | 0.002 / 0.002 / 0.002 |
| uniform (330 RPS) | 2027 | 2027 | 0 | 0 | 334 | 0.007 / 0.013 / 0.029 | 0.006 / 0.010 / 0.017 |
| mask_heavy | 1995 | 1995 | 0 | 0 | 329 | 0.009 / 0.016 / 0.019 | — |
| unmask_heavy | 2053 | 2053 | 0 | 0 | 339 | 0.004 / 0.004 / 0.013 | 0.005 / 0.009 / 0.017 |
| mix | 2026 | 2026 | 0 | 0 | 334 | 0.008 / 0.015 / 0.019 | 0.007 / 0.013 / 0.020 |
| retries | 8 | 8 | 0 | 0 | 675 | 0.004 / 0.004 / 0.004 | 0.003 / 0.003 / 0.003 |
| sustained (1000 RPS) | 7827 | 7827 | 0 | 0 | **957** | 0.031 / 0.122 / 0.131 | 0.044 / 0.120 / 0.128 |
| ramp (peak 1000 RPS) | 5612 | 5612 | 0 | 0 | 413* | 0.011 / 0.316 / 0.339 | 0.009 / — / — |
| large (100k, 100 RPS offered) | 17 | 17 | 0 | 0 | 1.9 | 8.03 / 8.24 / 8.24 | — |
| large_lowrate (100k, 5 RPS) | 19 | 19 | 0 | 0 | 2.1 | 1.74 / 2.24 / 2.24 | — |
| mixed_length (90% short / 10% 20k) | 484 | 484 | 0 | 0 | 76 | 0.092 / 0.361 / 0.451 | — |
| sustained_redisvault_2workers | 1250 | 1250 | 0 | 0 | 108 | 1.46 / 2.70 / 3.01 | 0.168 / 0.643 / 0.843 |

\* ramp `completed_rps` is the average across all ramp steps (100→330→600→1000
RPS); the peak 1000 RPS step achieved ~650 RPS.

Findings recorded at that historical stage (not revalidated here):

- **MemoryVault (1 worker) sustains ~957 RPS** at 1000 RPS offered, with mask
  p50 ~31 ms and p99 ~131 ms in that old report. This does not establish a
  capacity ceiling or current throughput.
- **Ramp** reproduces the organizers' described profile (up to 1000 RPS, ~200
  connections); the peak step reached ~650 RPS.
- **Large 100k-token mask** is functional but slow: ~1.74 s p50 at low rate
  (above the 1 s SLA). At high offered rate it degrades to ~8 s due to
  starvation.
- **mixed_length** shows starvation: mixing 10% long texts with short ones
  raises short-request p50 from ~3 ms to ~92 ms.
- **RedisVault (2 workers) is much slower** (~108 RPS) because
  `ensure_capacity()` performs a full Redis `SCAN` over all keys on every mask
  (O(N) per request). Single-request mask is ~70 ms vs ~3 ms for MemoryVault.
- **MemoryVault degrades as it accumulates entries**: `_purge_expired()` is
  O(N) on every operation. On a fresh server ~957 RPS; after accumulating many
  entries it drops to ~418 RPS.

## 5. Known bottlenecks

1. **RedisVault `ensure_capacity()` full SCAN** — O(N) per mask; makes the
   multiworker path ~9x slower than MemoryVault. Needs a cached counter or a
   bounded scan.
2. **MemoryVault `_purge_expired()` O(N)** — degrades throughput as entries
   accumulate.
3. **100k-token mask ~1.7 s** — above the 1 s SLA; the engine is single-pass
   and not chunked, so a single large document is CPU-bound.
4. **Long-text starvation** — mixing long and short requests degrades
   short-request latency significantly.

All numbers are **LOCAL DIAGNOSTIC** and not the organizers' official formula.

---

# Historical G8 — Previous package verification

This section preserves a previous G8 report. Its load, ZIP and container checks
predate the model integration and do not verify the current build.

## 1. Historical load numbers (LOCAL DIAGNOSTIC)

The load numbers in the G5 section above were reported for an earlier build
(2026-09-22, macOS 15.5 arm64, 8 CPU, 16 GiB RAM, Python 3.14.6). Key figures:

- **MemoryVault (1 worker) sustains ~957 RPS** at 1000 RPS offered, mask p50
  ~31 ms, p99 ~131 ms.
- **Ramp** reproduces the organizers' described profile (up to 1000 RPS, ~200
  connections); the peak step reached ~650 RPS.
- **Large 100k-token mask** is functional but slow: ~1.74 s p50 at low rate
  (above the 1 s SLA); ~8 s at high offered rate.
- **RedisVault (2 workers)** sustains ~108 RPS (O(N) capacity scan bottleneck).
- Zero errors, zero 429s, zero 5xx, zero timeouts across all profiles.

## 1a. Historical sequential /process diagnostic (not offered-load evidence)

Historical sequential test against the official `POST /process` contract
(`{payload, payload_id} → {result}`), mask-heavy (RPS = requests, not pairs),
reusing dataset texts with distinct payload_id per pair. Report:
`artifacts/process_load_report.json`.

**IMPORTANT — accounting fix (review issue 1):** the previous report
double-counted attempts/successes (4 HTTP requests → 8 reported). The loadtest
now counts at one authoritative location (`_finalize` from recorded
observations). The old report is retained as historical but is INVALID for
throughput claims. The fresh report reconciles: attempts == successes == latency
observations.

- **60 s, configured mask-loop ceiling 1000/s; actual offered HTTP rate was not measured**:
  22885 attempts, 22885 successes, 0 errors,
  0 429s, 0 5xx, 0 timeouts, 0 correctness failures.
- **22885 latency observations** (matches attempts — no double-counting).
- **Process latency**: mean 2.60 ms, p50 2.37 ms, p95 4.64 ms, p99 5.37 ms —
  recorded at that sequential workload, not at 1000 RPS or with BERT enabled.
- **Completed RPS ~381** in that sequential run. The test couples request
  arrival to completion, so it cannot distinguish generator and service limits
  or establish service headroom. Low single-request latency is not proof that
  concurrent requests can sustain 1000 RPS.
- **Exact restoration**: 0 correctness failures (all sampled mask→demask pairs
  restored exactly).
- This run is not a representative subset of a 1000 RPS open-loop test.
  Reconciled counters fix double-counting; they do not fix the sequential load model.

## 2. Clean-run verification (G8)

A fresh temp directory was created, the source (app/, configs/, scripts/,
tests/, docs/, pyproject.toml, README.md, .env.example, .gitignore) was copied
(no .venv, caches, artifacts, or .env), a venv was created, deps installed
(`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`), fixtures were
generated (`scripts/generate_fixtures.py`), and the full suite was run.

Result: **223 passed, exit 0**. The smoke test (`scripts/smoke.py`) returned
`ROUND_TRIP_OK: True`. The app served `/` (UI, HTTP 200) and `/health`
(`{"status":"alive","ready":true}`).

The same procedure was repeated from the extracted source ZIP
(`artifacts/alfagen_source.zip`): **223 passed, exit 0**, smoke
`ROUND_TRIP_OK: True`, `/` and `/health` OK. This proves the source ZIP is
reproducible.

## 3. Container path (G8)

A `Dockerfile` and `compose.yaml` were added. The Docker image was built and
run locally; the container served `/` (HTTP 200) and `/health`
(`{"status":"alive","ready":true}`), and the smoke test returned
`ROUND_TRIP_OK: True`.

All numbers are **LOCAL DIAGNOSTIC** and not the organizers' official formula.
