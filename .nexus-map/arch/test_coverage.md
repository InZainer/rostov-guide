> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# Тестовая поверхность (статически)

> Тесты при картографировании **не запускались** (правило минимального исполнения). Последний известный прогон: 62 passed — из `README.md`/`docs/architecture.md`, не перепроверено в этой сессии разметки.

| Тест | Что покрывает |
|---|---|
| `tests/test_hours.py` | `places/hours.py`: 24/7, перерывы, ночные интервалы, «до 24:00», неизвестный формат |
| `tests/test_places.py` | `kinds.resolve_kind`, `repository.filter/geocode/by_distance/brief`, `sparse`, `documents` |
| `tests/test_search.py` | `search/store.PlaceIndex` с `FakeEmbedder` и Qdrant в памяти |
| `tests/test_tools.py` | все 4 инструмента, ошибки-наблюдения, `only_24_7`, `query_adds_meaning` |
| `tests/test_plugins.py` | `RepeatGuard`, `UserTurnGuard` |
| `tests/test_events.py` | `web/events.translate/history` |
| `tests/test_runs.py` | `RunManager`: очередь, переподключение, отмена, лимиты, ошибки |
| `tests/test_api.py` | API целиком через ASGITransport: auth, изоляция, CSRF, SSE, история, очередь+перезагрузка |

Фейки: `tests/conftest.py` (`FakeEmbedder`, мини-база мест), `tests/fakes.py` (`FakeRunner` вместо ADK Runner).

## Пробелы
- **Frontend (`static/js/`)** — автотестов нет; проверялся вручную в браузере.
- `web/auth.LoginLimiter`, `/api/status` (зонд LM Studio), `embeddings.py` (ретраи, дисковый кеш) — без прямых тестов.
- Поведение живой модели покрывается не pytest, а `uv run rostov-guide eval-agent` (9 сценариев).
