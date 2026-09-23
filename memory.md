# memory.md — рабочая память AlfaGen PII Gateway

## 1. Быстрое восстановление контекста
- Проект: универсальный модуль защиты ПД, 17 категорий.
- Основной документ: MASTER_PROMPT.md.
- Строгие правила: rules.md.
- Последнее обновление: 2026-09-22 (MSK).
- Текущий этап: G8 + независимый аудит (исправления A–F, C1/C3, Trust Lab, C4, C8 UI).
- Состояние workspace: все 17 категорий реализованы и протестированы; RedisVault + MemoryVault; lifecycle state machine; атомарные Lua-примитивы; multiworker guard; autocheck isolation; per-consumer настройки; безопасные логи/метрики/лимиты; deadline; обработка 100k; нагрузочный инструмент; дизайнерский русский UI (sidebar + conversation + apps + journal); Лаборатория доверия; source ZIP собран и проверен чистой установкой; **253 теста**.
- Следующее конкретное действие: финальный обзор перед сдачей.

## 2. Инструменты и происхождение
- Используемые инструменты: предоставленный DeepSeek, VS Code, Kilo Code.
- Внешний мастер-промпт: происхождение указано; отдельное разрешение или запрет именно промптов не заявлять без источника.
- Изменение версии 2: добавленное автором требование письменного разрешения больше не является автоматическим стоп-фактором; не выдумывать ни подтверждённое разрешение, ни новый запрет.
- Статус ранее созданного кода: код G1–G8 создан в этом workspace; происхождение зафиксировано в docs/provenance.md.
- Независимая разработка: продолжать без ожидания отсутствующих PDF или подтверждения начать.
- Runtime BERT: ранее отдельно разрешён внутри pipeline; не используется (детекторы rule-based).
- Provenance: docs/provenance.md.

## 3. Источники и последние уточнения
- Реестр: docs/source-register.md.
- Официальный API Appendix A: **VERIFIED** — контракт `POST /process` реализован в `app/api/autocheck.py` и проверен контрактными тестами и живым HTTP-запросом (ds.pdf, стр. 6–7). Приложение B (стр. 8–9): таймаут 10с, ретраи до 2, 429 с Retry-After, стоп после 5 невалидных, метрика — нормированное span-based расстояние Левенштейна [0..1].
- SLA: ориентироваться на ТЗ — 1 сек; учитывать mean/p50/p95/p99.
- RPS: любые запросы; смесь не фиксирована.
- Demask: точный masked text и тот же payload_id.
- Retries: возможны после ошибки/429; нужны повторы обеих операций.
- Auth: исключение только для autocheck endpoint.
- LLM: реальный вызов необязателен; stub маркируется.
- Mask format: свободный; default скрывает весь sensitive value.
- Скрытая span-формула: не предоставлена.
- Конфликты/неизвестное: docs/assumptions.md.

## 4. Дедлайн и среда
- Подтверждённый дедлайн: завершение разработки 23.09.2026 23:59 (часовая зона не подтверждена).
- Платформа/CPU/RAM/GPU: macOS 15.5 (arm64), 8 CPU, 16 GiB RAM; GPU не используется.
- Python и lockfile: Python 3.14.6, venv .venv; lockfile не создан.
- Redis/Docker: redis-server/redis-cli и docker доступны; реальный Redis 7.2.7 использован для интеграционных тестов (disposable instance на свободном порту, DB 15; никогда не переиспользуется/не флашится чужой Redis).
- Model artifacts: не выбраны; BERT-разрешение требует проверки актуальности.
- Kilo rules loading: kilo.jsonc с массивом instructions — способ по документации; фактическая загрузка требует проверки в новом сеансе.

