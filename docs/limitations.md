# Current limitations

Updated 23 September 2026. See `requirements-verification.md` for the complete
requirement mapping and `benchmark.md` for measured workloads.

- 95% quality on representative independent data or the hidden judge dataset is
  unproven. Synthetic cases used during fixes are regression evidence only.
- NER and NLI are active local models. NLI classifies context, not whether a fact
  is truly public knowledge. An unfamiliar person described as a poet can still
  be mistaken for a public biography; an unqualified meeting address is ambiguous.
- Structured types still use rules. Unusual addresses, unknown documents and
  obfuscation are not exhaustively covered. A generic NER is not trained on all
  seventeen required PII labels.
- Sustained successful 1000 RPS and 2000 bonus RPS are not demonstrated. The large
  context model remains a CPU bottleneck on public/mixed passages. Failed or
  rejected requests must not be counted as successful throughput.
- Earlier 100k tests estimated tokens as characters/4 and contained only 60,897
  active-NER tokens. The corrected fixture contains 100,082 tokens. Its 1-second
  latency target remains unmet; body limit is now 2 MiB by default.
- MemoryVault uses an expiry heap rather than scanning every entry, but is still
  single-process and loses mappings at restart. RedisVault supports shared,
  encrypted mappings; capacity accounting still scans Redis and must be measured
  before recommending multiworker for throughput.
- Metrics are per worker, with explicit rate windows. Incoming-text TPS is an
  estimate; inference TPS counts actual model input IDs, including overlaps and
  hypotheses. Multiworker metrics require aggregation outside this prototype.
- UI policy updates are in-memory and do not persist. For several workers, edit
  the configuration file and restart all workers together; mutable UI updates
  return 409 instead of silently diverging.
- Already-running native inference cannot be force-killed safely. The 9-second
  response deadline returns overload while retaining that worker's admission slot
  until completion. Persistent overload is a capacity failure, not a passed SLA.
- A real external LLM integration and production banking/regulatory controls are
  not demonstrated. Basic judge UI and `/process` work without external API keys.
- No model was trained on russian-pii-66k: the proposed dataset lacks explicit
  public/private labels and declared licensing/provenance in the reviewed card.
