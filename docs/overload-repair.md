# Overload isolation repair — September 23, 2026

The supplied server excerpt shows 33 HTTP 200 and 268 HTTP 429 `/process`
responses. Of 816 lines, 507 end in the pager's truncation marker. It identifies
`alfagen-pii-gateway.service` and a direct Uvicorn process. This excerpt cannot
establish the complete five-minute workload, payload lengths, or the cause of
each old 429. Use the full `journalctl` export in [server-deployment.md](server-deployment.md).

## Root cause and change

All `/process` operations previously competed for eight admission slots before
the lifecycle could distinguish new masking from completed restoration/replay.
An expensive context-model call could therefore block a restoration that needs
only the existing vault mapping. Requests that did not require context inference
also competed with an unrestricted number of those expensive model calls, up to
the overall request limit.

The repair makes two bounded changes:

1. During general admission overload, an independently bounded worker lane can
   serve only committed records. It uses the same namespace, exact masked-text
   comparison, original fingerprint, conflict behavior, and retention logic as
   the normal route. A missing or in-progress record cannot start inference in
   that lane. Its capacity equals the general limit: eight by default.
2. At most two context-model calls run per process (one on a single-CPU host).
   Extra context work gets an explicit retryable overload response and releases
   its request slot. It does not return an unclassified or regex-only success.

Both original ONNX models, tokenizers, detection rules, thresholds, and spans
are unchanged. No cross-request prediction cache was added. The earlier INT8
candidate remains rejected because of its recorded accuracy regression. This
repair changes scheduling and isolation; it does not make neural inference
intrinsically faster or establish the 1000-successful-RPS requirement.

Logs now distinguish `admission`, `context`, and `deadline` overload reasons.
The same finite labels appear in `pii_process_overload_total`. Benchmark reports
separate successful masks/restores by case kind so aggregate throughput cannot
hide which types succeeded. Neither mechanism records submitted text or tokens.

## Measurements

The same open-loop benchmark options were used before and after: 30 seconds,
1000 scheduled RPS, client concurrency 200, `required,public,mixed`, and ready
restoration every mask slot. Every mask has fresh synthetic text and a fresh ID;
the oracle requires exact public content and concealed private fields, followed
by exact restoration. Warmup is excluded. No HTTP retries are made.

| Local environment | Before correct RPS | After correct RPS | Before success p95 | After success p95 |
|---|---:|---:|---:|---:|
| Native macOS | 44.33 | 119.73 | 658.72 ms | 208.63 ms |
| Linux/arm64 Docker, 4 CPU quota, 6 GiB | 5.80 | 26.37 | 6178.88 ms | 1910.31 ms |

The Docker baseline completed 182 correct responses, 1521 HTTP 429s, and 66
transport errors. The repaired run completed 797 correct responses, 1407 HTTP
429s, and 8 transport errors. In-window correct counts were 174 and 791;
responses during drain are excluded from RPS. Neither run recorded incorrect
HTTP 200 output. The remaining overload and transport errors are not a pass.

These are overloaded diagnostics, not identical fixed request replays or
capacity certifications. The generator shares the host and drops late slots;
it launched only 1769 and 2212 requests in the Docker runs, respectively, rather
than all 30,000 scheduled requests. Actual request composition differs because
new restores become available only after successful masks. The roughly 4.5-fold
increase therefore describes this diagnostic, not a general model speedup.
The remote server was not modified or load-tested by this repair.

### Five-minute sustained diagnostic

The repaired four-CPU container was then tested for 300 seconds at 330 scheduled
RPS, with the same mix, concurrency, and restoration policy. The generator
scheduled 99,000 slots but launched 32,739 HTTP requests; 66,099 slots were late
and 162 exceeded client capacity. Correct responses: 20,126 total, with 20,118
inside the window (**67.06 successful RPS**, successful HTTP p95 **269.32 ms**).
There were **12,574 HTTP 429s and 39 transport errors**. No incorrect successful
output or timeout was recorded. Health remained ready after the run.