## 5. Архитектура
- Autocheck adapter: app/api/autocheck.py; схема UNVERIFIED (нет Приложения A); disabled autocheck не обрабатывает запросы.
- Shared PII engine: app/core/engine.py (включая per-consumer CARD+PIN комбинацию; opaque-маркеры без коллизий; MaskResult repr без originals).
- State/Vault: RedisVault (app/vault/redis.py) для multiworker + MemoryVault (app/vault/memory.py) для single-process; lifecycle state machine (app/vault/lifecycle.py); multiworker+MemoryVault guard enforced; retention отделён от replay grace (PII_VAULT_TTL_SECONDS=3600 для свежей маски, replay grace 300s после unmask).
- Политики: configs/consumers.yaml + app/policies/; detect_types валидируется (FULL_NMAE → PolicyLoadError); config-driven regex detector (C4).
- Optional provider: stub (app/providers/stub.py), явно маркирован; AlfaGen не интегрирован.
- Безопасность: SafeLogger не утекает PII; метрики с ограниченной кардинальностью; лимиты с deadline; validation errors не отражают входные значения (detail[].input удалён); egress guard пере-детектирует outbound текст (не доверяет caller claims).
- Отклонённые альтернативы: нет; см. docs/decisions.md при появлении.

## 6. Контракт и состояние
- Request schema: **VERIFIED** — официальный `{payload, payload_id} → {result}` (docs/api-contract.md).
- Response schema: **VERIFIED** — `{"result": "<string>"}`.
- Mask replay: PASSED.
- Unmask replay: PASSED.
- Concurrent same-ID: PASSED (8 потоков + Redis).
- Cross-worker: PASSED (два независимых RedisVault, общий Redis).
- Original == masked: PASSED.
- TTL / replay grace / capacity: настраиваемые (PII_VAULT_TTL_SECONDS=3600, replay grace 300s, max_entries 100k, max_bytes 1GB), проверены тестами; retention отделён от replay grace.
- Redis owner-release: PASSED (correct-owner release удаляет запись; wrong/stale-owner не удаляет; Lua ARGV[1] исправлен).
- Официальный /process: mask→demask→replay→conflict→no-PII→429+Retry-After — все PASSED (tests/integration/test_api.py::TestAutocheck; живой HTTP).

## 7. Покрытие 17 категорий
| Тип                 | Реализация   | Positive/negative tests | Известная проблема |
| ------------------- | ------------ | ----------------------- | ------------------ |
| FULL_NAME           | IMPLEMENTED  | PASSED                  | —                  |
| BIRTH_DATE          | IMPLEMENTED  | PASSED                  | —                  |
| BIRTH_PLACE         | IMPLEMENTED  | PASSED                  | —                  |
| PASSPORT            | IMPLEMENTED  | PASSED                  | —                  |
| CITIZENSHIP         | IMPLEMENTED  | PASSED                  | —                  |
| PASSPORT_ISSUER     | IMPLEMENTED  | PASSED                  | —                  |
| DEPARTMENT_CODE     | IMPLEMENTED  | PASSED                  | —                  |
| PASSPORT_ISSUE_DATE | IMPLEMENTED  | PASSED                  | —                  |
| DRIVER_LICENSE      | IMPLEMENTED  | PASSED                  | —                  |
| ADDRESS             | IMPLEMENTED  | PASSED                  | —                  |
| EMAIL               | IMPLEMENTED  | PASSED                  | —                  |
| PHONE               | IMPLEMENTED  | PASSED                  | —                  |
| INN                 | IMPLEMENTED  | PASSED                  | —                  |
| CARD                | IMPLEMENTED  | PASSED                  | —                  |
| CVV                 | IMPLEMENTED  | PASSED                  | —                  |
| PIN                 | IMPLEMENTED  | PASSED                  | —                  |
| CARDHOLDER_NAME     | IMPLEMENTED  | PASSED                  | —                  |

- Все 17 категорий IMPLEMENTED, positive+negative тесты PASSED, зарегистрированы в registry + detectors.yaml, round-trip и HTTP API проверены.
- **Независимый аудит (47 случаев)**: 47/47 PASSED после исправлений C1/C3 (было 19/47). Исправлены: bare/lowercase/uncommon ФИО, ISO/US/uppercase/written-day даты, bare passport, Unicode dash/NBSP, CVV-код/ПИН-код карты, compact/multiword адреса, order-id suppression (CARD/INN), public-person/office context.
- G3-оценка: per-category метрики 1.000 на синтетических фикстурах (caveat: синтетика, не официальная формула).
- Evaluator исправлен: per-document character recall (не merged across docs), exact match требует category + boundaries.

