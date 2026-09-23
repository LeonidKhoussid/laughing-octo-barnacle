# Scorecard — AlfaGen PII Gateway

> Актуальное сопоставление с полным ТЗ от 23.09.2026: [requirements-verification.md](requirements-verification.md). Ниже сохранены исторические этапы и их результаты; старые утверждения о завершении, отсутствии Приложения A, regex-only детекции и оценочные 100k не являются текущим статусом.

Сопоставление критериев рубрики с реализацией и тестами. Баллы не присваиваются самостоятельно; отмечается подтверждённое покрытие. Официальные баллы не выставляются (R05, R07).

| Код | Критерий | Максимум | Статус | Доказательство |
| --- | --- | --- | --- | --- |
| C1 | Идентификация и маскирование | 6 | COVERED | 17 категорий IMPLEMENTED; per-category P/R/F1=1.000 на синтетике; tests/unit/test_detectors.py |
| C2 | Демаскирование | 3 | COVERED | Exact round-trip `unmask(mask(x))==x`; повторы, конкурентность, cross-worker; tests/unit/test_roundtrip.py, tests/property/test_roundtrip_property.py, tests/integration/test_redis_vault.py |
| C3 | Точность контекста и вариации | 4 | COVERED | Контрастные пары (Пушкин, офис vs адрес), границы, Unicode; tests/unit/test_detectors.py, tests/property/test_roundtrip_property.py |
| C4 | Настройки и расширение | 4 | COVERED | Per-consumer политики в configs/consumers.yaml; добавление типа через registry/config без правки ядра; Trust Lab Action B |
| C5 | Производительность | 4 | PARTIAL | Актуальные HTTP-измерения с моделями: docs/benchmark.md; 1000 успешных RPS не достигнуто, SLA для 100k и масштабирование Redis требуют отдельной проверки |
| C6 | Безопасность, логи, метрики | 3 | COVERED | Canary-тесты (tests/integration/test_g5_safety.py, test_log_leak.py); AEAD; limits; egress guard |
| C7 | Расширенные сценарии | 3 | COVERED | Токенизация, конфигурируемые виды масок, комбинационное правило CARD+PIN; tests/integration/test_g6_ui.py, test_g7_trust_lab.py |
| C8 | Демо и презентация | 3 | COVERED | Русский UI на /; judge-guide.md, demo-script.md; Trust Lab |

## Финальная встреча (отдельный лист)

Четыре оценки 1/2/3: доверие, внедряемость, сила решения, команда. Не складываются с 30 в официальную шкалу 42.

## Статус на 22.09.2026 (G8)

Проект завершён (G1–G8). Все 17 категорий IMPLEMENTED, 223 теста PASSED, source ZIP собран и проверен чистой установкой, Dockerfile/compose.yaml созданы. Официальный адаптер UNVERIFIED (нет Приложения A); отсутствие схемы блокирует только подтверждение совместимости официального адаптера, а не независимое ядро, demo API и тесты. Публичный endpoint BLOCKED (нет инфраструктуры/credentials); предоставлен локальный запуск и контейнерный путь.

**Важно:** статус COVERED означает подтверждённое покрытие критерия реализацией и тестами, а не официальные баллы жюри. Официальные баллы не присваиваются.
