> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# Системы и их границы

## Places — справочник мест · `src/rostov_guide/places/` (580 строк)
- `repository.py` (218): `Place` (`brief()` для модели — адрес/часы «нет в базе» вместо пропуска; `card()` с координатами для UI), `PlaceRepository` (загрузка всей `poi.sqlite` в память через aiosqlite, `filter`, `by_distance`, `geocode`: дом → середина улицы → место по названию).
- `hours.py` (126): разбор OSM `opening_hours`, «открыто до 21:00», «до 24:00», ночные интервалы, таймзона UTC+3.
- `kinds.py` (235): `KIND_LABELS`, `GROUPS` (food…), `CUISINES` (кухня → блюда), `KIND_NEEDS` (вид → потребности), `resolve_kind` («аптеки» → `pharmacy`).
- Самый связанный модуль проекта (8 входящих импортов, ручной подсчёт).

## Search — гибридный поиск · `src/rostov_guide/search/` (527 строк)
- `documents.py`: текст документа с расширением словарём (`DOC_VERSION=4`, смена версии → пересборка).
- `embeddings.py`: `EmbeddingClient` к `/embeddings`, ретраи, LRU запросов, дисковый кеш `data/embed_cache.sqlite`.
- `sparse.py`: BM25 (snowball), шумовые слова запроса, `meaningful_terms` (используется агентом).
- `store.py`: `PlaceIndex` — Qdrant (в памяти без `QDRANT_URL`, сервер — с ним), `query_batch_points` dense+bm25, RRF в Python, `found_by`, фоновая пересборка без простоя, `IndexStatus`.
- `evaluate.py`: 20 размеченных запросов, P@5/Hit@1 по режимам.

## Agent — ADK · `src/rostov_guide/agent/` (729 строк)
- `factory.py`: `LiteLlm("openai/…")`, `LlmAgent`, `App` с плагинами и `EventsCompactionConfig` (суммаризатор без рассуждения), `Runner` + `SqliteSessionService`.
- `tools.py`: `search_places`, `find_places`, `nearest_places`, `place_details`; `query_adds_meaning`; ключ состояния `user_location`.
- `prompt.py`: `build_instruction` (InstructionProvider: время, точка пользователя), `SUMMARY_PROMPT`.
- `plugins.py`: `ToolErrorsAsObservations`, `RepeatGuard`, `UserTurnGuard`, `JournalPlugin` (JSONL в `runs/`).
- `evaluate.py`: 9 сценариев с эталоном из базы, pass@1/pass^n, Уилсон.

## Web — API и прогоны · `src/rostov_guide/web/` (894 строки)
- `app.py`: lifespan (импорт litellm, сборка сервисов, фоновый индекс), CSRF-проверка Origin, `Cache-Control: no-cache` для статики, MIME для `.js`.
- `auth.py`: scrypt в пуле потоков, JWT в HttpOnly-cookie, `LoginLimiter`, `UserStore` (`data/app.sqlite`).
- `routes.py`: чаты (= сессии ADK), `POST messages` → 202 + run_id, SSE `/api/runs/{id}/events` (204 когда всё доставлено), cancel, places, reverse, status (включая состояние модели в LM Studio).
- `runs.py`: `RunManager` — семафор `max_parallel_runs`, очередь с позициями, лимиты чат/пользователь, таймаут, `abort_signal`, человеческие ошибки.
- `events.py`: `translate` (событие ADK → события UI) и `history` (сессия → сообщения).

## Frontend · `src/rostov_guide/static/` (1256 строк JS + CSS/HTML)
- `app.js` (525): контроллер — вход, чаты, отправка, подписка, статус, точка на карте, тема.
- `chat.js` (374): `AssistantView` — ход мысли, ответ, карточки, связка с картой; одинаково для потока и истории.
- `map.js`, `api.js` (`follow` на EventSource), `markdown.js` (экранирование до разметки), `ui.js`.

## Composition / CLI · `config.py`, `services.py`, `__init__.py`
- `Settings` (pydantic-settings, `.env`), `Services` + `ensure_index(background=…)`, CLI `serve|index|eval-search|eval-agent`.

## Внешнее
- **LM Studio** (inferred, вне репозитория): чат-модель и эмбеддинги; выгружает модели по простою.
- Данные: `data/poi.sqlite` (OSM, ODbL), остальное в `data/` генерируется.
