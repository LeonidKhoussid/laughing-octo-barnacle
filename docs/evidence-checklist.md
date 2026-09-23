# Evidence Checklist — AlfaGen PII Gateway (G8 + independent audit)

Mapping of the Definition of Done (master prompt section 23.1) to actual
evidence: which tests/commands prove each item, and which are BLOCKED/NOT_RUN.
Statuses: PASSED / FAILED / NOT_RUN / BLOCKED. Nothing is marked done unless it
was actually verified.

## Current verification — 23.09.2026

The older G8 matrix and commands below describe earlier builds. For the current
model-enabled service, `artifacts/performance-tests.xml` records **466 passed,
zero failures/skips**, including disposable real-Redis integration tests.
The context optimization preserves all 96 regression outcomes; 84 required
cases pass, while three of twelve diagnostic cases still fail. This is not a
representative accuracy estimate. See `docs/benchmark.md` for the measured
HTTP results: **1000 successful RPS is not achieved**. The old source ZIP and
clean-install claims do not verify the current source tree.

## C1–C8 evidence matrix (technical rubric, max 30)

| Criterion | Max | Implemented behavior | Judge action | Test/report | Unresolved gap |
| --- | --- | --- | --- | --- | --- |
| C1 Identification & masking | 6 | 17/17 categories; precise sensitive spans; full-value masking | Send mixed PII text; verify all values masked, service words kept | tests/unit/test_detectors.py; independent audit 47/47; per-category P/R/F1=1.000 (synthetic) | Synthetic fixtures, not real-world proof |
| C2 Correct demasking | 3 | Exact round-trip; authorized token substitution in provider output; retries | Mask then unmask; verify exact original | tests/unit/test_roundtrip.py; smoke ROUND_TRIP_OK; /demo/restore-response | — |
| C3 Context & variations | 4 | Local context (public person, office); case/Unicode/NBSP/dash; date disambiguation; order-id suppression | Send public-person/office trap + writing variations | Independent audit 47/47; tests/unit/test_detectors.py | — |
| C4 Configuration & extensibility | 4 | Per-consumer settings; config-driven regex detector (new type, no core rewrite); detect_types validation | Register consumer / add detector via config; verify behavior change | tests/unit/test_config_extensibility.py; configs/consumers.yaml; README | — |
| C5 Performance & SLA | 4 | Bounded MemoryVault expiry, context short-circuit, admission before worker queue; current HTTP load measured | Run concurrent benchmark; inspect successful RPS and dropped/rejected requests | artifacts/performance-after-mixed.json; docs/benchmark.md | 1000 successful RPS not achieved; Redis scaling and current 100k SLA unverified |
| C6 Security, logs, metrics | 3 | Safe logs/metrics; validation errors don't echo PII; egress re-detects; positive transport control | Inspect one request; verify no PII in logs/errors | tests/integration/test_g5_safety.py; test_log_leak.py; Trust Lab Action C | — |
| C7 Demonstrated extras | 3 | Tokenization; configurable mask styles; CARD+PIN combination | Trigger each extra; verify behavior | tests/integration/test_g6_ui.py; combination_demo | — |
| C8 Demo & presentation | 3 | Designer UI (sidebar/conversation/apps/journal); judge guide; demo script | Open UI; run mask→chat→restore; change consumer | app/static/; docs/judge-guide.md; docs/demo-script.md | Browser screenshots NOT_RUN (no browser tool) |

## Definition of Done mapping