## 8. Критические инварианты
- Exact round-trip: PASSED (smoke + тесты).
- Unicode/offsets: PASSED (property-тесты, включая emoji; code-point→UTF-16 конверсия в UI).
- No raw PII in safe telemetry: PASSED (canary-тесты; validation errors не отражают входные значения).
- Tenant isolation / permissions: PASSED (autocheck isolation test).
- Mandatory detector failure blocks egress: PASSED (Trust Lab Action C, upstream_calls=0; healthy path делает реальный transport call — positive control).
- Atomic vault commit before success: PASSED (lifecycle atomic_commit).
- Policy snapshots / rollback: NOT_RUN (Trust Lab Action B сравнивает candidate vs active без активации).
- 100k end-of-document coverage: PASSED (функционально; p50 ~1.74s — выше SLA 1s, известный bottleneck).

## 9. Последние реальные измерения
- Команда: `.venv/bin/pytest` → **286 passed, exit 0**.
- Независимый аудит: 47/47 detection cases PASSED; 8 fresh cases PASSED (inflected names, compound dates, client+poet, phone extensions).
- **Независимый challenge set** (Layer B, 46 примеров, 17 категорий): 46/46 round-trip, 0 missed, 0 false positives, concealment recall 1.000, overmask 0.000; per-category P/R/F1=1.000 (LOCAL DIAGNOSTIC, синтетика).
- Нагрузка: scripts/loadtest.py → artifacts/load_report.json (sustained 956.77 RPS MemoryVault; uniform 334 RPS; 100k mask p50 1.74s выше SLA; RedisVault 2-worker 108 RPS; zero errors/429s).
- **Официальный /process нагрузка** (artifacts/process_load_report.json, accounting fix): 60с при 1000 RPS → 22885 attempts, 22885 success, 0 errors/429/5xx/timeouts, 0 correctness failures; 22885 latency observations (reconcile); process latency mean 2.60ms p50 2.37ms p95 4.64ms p99 5.37ms; completed RPS ~381 (ограничен генератором, не сервисом). Старый отчёт (22120/737 RPS) помечен INVALID для throughput-заявлений (double-counting).
- Дата/build/policy/model: 2026-09-22; политики из configs/consumers.yaml; модель не используется.
- Per-type quality report: artifacts/evaluation_report.json (G3) — все 17 категорий P=1.000 на синтетике (caveat).
- Mean/p50/p95/p99 mask/unmask: записаны в artifacts/load_report.json.
- 100k test: PASSED функционально, но mask p50 1.74s — выше SLA 1s (известный bottleneck).

## 10. Рубрика и демонстрация
- Полная evidence matrix: docs/scorecard.md, docs/evidence-checklist.md.
- C1…C8: подтверждённое покрытие COVERED; официальные баллы не присваиваются.
- C7 сценарии: токенизация / конфигурируемые виды масок / комбинационное правило — все реализованы.
- Лаборатория доверия: IMPLEMENTED (Actions A/B/C, безопасный отчёт, API, UI-панель; auth для protected consumers; отчёт из recorded runs; positive transport control; вариации используют submitted input).
- UI: IMPLEMENTED (дизайнерский layout: sidebar + conversation + apps + journal; emoji offset fix; XSS-safe).
- Judge guide: docs/judge-guide.md; demo script: docs/demo-script.md; trust lab doc: docs/trust-lab.md.

