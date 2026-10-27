> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# Зависимости

> Все стрелки ниже — `inferred from manual inspection` (grep импортов): AST-экстрактор не разрешает относительные импорты Python. Циклов между системами не найдено.

```mermaid
graph LR
  FE[static/ Frontend] -->|REST + SSE| WEB[web/]
  WEB --> AGENT[agent/]
  WEB --> PLACES[places/]
  WEB --> COMP[services.py + config.py]
  AGENT --> PLACES
  AGENT --> SEARCH[search/]
  AGENT --> COMP
  COMP --> SEARCH
  COMP --> PLACES
  SEARCH --> PLACES
  AGENT -.->|LiteLLM chat| LMS[(LM Studio)]
  SEARCH -.->|/embeddings| LMS
  WEB -.->|status probe| LMS
  AGENT --> SESS[(data/sessions.sqlite)]
  WEB --> USERS[(data/app.sqlite)]
  SEARCH --> QD[(Qdrant: in-memory / server)]
```

## Входящие импорты (ручной подсчёт)

| Модуль | Кем импортируется |
|---|---|
| `places/repository.py` | search/documents, search/store, search/evaluate, agent/tools, agent/evaluate, services, web/events, web/runs |
| `services.py` | agent/factory, agent/tools, agent/evaluate, web/app, `__init__` |
| `places/hours.py` | places/repository, agent/tools, agent/prompt, web/events, web/routes |
| `places/kinds.py` | places/repository, search/documents, agent/tools, web/events |
| `config.py` | services, agent/factory, web/app, web/runs, `__init__` |
| `agent/tools.py` | agent/factory, agent/prompt, web/routes (`USER_LOCATION_KEY`) |
| `agent/factory.py` | agent/evaluate, web/app, web/routes (`APP_NAME`) |
| `web/events.py` | web/runs, web/routes |

Frontend: `app.js` → `api.js`, `chat.js`, `map.js`, `ui.js`; `chat.js` → `markdown.js`, `ui.js`; `map.js` → `ui.js` (AST-backed, JS).

## Последовательность вопроса

```mermaid
sequenceDiagram
  participant B as Браузер
  participant R as routes.py
  participant M as RunManager
  participant A as ADK Runner
  participant T as tools.py
  B->>R: POST /api/chats/{id}/messages
  R->>M: start(state_delta: user_location, title)
  R-->>B: 202 {run_id}
  B->>R: GET /api/runs/{id}/events (EventSource)
  M->>M: очередь (semaphore), event queued/started
  M->>A: run_async(streaming=SSE, max_llm_calls, abort_signal)
  A->>T: вызов инструмента
  T-->>A: dict (error+hint / places)
  A-->>M: Event → events.translate → buffer[seq]
  M-->>B: data: {...} (id: seq), в конце done
```

## Хрупкие стыки
- `web/routes.py` ↔ `agent/tools.py`: общий ключ состояния `user_location`.
- `web/events.py` ↔ `static/js/chat.js`: формат событий UI (ручной контракт, без схемы).
- `agent/tools.py` → `search/sparse.py` (`meaningful_terms`): агент зависит от токенизации поиска.
