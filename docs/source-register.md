# Реестр источников — AlfaGen PII Gateway

Реестр материалов организаторов/пользователя и технических справочников. Не заменяет более ранние сгенерированные HTML/Markdown-планы, которые содержали предварительные допущения.

## Материалы организаторов / пользователя

| Код | Материал | Статус в workspace | Примечание |
| --- | --- | --- | --- |
| [S1] | Исходное ТЗ в сообщении пользователя | Отражено в мастер-промпте | Полного Приложения A нет |
| [S2] | `Критерии_оценивания _альфа.pdf`, 4 стр. | Не найден в workspace | Рубрика 30 баллов |
| [S3] | `критерии_фрейм_топы (1).pdf`, 2 стр. | Не найден в workspace | Финальная встреча |
| [S4] | `Pasted markdown.md`, переписка | Не найден в workspace | Уточнения 22.09.2026 |
| [S5] | Сообщение с семью вопросами/ответами | Отражено в мастер-промпте | Уточнения контракта |
| [S6] | Сообщение об ограничении инструментов | Отражено в мастер-промпте | Только DeepSeek/VS Code/Kilo Code; отдельного ответа о промптах нет |
| [S7] | Пять изображений презентации | Не найдены в workspace | Сроки, инфраструктура |
| [S8] | `ds.pdf` — ТЗ трека, Приложение A (стр. 6–7), Приложение B (стр. 8–9) | `/Users/leo/Downloads/ds.pdf` | Официальный контракт `POST /process` и инструкция нагрузочного тестирования |

## Технические справочники

| Код | Справочник | URL |
| --- | --- | --- |
| [D1] | Kilo Code — Custom Rules | https://kilo.ai/docs/customize/custom-rules |
| [D2] | Kilo Code — AGENTS.md | https://kilo.ai/docs/customize/agents-md |
| [D3] | Natasha | https://github.com/natasha/natasha |
| [D4] | Hugging Face Transformers — Token classification | https://huggingface.co/docs/transformers/tasks/token_classification |
| [D5] | Redis — Transactions | https://redis.io/docs/latest/develop/using-commands/transactions/ |
| [D6] | Cryptography — Authenticated encryption | https://cryptography.io/en/latest/hazmat/primitives/aead/ |
| [D7] | FastAPI — Concurrency and async/await | https://fastapi.tiangolo.com/async/ |
| [D8] | RFC 9110, §10.2.3 Retry-After | https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after |
| [D9] | Prometheus — Instrumentation | https://prometheus.io/docs/practices/instrumentation/ |

## Статус проверки допустимости

- Разрешённые инструменты: предоставленный DeepSeek, VS Code, Kilo Code — CONFIRMED (из [S6]).
- Допустимость внешнего планирующего документа: отдельного ответа организаторов о заранее подготовленных промптах нет. Это не подтверждённое разрешение и не отдельно установленный запрет. Требование письменного разрешения было авторской интерпретацией первой версии, а не цитатой организатора; в версии 2 оно не является автоматическим стоп-фактором.
- Происхождение ранее созданного кода: нет ранее созданного кода в workspace.
- Runtime BERT: ранее отдельно разрешён внутри pipeline; проверить актуальность при конфликте.
- Официальный API Appendix A: **VERIFIED** — контракт `POST /process` реализован в `app/api/autocheck.py` и проверен контрактными тестами и живым HTTP-запросом (см. docs/api-contract.md).