Successful masking included 2,173 public cases and 2,126 mixed-context cases,
plus successful cases for all 17 required categories; these numbers exclude
restorations. Public/mixed restorations also succeeded. This verifies that those
paths were exercised rather than silently disabled. It remains synthetic
functional evidence, not independent 95% accuracy or a passed load SLA.

Evidence: `artifacts/overload-docker-after-5min.json`. Do not compare its 67.06
RPS directly with the earlier 1000-scheduled-RPS runs as a controlled speedup:
offered load, duration, realized request mix, and generator drops differ.

### Connection follow-up

A separate 30-second probe identified three `RemoteProtocolError` failures;
the server log had no corresponding traceback. With only Uvicorn's idle
keep-alive timeout raised from 5 to 30 seconds, another 30-second probe recorded
1956 correct HTTP 200s, 1130 HTTP 429s, and zero transport errors/timeouts or
incorrect output. It completed 65.0 correct RPS inside the window, with success
p95 219.37 ms. Evidence: `artifacts/overload-{transport,keepalive}-probe.json`
and the corresponding `*-types.json` exception-type counters.

Docker and Compose now default to `UVICORN_TIMEOUT_KEEP_ALIVE=30`; the systemd
guide includes the corresponding service override. The processing deadline
remains nine seconds. This short comparison supports the configuration change;
it does not prove that all 39 earlier transport failures were caused by idle
connection expiry or guarantee that network errors cannot recur. The earlier
five-minute report is retained unchanged, not relabelled as error-free.

## Regression verification

Full suite: **587 passed, zero skipped**, seven existing deprecation warnings,
31.31 seconds, exit 0. Reports: `artifacts/overload-fix-tests.{log,xml}`.
The new regressions check restoration and replay while both ordinary admission
and its worker pool are saturated; missing, in-progress, conflicting, and foreign
namespace records; bounded NLI concurrency; slot release after native errors;
preservation of NER availability; safe overload reason logging and HTTP 429;
and accurate per-case benchmark counters. Existing context, all-category,
100k-token, Redis, body-limit, deadline, and replay tests also remain enabled.

### Clean source-ZIP build and extra diagnostic

The source ZIP built successfully into image
`sha256:d6bf9197edcb14959e700fcc923105c70dcb9ab19d092117fb90ddb0b59c38f9`.
It starts with no `.env` or API key and uses the new 30-second HTTP keep-alive
default. Root/React asset delivery and all 19 required-field fixtures passed
over HTTP, including mask replay, exact restoration and restoration replay.

An additional public-text diagnostic **failed**: “Драматург Бернард Шоу написал
пьесу.” is over-masked. The same failure was reproduced on the pre-repair native
service and the saved baseline app in Docker (FULL_NAME span 10..21). It is not
introduced by scheduling changes. Its expected output remains unchanged, and
it is recorded separately from the passing test suite. The corresponding
private-client example passes, and all 21 cases restore exactly. Thus the
additional HTTP check contains 20 passing masking cases and one failed masking
case, not a universal quality pass. Report:
`artifacts/overload-release-container-check.json`.

Evidence is in `artifacts/overload-{before,after}-open.json`,
`artifacts/overload-docker-{before,after}-open.json`, and
`artifacts/overload-docker-after-metadata.json`. The Docker comparison uses the
same previously verified runtime/model image, with the baseline/current app
source mounted read-only. Full source packaging is separate from that setup.

## Deployment

Follow the **Existing systemd server** section in
[server-deployment.md](server-deployment.md). Commit and push the local repair
first, update the server checkout, configure the recommended keep-alive timeout,
then restart the existing service. No new package, model, or API key is required.
Do not start Docker alongside the existing service on port 8000.

The underlying FP32 context model remains expensive. Replacing it requires a
smaller model validated on independent public/private examples; disabling it,
changing thresholds just to pass benchmarks, or counting rejected requests
would not resolve the accuracy/performance requirement.
