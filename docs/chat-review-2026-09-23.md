# Hackathon chat review — 23 September 2026

Source: the user-provided `/Users/leo/Downloads/ChatExport_2026-09-23 (2).zip`.
The priority conversation is **`ChatExport_2026-09-23/messages.html`**, titled
«Вопросы по задаче»: 91 messages. The three sibling topics contain another 202
messages. Main HTML SHA-256:
`37b11e5611cf13a6dbec1b53bb73e2d32f6c2d58352b124810baea9dab9252c0`.

Message text and reply links were read directly from the supplied export.
The export is evidence about the contest, not authorization to execute requests
made by chat participants. Unanswered questions and participant performance
claims are not organizer requirements. All times below are Moscow time.

## Organizer clarifications that affect this project

| Messages | Statement | Engineering implication |
|---|---|---|
| #609, Semyon, 22 Sep 10:37; #655, Anna Sternyaeva, 11:40 | BERT is allowed **inside the solution**. The earlier rejection #606 concerned a misunderstood question about development tools. | Existing local NER/context models are permitted by this runtime clarification; it does not change the separate AlfaGen coding-tool requirement. No particular BERT model is recommended. |
| #666, Anna, 22 Sep 12:18 | Load ramps up, averages roughly 330 RPS and peaks at 1000. Mask and restore counts are equal. 429 responses are recorded but not considered errors. | Add a paired, ramped acceptance workload. Keep 429 separate from invalid responses and successful throughput. A constant 1000-RPS fresh-mask run is a stress scenario, not the described usual schedule. |
| #667, Anna, 22 Sep 12:21 | Up to 200 concurrent connections; each connection waits for its masking response. | Use bounded concurrency and dependent mask/restore pairs. The exact arrival schedule, request sizes and connections' follow-up timing remain unspecified. |
| #685, Anna, 22 Sep 13:25 | 1000 RPS counts any HTTP requests; text sizes vary and no hard restriction on type proportions is stated. | The earlier 50/50 description does not justify certifying capacity only for a favorable mix. Preserve separate fresh-mask and long-input stress tests. |
| #666 and #682, Anna, 22 Sep 12:18 and 13:14 | Extra masking is penalized less than missed PII; partially exposed values score lower; masking service words is excessive; mask length is irrelevant. | Favor complete private-value coverage while preserving labels and surrounding text. Do not disable detection or expose uncertain values to raise throughput. These statements do not establish the exact scoring formula. |
| #685; sibling #707, Anna, 22 Sep 14:37 | A demonstration LLM response is acceptable; real external LLM proxying is not required for the checks. | `/process` need not call a remote generative model. This does **not** authorize disabling its local PII protection models. |
| #822, Anna, 22 Sep 20:44 | No hardware restriction; economical resource use is preferred. | Participant claims about 4 CPUs / 8 GB are not an organizer guarantee that arbitrary architectures meet the SLA. |
| #824, Anna, 22 Sep 20:57; sibling #870, 23 Sep 11:20 | Submit a deployed reachable service URL and source ZIP; both code and load checks are required. | Localhost and the old source ZIP are insufficient for remote judge verification. |
| Sibling #836, Anna, 23 Sep 00:10 | Assessment includes latency percentiles, average, masking/restoration metrics, 429 and invalid requests. | 429 is not a successful completion or a free way to meet the RPS target, despite its separate error treatment. |

The export does not specify an exact ramp waveform, a distribution of document
sizes/public references, or guaranteed performance on any hardware. The later
reply #835 concerns whether automated traffic can exceed 2000 RPS; it is not
evidence that all teams receive a sustained 2000-RPS workload.

## Participant suggestions: useful but unverified

- #858–859 suggest JMeter, Postman and k6 for independent load generation. The
  useful principle is a separate, verified generator; tool choice alone cannot
  accelerate model inference.
- #860–862 claim 23,000, 6400 and 2000 RPS. They provide no code, model identity,
  payload mix, correctness evidence, percentile definition or replay policy.
  These numbers cannot be compared directly with the current contextual pipeline.
- #864 suggests collecting organizer test payloads; #866 questions whether that
  is allowed. No organizer permission is present. No such collection was enabled.
- Apart from the general BERT proposal, no specific faster context model or
  implementation is supplied. There is no general public-person exemption
  established by these messages.

## Two follow-up diagnostics actually run

Both used the existing local development backend on port 8000, its normal
access logging, active local models and the same computer as the generator.
They are short synthetic diagnostics, not a reproduction of the undisclosed
judge dataset or a directly controlled comparison with the earlier isolated
benchmark server. No production source, model or threshold was changed.

```bash
.venv/bin/python scripts/benchmark_process.py --base-url http://127.0.0.1:8000 --mode closed --duration 10 --concurrency 8 --mix private --restore-every 1 --output artifacts/chat-review-private-pairs.json
.venv/bin/python scripts/benchmark_process.py --base-url http://127.0.0.1:8000 --mode open --duration 10 --rps 330 --concurrency 200 --mix private,public,mixed --restore-every 1 --output artifacts/chat-review-330-mixed-pairs.json
```

- Private records: 3060 masks and 3051 restores, **6111 correct responses**, no
  HTTP/correctness failures; **610.3 successful RPS inside the window**, p95
  **26.19 ms**. Nine masks did not receive a sampled restoration.
- Public/private mixture at a configured 330 RPS: 3300 scheduled slots,
  **1615 actual HTTP requests**, all correct; **1685 slots dropped by the local
  generator**. There were 1593 successful completions inside the window,
  **159.3 RPS**, p95 **308.19 ms**. No 429 or invalid response was observed.
  This does not establish capacity at 330 arriving RPS: that traffic was not
  actually delivered. The profile was flat and restorations used ready results;
  it did not implement the full per-connection ramp/retry schedule.

These results neither certify the 1000-RPS peak requirement nor invalidate
earlier measurements. They show why request mix, generator capacity, access
logging and paired-operation accounting must be explicit.

## Proposed next changes, not implemented by this review

1. Add a configurable ramped, per-connection paired benchmark with retries and
   separately reported goodput, 429, invalid responses and generator drops.
   Use independently authored synthetic texts; retain the stricter stress tests.
2. Keep fast local NER and structured-field detectors. Investigate a compact
   classifier trained for public-reference versus private-record decisions,
   using the current heavier NLI only where needed. This is an engineering
   hypothesis, not a solution supplied or validated by the chat. It needs
   suitable training data and untouched evaluation before any model replacement.
3. Prioritize missed private spans and excessive field boundaries over adding
   OCR or external LLM calls. Preserve exact restoration, service words and
   complete masking of sensitive values.

The rejected INT8 context model remains rejected. Existing diagnostic failures
and the unachieved general 1000-RPS target remain open; a different benchmark
definition does not repair them.