## 11. Последний завершённый этап
- Что изменено (независимый ре-чек):
  - Fix 1: loadtest accounting — убрано двойное инкрементирование; счёт только в `_finalize`; `_process` валидирует 200 без `result`; `correctness_failures` отдельно; детерминированные тесты (tests/unit/test_loadtest_accounting.py).
  - Fix 2: provider output protection — `_protect_provider_output` маскирует новую PII в ответе провайдера, сохраняя известные токены (уникальные placeholder'ы); тесты TestProviderOutputProtection.
  - Fix 3: detection — морфология имён (инфлексии), compound written dates, client+poet override, phone extensions; 8 fresh cases PASSED.
  - Fix 4: deadline/overload — DeadlineExceededError → 429 (не 422) во всех endpoints; тест test_process_deadline_returns_429.
  - Fix 5: config website — `/config/update` endpoint (server-side, валидация, атомарность); PolicyStore.update_consumer; UI Applications view с редактируемыми контролами.
  - Layer B: независимый challenge set (46 примеров, 17 категорий) + evaluator (per-category).
- Выполненные проверки: `.venv/bin/pytest` — 286 passed, exit 0; 47/47 + 8/8 fresh detection; challenge set 46/46; официальный /process нагрузка 60с@1000RPS — 22885/22885, 0 errors, p50 2.37ms.
- Осталось непроверенным: публичный deployment (BLOCKED — нет инфраструктуры/credentials); browser screenshots (NOT_RUN — нет browser tool); multiworker Redis benchmark (NOT_RUN в этом проходе).
- Регрессии: нет.

## 12. Блокеры и ближайшие действия
1. Финальный обзор перед сдачей.
- Известные bottlenecks: 100k mask выше SLA 1s (p50 1.74s); RedisVault O(N) capacity scan.
- Что НЕ начинать: BERT/ML-эксперименты до стабильной базы.
- Что требуется от пользователя: инфраструктура/credentials для публичного deployment (официальный /process реализован и проверен локально; доступный URL — отдельное требование).

## 13. Запуск и сдача
- Проверенная команда setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` (exit 0).
- Проверенная команда run: `.venv/bin/uvicorn app.main:app --port 8000` (проверено).
- Smoke/contract/regression: pytest 253 passed, exit 0; smoke OK.
- ZIP: artifacts/alfagen_source.zip (115 файлов, 258216 байт, sha256 f3b792a00a37cf83486e4c1f60bb006b9ec8ab52bc7d4a41f804a9e5fdc75277).
- Clean install: PASSED (253 passed, exit 0; smoke ROUND_TRIP_OK; fixtures генерируются on-the-fly, artifacts/ не требуется).
- Публичный endpoint: BLOCKED (нет инфраструктуры/credentials); локальный запуск + Dockerfile/compose.yaml.
- Необходимый срок доступности: UNKNOWN.
- Время последней freeze/release проверки: 2026-09-22.

## 14. Упрощение первого экрана для жюри — 23.09.2026

- По запросу пользователя добавлен начальный экран «Демонстрация»: заполненный синтетический пример, одна кнопка, фактический цикл mask/unmask и сравнение с исходным текстом.
- Используется существующий профиль `autocheck` без ключа. LLM не вызывается; защищённые профили, приложения и журнал остаются в «Инструментах».
- Обновлены React-компоненты, стили, production-сборка и краткая инструкция `docs/judge-guide.md`. Backend и правила авторизации не изменены.
- PASSED: `cd frontend && npm run build`; `.venv/bin/pytest -q tests/integration/test_api.py tests/integration/test_g6_ui.py`; браузерные проверки первого входа, маскирования/восстановления, отсутствия ПД, пустого ввода, клавиатуры, слишком большого текста и восстановления после ошибки.
- Отчёт и границы проверки: `docs/judge-ui-check.md`. Полный pytest и нагрузочные проверки в этом проходе не запускались.
- Следующее действие перед сдачей: пересобрать архив из актуальных исходников и проверить его запуск. Существующий ZIP не обновлялся.

## 15. Локальный контекст и полные границы ФИО — 23.09.2026

- Исправлены `FULL_NAME`, повествовательные `BIRTH_DATE`/`BIRTH_PLACE`: публичное упоминание автора сохраняется, клиент-тёзка и связанные личные реквизиты защищаются. Устранена частичная маска имени с вариантом написания «Александер».
- Контекст ограничен конкретным субъектом; обычные глаголы «написал/читает» не освобождают имя от защиты. Добавлены формы имён, инициалы и отдельные анкетные поля. Это эвристика, не универсальная база публичных персон.
- React: четыре выбираемых примера (клиент, публичная личность, клиент-тёзка, смешанный контекст), прежний простой цикл без ключа и LLM. Backend и Vite перезапущены; локальный сайт работает на `http://localhost:5173/`.
- PASSED: `.venv/bin/pytest --ignore=tests/integration/test_redis_vault.py -o addopts='' -q --tb=short --junitxml=artifacts/context-regression-tests.xml` — 318 passed, exit 0. Включены 43 новых контекстных регрессии и 9 сценариев точного round-trip через оба HTTP API.
- PASSED: `.venv/bin/python scripts/evaluate_challenge.py` — 46/46 синтетических случаев, 0 пропусков/ложных срабатываний; `cd frontend && npm run build` — exit 0.
- PASSED: живые HTTP-запросы после перезапуска (`artifacts/context-live-check.json`), четыре сценария и визуальный просмотр в браузере. Подробности и границы: `docs/context-check.md`.
- NOT_RUN в этом проходе: Redis, multiworker/нагрузка/SLA, публичный deployment, пересборка ZIP и чистая установка. Старые результаты в разделах выше относятся к предыдущим этапам; текущий исходный код отличается от существующего ZIP.
- Следующее действие перед сдачей: пересобрать исходный архив и проверить установку/запуск из него; отдельно выполнить нагрузочную и Redis-проверку в изолированной среде.

## 16. Локальные BERT-модели и повторная проверка — 23.09.2026

- По прямому запросу пользователя подключены RuBERT-tiny2 NER (ONNX INT8) и mDeBERTa NLI (ONNX FP32) в основной pipeline; активен `semantic_context`. Фиксированные ревизии и SHA-256 в `configs/semantic-models.json`; подготовка через `scripts/download_semantic_models.py`. Входные тексты не передаются внешней модели. Старые указания «модель не используется» и запрет начинать BERT-эксперименты описывают предыдущее состояние.
- NER дополняет распознавание незнакомых имён, контекст оценивается отдельно для каждого упоминания. Личные записи и реквизиты имеют приоритет; научная атрибуция, литература и публичные контакты могут сохраняться. Это не проверка произвольных фактов по внешней базе знаний.
- Исправлены границы email, повествовательных дат/мест рождения, cardholder и ложные NER-срабатывания на служебных словах; API возвращает безопасный 503 при отказе обязательной модели и блокирует отправку провайдеру.
- Во время исследования ONNX Runtime вызывал нативный сбой при завершении Python. Телеметрия отключена до нативного импорта; 3 новых отдельных процесса выполнили NER/NLI и завершились с exit 0 без новых crash reports. Отчёт: `artifacts/semantic-runtime-stability.json`.
- PASSED: `.venv/bin/pytest --ignore=tests/integration/test_redis_vault.py -o addopts='' -q --tb=short --junitxml=artifacts/semantic-tests.xml` — 384 passed, exit 0, 22,38 с. Включены модельные, контекстные, fail-closed и функциональные проверки длинного текста. Redis не запускался.
- PASSED: прежний challenge — 46/46 round-trip, 0 пропусков/ложных срабатываний; React production build; проверка контрольных сумм всех шести файлов моделей.
- Качество: исходный набор 25/52 по непробельным защищаемым символам; после исправлений 52/52 (51/52 точных границ). Первый новый независимый набор дал 26/32 по символам, 23/32 по границам, 0 пропущенных и 92 лишне скрытых символа. После исправлений он уже регрессионный: 32/32 по символам, 29/32 по границам (серия/номер паспорта разделены). Первый результат сохранён отдельно. Эти синтетические наборы не подтверждают 95% на реальном потоке.
- Живой `/process`: 48/48 запросов (маска, повтор, восстановление, повтор восстановления); публичные фрагменты сохранены, личные скрыты, текст восстановлен точно. 12 последовательных маскирований коротких текстов: p50 115,64 мс, p95 296,61 мс. Отчёт `artifacts/semantic-live-check.json`. Это не нагрузочный тест и не подтверждение 1000 RPS.
- Сервис перезапущен с моделями; `http://localhost:5173/`, backend 8000, health ready. В браузере проверены научный/частный пример, литература, клиент-тёзка; все восстановлены точно. Добавлены готовые примеры в React. Подробности, источники и ограничения: `docs/semantic-check.md`.
- NOT_RUN: Redis/multiworker/нагрузка/SLA, Docker build, новый ZIP/чистая установка, публичный deployment. Архив предыдущего этапа устарел. Следующие действия перед сдачей: независимый репрезентативный набор и нагрузка именно с включёнными моделями, затем упаковка/чистый запуск.

## 17. Профилирование и оптимизация производительности — 23.09.2026

- По запросу пользователя оптимизирован существующий код, без отключения обязательного NER/NLI. Профиль `artifacts/throughput-before.prof`: около 98% времени выбранной смешанной нагрузки приходилось на NLI. Аппаратные рекомендации не заменяют измерение кода.
- MemoryVault: полный обход записей при обращениях заменён индексом сроков хранения с поколениями; учёт байтов выполняется при записи, не при чтении. Проверены TTL, ёмкость, конкурентность и атомарная замена. При 20 000 записях 1000 чтений: 727,76 → 0,735 мс (`artifacts/vault-performance.json`); это измерение хранилища, не HTTP RPS.
- Контекстный детектор прекращает проверку альтернативных гипотез после первого достаточного подтверждения. Пороги и правила защиты сохранены. На 96 синтетических регрессиях результаты идентичны прежней исчерпывающей проверке, round-trip точный; 84 обязательных случая проходят по защищаемым символам. Три из 12 диагностических случаев всё ещё не проходят (68 пропущенных чувствительных символов); эти ошибки не скрыты. За 192 маскирования число NLI-пар уменьшилось 342 → 156, среднее время 158,40 → 73,48 мс. Отчёт: `artifacts/semantic-short-circuit-comparison.json`.
- `/process` проверяет допуск до очереди AnyIO. Отмена запроса не освобождает слот работающего синхронного обработчика раньше его завершения; отмена в очереди предотвращает поздний запуск. Проверены 429/Retry-After, отказ dispatch, дедлайн и повторное использование слотов. Гистограммы ограничены последними 8192 наблюдениями: count/mean за всё время, перцентили за явно указанное окно.
- Новый `scripts/benchmark_process.py` выполняет настоящую конкурентную HTTP-нагрузку с уникальными текстами/ID и независимой проверкой результата. Старый `loadtest.py process_sustained` обозначен последовательной диагностикой; прежние заявления о поданной нагрузке 1000 RPS недействительны.
- Парные HTTP-прогоны по 10 с, Apple M1 Pro (8 CPU, 16 GB), один worker, MemoryVault, генератор на том же компьютере, concurrency=8, обе модели включены: частные записи 502,4 → 649,2 успешных RPS, p95 24,12 → 17,70 мс; смешанный публичный/частный текст 44,2 → 80,5 RPS, p95 418,43 → 192,12 мс. В этих прогонах нет ошибок HTTP или содержания. Отчёты `artifacts/performance-{before,after}-{private,mixed}.json`; условия и ограничения в `docs/benchmark.md`.
- Попытка открытой нагрузки 1000 RPS на смешанном тексте НЕ ПРОШЛА: 10 000 запланированных слотов, 9110 пропущены генератором, 890 фактических HTTP-запросов, 863 корректных 200, 27 ответов 429. Успешный RPS внутри окна 78,6; p99 1045,66 мс. Это не 1000 реально пришедших на сервис запросов в секунду. Отчёт `artifacts/performance-after-1000-mixed.json`.
- Смешанный 20-секундный прогон маскирования/восстановления: 2079 масок + 2070 восстановлений, все 4149 ответов корректны, 207,05 успешных RPS внутри окна, p95 137,85 мс (`artifacts/performance-after-restoration-mix.json`). Дешёвые восстановления не подменяют результат mask-heavy нагрузки.
- Кандидат mDeBERTa INT8 отклонён: 44/84 обязательных случаев против 84/84 у FP32, 768 лишне скрытых публичных символов при небольшом выигрыше скорости. Активный manifest и FP32 не изменены. Отчёт `artifacts/nli-quantization-comparison.json`; кандидат изолирован и не загружается сервисом.
- PASSED: `.venv/bin/pytest -o addopts='' -q --tb=short --junitxml=artifacts/performance-tests.xml` — 466 passed, 0 skipped, 24,36 с, exit 0; включая отдельный временный настоящий Redis. Пять предупреждений об устаревающих API зависимостей, без падения Python.
- Dev-сервис перезапущен: `npm run dev:all` из frontend, сайт `http://localhost:5173/`. В браузере после перезапуска проверены сохранение научного публичного упоминания, три частных фрагмента и точное восстановление; полный цикл примера 178 мс. Интерфейс визуально просмотрен. Текущий исходный код отличается от старого ZIP.
- Цель 1000 успешных RPS НЕ ДОСТИГНУТА. Следующая основная задача — уменьшить стоимость контекстной модели с проверкой качества на независимой разметке; кэш повторов или отключение модели не считаются решением. NOT_RUN: нагрузка на выбранном Linux-сервере, Redis multiworker throughput, текущий 100k SLA, новый Docker/ZIP/чистая установка и публичное развёртывание.

## 18. Разбор переписки хакатона — 23.09.2026

- Прочитан архив пользователя `ChatExport_2026-09-23 (2).zip`: основной неснабжённый суффиксом каталог содержит тему «Вопросы по задаче» (91 сообщение), три соседних темы — ещё 202. Источник [S9], проверенные ссылки ответов/времена и выводы: `docs/chat-review-2026-09-23.md`.
- Anna Sternyaeva #655 разрешает BERT внутри pipeline. #666–667 описывают типичную нагрузку с разгоном, среднее ~330 RPS, пики 1000, равное число mask/restore, до 200 соединений; 429 фиксируется отдельно и не считается ошибкой проверяющей системы. Более поздний #685 не ограничивает жёстко доли запросов/размеры, поэтому этот профиль не заменяет stress-тесты. Старый счётчик `errors` в benchmark включает HTTP 429; документация поясняет отличие от терминологии жюри.
- Заявления участников о 2000/6400/23000 RPS не содержат моделей, данных и подтверждённой точности. Нового готового способа ускорить контекст в переписке нет. Предложение собирать закрытые payload проверяющей системы не подтверждено организатором и не выполнялось.
- Дополнительные 10-секундные проверки существующего dev backend 8000, оба локальных NLP-компонента включены: частные записи с восстановлением — 6111 корректных ответов (3060 масок, 3051 восстановление), 610,3 успешных RPS в окне, p95 26,19 мс; `artifacts/chat-review-private-pairs.json`.
- Попытка 330 RPS, смесь private/public/mixed с восстановлением: 3300 слотов, 1615 отправленных HTTP-запросов, все корректны (823 маски, 792 восстановления), 1685 пропусков генератора; 159,3 успешных RPS в окне, p95 308,19 мс. `artifacts/chat-review-330-mixed-pairs.json`. Это плоский профиль на одном компьютере, не точный прогон жюри и не доказательство 330 реально поданных RPS.
- Production-код, активные модели и пороги в этом разборе не изменялись. Обновлены только документы/реестр источников и сохранены результаты диагностики. Следующее предложение: корректный paired/ramp benchmark и исследование меньшего обученного контекстного классификатора с независимой проверкой качества; эти изменения ещё не реализованы.

## 19. Контекстные объяснения, новые регрессии и остановка падающего экспорта — 23.09.2026

- Добавлен отдельный NLI-veto частной информации перед разрешением публичного контекста; приватная и первая публичная гипотеза вычисляются одним пакетом. Явные частные записи имеют приоритет; прежняя политика биографических/учебных ссылок сохранена. Признаки NLI, исторического правила, названия учреждения, частной записи и неопределённости теперь различаются в Detection.
- FULL_NAME распознаёт Unicode/диакритику/транслитерацию у явных полей/ролей, включая получателей и заказчиков; поддержаны домашние адреса с «из дома» и «переулок». Не добавлялись новые списки известных персон.
- React: свёрнутый сценарий «Одно имя — два контекста», настоящие запросы Action A, результаты и объяснения из текущих детекций без дополнительных вызовов модели. Action A использует общие ограничения тела/допуска/дедлайна. Ключи защищённых потребителей по-прежнему обязательны; autocheck остаётся доступным для жюри.
- Новый набор из 36 примеров размечен и зафиксирован до запуска (SHA в docs/context-improvements-2026-09-23.md). Первый обязательный результат 17/32 сохранён в artifacts/context-cascade-frozen-baseline.json. После использования ошибок для разработки: 29/32 по символам, 32/32 round-trip. Два расхождения относятся к служебным словам адреса в замороженном эталоне; одно — избыточно скрытое публичное имя. Набор теперь регрессионный, не независимый. Старые обязательные наборы: 52/52 + 32/32 по символам. Старые три диагностические ошибки (68 пропущенных символов) и новый неоднозначный адрес (15) остаются; это не доказательство точности 95%.
- Исследовательский PyTorch 2.8 / Python 3.11 экспорт дважды вызвал SIGBUS в Apple Accelerate BLAS, PID 94585/95428, 14:49:38 и 14:50:17 MSK. Нативный стек подтвердил Torch, НЕ ONNX Runtime. Эксперимент остановлен; artifacts/tiny_nli_lab.py блокирует macOS до нативных импортов. НЕ ПОВТОРЯТЬ Torch-экспорт на этом Mac. Кандидат rubert-tiny-bilingual-nli не включён в manifest; качество/скорость не оценены, лицензия fine-tune не установлена. Статус: artifacts/tiny-nli-experiment-status.json. Удалять crash reports или скрывать эти события нельзя.
- Итоговый pytest: 511 passed, 0 skipped, 31,30 с, exit 0; artifacts/context-improvements-tests.xml. Включены настоящий временный Redis и тест нормального завершения нативных NER/NLI. Один промежуточный запуск выявил нестабильный 30-мс предел в тесте счётчиков; три count-based теста получили окно 1 с, точные assertions сохранены. React production build прошёл. Новых crash reports после остановки эксперимента не появилось.
- Живой HTTP /process: четыре сценария mask/restore/replay прошли; artifacts/context-improvements-live.json. В in-app браузере 5173 выполнено сравнение Бернарда Шоу с реальными объяснениями и смешанный Unicode-пример: 2 частных фрагмента скрыты, публичное имя сохранено, точный round-trip (317 мс UI).
- Последний короткий paired benchmark (10 с, concurrency 8, один worker MemoryVault, обе модели, тот же Mac и генератор): 1148/1148 корректных ответов, 114,0 успешных RPS в окне, p95 301,18 мс, p99 344,84 мс. До объединения гипотез новая политика показывала 71,7 RPS; прежние 207 RPS были без дополнительного privacy-veto. artifacts/context-improvements-throughput.json и docs/context-improvements-2026-09-23.md. Цель 1000 RPS по-прежнему НЕ ДОСТИГНУТА; новая проверка приватности имеет измеримую стоимость.
- Dev-сервис перезапущен с последними изменениями (`npm run dev:all`, frontend5173/backend8000), health ready=true. NOT_RUN: новое Linux-развёртывание, актуальный ZIP/чистая установка, 100k SLA и Redis multiworker throughput. Следующий шаг — компактная обученная контекстная модель с отдельными train/calibration/test данными; экспорт выполнять в подходящей Linux-среде, а не повторять падающий macOS-путь.
