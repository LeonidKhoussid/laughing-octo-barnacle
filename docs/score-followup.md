# Follow-up to the repeated scoring result

## What the supplied evidence establishes

`scoring 2.log` is identical to the previous scoring summary:
`SUCCESS, score=9844, issuesTotal=31, CRITICAL=2, MAJOR=26, MINOR=3`.
There are no issue descriptions, locations, scoring denominator or test traces.
This does not identify which defects caused the score or establish that all
31 issues concern performance. None of those issue counts is claimed fixed here.

The reported server totals were 320,909 completed HTTP requests, 16,748 successes
and 304,161 overloads: only **5.22% succeeded**. The 1,156 total RPS includes
rejections. One read-only GET to the public `/metrics` endpoint subsequently
observed 18,292 successes and 324,900 overloads, with no traffic in its then-current
60-second window. Only three recorded overloads were classified as context;
the rest were admission. Admission means request slots are occupied, not proof
that NLI alone caused the bottleneck. No public load test or deployment was
performed. Evidence: `artifacts/score-followup-input.json`.

## Changes

- Short NER inputs (at most 254 encoded tokens, one model window) use a
  single-threaded ONNX session. Longer inputs retain the configured four-thread
  session. Both load the same verified NER weights, tokenizer and labels; no
  model, privacy threshold, context rule, token window or input is skipped.
  If only one NER thread is configured, one session is reused. This adds a
  second NER session on the four-CPU configuration; the observed container
  memory after the large-input check was about 2.02 GiB, not a peak-memory bound.
- Rolling traffic metrics now distinguish success, overload and error per
  operation. Separate outcome latency histograms prevent fast rejections from
  making successful latency appear better. Existing aggregate metrics remain.
- Native model batch durations are recorded separately for NER and context,
  including failed attempts, without request text. They exclude tokenization
  and admission/worker waiting; concurrent durations are not exclusive CPU time.
- Optional `PII_LOG_FILE` preserves safe stage logs in rotating files: 100 MiB
  per file, five backups, UTC timestamps, no sampling. The systemd guide disables
  duplicate Uvicorn access lines while keeping startup errors in journald.
  File logging is optional; the default still needs no environment file.

## Controlled thread comparison

Four CPU / 6 GiB local Docker limit, same baseline image and models, 10 seconds
per workload, closed concurrency eight, ready restoration every other slot.
Only `PII_NER_THREADS` changed; trials alternated 4, 1, 4, 1.

| NER threads | Structured fixtures: correct RPS | Mixed fixtures: correct RPS |
|---|---:|---:|
| 4, first trial | 437.5 | 125.7 |
| 1, first trial | 632.3 | 240.8 |
| 4, second trial | 466.2 | 117.2 |
| 1, second trial | 587.6 | 293.8 |

All structured requests succeeded. Mixed runs still had HTTP 429s; correct RPS
excludes them. Input IDs/text were fresh, but fixture templates repeat. Ready
restoration scheduling makes the realized mix vary. Generator and Docker share
the workstation. These short comparisons justify avoiding four-thread overhead
for short requests, not a production capacity guarantee.
Evidence: `artifacts/score-followup-thread-comparison.json` and its eight reports.

Globally using one thread **failed** the 100,082-token HTTP check: 429 at 9.05 s.
That setting was not made the default. With separate short/long sessions, the
same document masked successfully in **8.54 s**, restored in **0.60 s**, concealed
the checked beginning/middle/end fields, and restored exactly. This is one
functional case below the checker timeout, still above the one-second target.
Both results are retained in `artifacts/score-followup-{ner1,adaptive}-100k.json`.

## Final code measurements

Same four-CPU / 6 GiB container, actual HTTP `/process`, unchanged synthetic
oracle, short/long sessions enabled, file logging enabled, access logging off:

| Run | Correct RPS within window | HTTP 200 total | HTTP 429 | Success p95 |
|---|---:|---:|---:|---:|
| Structured, closed 8, 15 s | 560.93 | 8,422 | 0 | 27.06 ms |
| Mixed, closed 8, 15 s | 319.80 | 4,803 | 1,912 | 22.15 ms |
| Mixed, open target 1000, 60 s | 199.30 | 11,960 | 4,999 | 34.94 ms |

No incorrect HTTP 200, timeout or transport error was recorded in these runs.
The open run launched only 16,959 of 60,000 scheduled requests: 43,041 were late
at the generator. Actual dispatch rate was 282.65 RPS, so this is **not a passed
1,000-RPS test**. Four replies finished during drain. Do not directly compare
these numbers with the live server's approximately 48 successful RPS: hardware,
inputs, offered load and generator differ. Mixed results also favor work that
is admitted; high success throughput does not mean expensive context cases all
succeeded.

The combined final model measurements show NER native batch mean 4.17 ms versus
context mean 1.054 s (includes the large-input run and warmups). In this local
configuration, 6,515 context-limit rejections and 396 admission rejections were
recorded. After short NER improves, expensive context inference remains a clear
limitation. These timings are not an estimate of independent per-request CPU
cost or universal model accuracy.

Evidence: `artifacts/score-followup-final-{required,mixed,open,metrics}.json`.
The 133,655 structured audit records contain exactly 25,196 success and 6,911
overload terminal outcomes, matching application counters; there were no error
outcomes. Field/timestamp checks passed. File bytes: 35,447,769. No full audit
log is included in the source ZIP. Evidence: `artifacts/score-followup-audit-check.json`.

## Verification and remaining work

Full suite: **590 passed, zero skipped, seven existing deprecation warnings**,
28.61 seconds, exit zero. Reports: `artifacts/score-followup-tests.{log,xml}`.
New checks cover thread routing and unchanged entity boundaries, real session
thread options, rate reconciliation/window expiry, success-only latency,
rotating file retention/no duplicate propagation, and failed inference timing.
The installed Uvicorn CLI was checked to honor `UVICORN_ACCESS_LOG=false` and
`UVICORN_TIMEOUT_KEEP_ALIVE=30` when no explicit CLI flags override them.

The known Bernard Shaw public-text false positive from the preceding repair
was not changed. Representative 95% quality, 1,000 successful RPS and one-second
100k latency are still unproven/unmet. A smaller or trained context classifier
requires an independent quality comparison, not removal of safeguards to claim
throughput. The unchanged score's 31 issues cannot be mapped from its summary.

Deploy the updated source and restart the existing systemd unit. Keep
`PII_NER_THREADS=4`: this now applies to long inputs, while short inputs use one
thread automatically. For loss-resistant request logging, apply the explicit
systemd configuration in [server-deployment.md](server-deployment.md). No server
changes have been made by this local follow-up.