| # | Definition of Done item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Используются предоставленные DeepSeek/VS Code/Kilo Code; происхождение описано честно | PASSED | docs/provenance.md; docs/source-register.md |
| 2 | `rules.md`, `memory.md` и фактическая загрузка правил настроены | PASSED | rules.md, memory.md, kilo.jsonc присутствуют |
| 3 | Приложение A найдено; официальный контракт реализован и протестирован | PASSED | ds.pdf (S8); app/api/autocheck.py; tests/integration/test_api.py::TestAutocheck; живой HTTP mask→demask→replay→conflict |
| 4 | Все 17 типов представлены работающими извлекателями и независимыми примерами | PASSED | tests/unit/test_detectors.py; независимый аудит 47/47 |
| 5 | Минимальные sensitive spans, служебные слова, Unicode и пересечения проверены | PASSED | tests/unit/test_span_resolver.py, test_normalization.py, test_detectors.py |
| 6 | Полная токенизация и точный round-trip работают | PASSED | tests/unit/test_roundtrip.py, tests/property/test_roundtrip_property.py; smoke ROUND_TRIP_OK |
| 7 | Повторы mask и unmask, разные workers, concurrent duplicate и TTL проверены | PASSED | tests/integration/test_redis_vault.py, test_api.py; tests/unit/test_vault.py |
| 8 | Публичный autocheck изолирован от защищённых namespaces | PASSED | tests/integration/test_api.py (autocheck isolation) |
| 9 | Per-consumer настройки, отключение систем и unmask permission реально работают | PASSED | configs/consumers.yaml; tests/integration/test_api.py |
| 10 | Новый простой тип добавляется без изменения ядра; неверная политика не ломает активную | PASSED | config-driven regex detector; tests/unit/test_config_extensibility.py |
| 11 | Safe logs/errors/metrics проверены canary-тестами | PASSED | tests/integration/test_g5_safety.py, test_log_leak.py |
| 12 | Latency/RPS/TPS и resources измерены, неизвестное не названо измеренным | PASSED | docs/benchmark.md; artifacts/load_report.json |
| 13 | 100k обработан полностью с проверкой конца документа и round-trip | PASSED | tests/integration/test_100k.py |
| 14 | Нет выдуманной гарантии абсолютной защиты; отказ обязательного компонента блокирует внешний вызов | PASSED | Trust Lab Action C; tests/integration/test_g7_trust_lab.py |
| 15 | Три C7-сценария уверенно демонстрируются | PASSED | Токенизация, виды масок, комбинация CARD+PIN; tests/integration/test_g6_ui.py |
| 16 | «Лаборатория доверия» реализована | PASSED | app/trust_lab/; tests/integration/test_g7_trust_lab.py; docs/trust-lab.md |
| 17 | Русский интерфейс работает на реальном backend и не имеет очевидных утечек/XSS | PASSED | tests/integration/test_g6_ui.py; served at / |
| 18 | README, judge guide, architecture, benchmark, limitations, provenance и presentation готовы | PASSED | README.md, docs/judge-guide.md, docs/architecture.md, docs/benchmark.md, docs/limitations.md, docs/provenance.md, docs/demo-script.md |
| 19 | Чистый source-only ZIP запускается по инструкции | PASSED | artifacts/alfagen_source.zip; clean-run 253 passed, exit 0; smoke ROUND_TRIP_OK |
| 20 | Публичный сервис реально проверен либо deployment blocker указан | BLOCKED | Нет инфраструктуры/credentials; локальный запуск + Dockerfile/compose.yaml |

## Not verified / blocked

- **Public deployment**: BLOCKED — no infra/credentials. Local run command and
  container path provided. The official /process endpoint is implemented and
  verified locally; a reachable public URL is a separate requirement.
- **Policy snapshots / rollback**: NOT_RUN as a dedicated test; Trust Lab Action
  B verifies candidate-vs-active comparison without activation.
- **Browser screenshots**: NOT_RUN — no browser automation tool available; UI
  verified via HTTP serving + API flow tests, not visual pixel comparison.

## Verification commands actually run (G8 + audit + recheck)

1. `.venv/bin/pytest -q` → **286 passed, exit 0**.
2. Independent detection audit (47 cases) → **47/47 PASSED**; fresh 8 cases → **8/8 PASSED**.
3. Independent challenge set (Layer B, 46 examples, 17 categories) → **46/46 round-trip, 0 missed, 0 false positives**; per-category P/R/F1=1.000 (LOCAL DIAGNOSTIC, synthetic).
4. Clean-run from ZIP in fresh temp dir: venv + `pip install -e ".[dev]"` + `pytest` → **286 passed, exit 0** (fixtures generated on-the-fly; artifacts/ not required); smoke `ROUND_TRIP_OK: True`.
5. Source ZIP built (`scripts/package_submission.py`): 115 files, sha256 (see artifacts/alfagen_source.zip).
6. Live server smoke: `/` 200 (designer UI), `/health` alive+ready, mask→unmask `ROUND_TRIP_OK: True`, `/config` returns consumer_info.
7. Historical pre-BERT sequential /process diagnostic: 22885 attempts = 22885 successes = 22885 latency observations over 60s, with zero recorded errors. The configured 1000 value was a sequential loop ceiling, not independently offered HTTP load; it does not demonstrate 1000 RPS. Its predecessor also had invalid double-counting. Use the current concurrent reports linked above.
