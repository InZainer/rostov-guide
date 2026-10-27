> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# Понятия предметной области

- **Place (место)** — запись OSM: вид (`kind`), название, адрес, часы, кухня, координаты. Для модели — `brief()`, для UI — `card()`. Отсутствие данных выражается явно: «нет в базе».
- **Kind / Group** — тег OSM (`pharmacy`) и группа (`food`); модель может писать «аптеки», `resolve_kind` нормализует. Еда ищется всей группой.
- **Document expansion** — текст документа индекса дополняется словарём: кухня → блюда, вид → потребности («снять наличные»). Только для поиска, пользователю не показывается.
- **Hit / found_by** — результат гибридного поиска с меткой источника: «смысл», «слова», «смысл+слова».
- **Chat = ADK Session** — чат пользователя хранится как сессия ADK; заголовок и `user_location` — в состоянии сессии.
- **Run (прогон)** — один вопрос: фоновая задача, очередь к модели, буфер событий с `seq`, статусы `queued/running/done/error/cancelled`.
- **UI event** — `thought_delta/thought/tool_call/tool_result/answer_delta/answer/usage/compaction/error/done`.
- **Compaction** — сжатие истории ADK в сводку при росте промпта (порог 10k токенов при окне 16k); `UserTurnGuard` сохраняет вопрос.
- **Observation-error** — ответ инструмента `{"error","hint"}` вместо исключения; повторный идентичный вызов → наблюдение от `RepeatGuard`.
- **User location** — точка «я здесь» с карты; передаётся с каждым вопросом (в т.ч. «нет точки»).
