> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# rostov-guide — карта проекта

**Что это.** MVP агента-справочника по 8860 местам Ростова-на-Дону (снимок OSM, `data/poi.sqlite`). Пользователь пишет вопрос словами, агент на Google ADK + локальной модели LM Studio ищет места (в т.ч. по смыслу через Qdrant), шаги агента идут в браузер по SSE. Учебное задание курса «Агентный ИИ»; оценивается UX + архитектура.

**Запуск:** `uv run rostov-guide` (нужен LM Studio с `qwen3.5-4b` окном 16k и `text-embedding-bge-m3`). Тесты: `uv run pytest` (62, без сети/модели). Оценки: `eval-search`, `eval-agent -n 3`.

## Системы (code_path проверены)

| Система | Путь | Суть |
|---|---|---|
| Places | `src/rostov_guide/places/` | справочник в памяти, геокодинг, часы, словари видов/кухонь — **хаб**: импортируют 8 модулей |
| Search | `src/rostov_guide/search/` | Qdrant dense+BM25, RRF в приложении, кеш эмбеддингов, без сервера — коллекция в памяти |
| Agent | `src/rostov_guide/agent/` | LlmAgent, 4 инструмента, плагины, сжатие контекста, `eval-agent` |
| Web | `src/rostov_guide/web/` | FastAPI, auth, `RunManager` (очередь, буфер SSE), перевод событий ADK |
| Frontend | `src/rostov_guide/static/` | ES-модули без сборки, Leaflet |
| Composition | `config.py`, `services.py`, `__init__.py` | Settings, сборка Services, CLI |

Поток вопроса: `static/js/app.js` → `POST /api/chats/{id}/messages` (`web/routes.py`) → `RunManager.start` (`web/runs.py`) → `Runner.run_async` (`agent/factory.py`) → инструменты (`agent/tools.py`) → `web/events.translate` → SSE → `static/js/chat.js`.

## Что важно знать до правок

- **Контракт событий UI** задаёт `web/events.py` (`thought_delta`, `tool_call`, `tool_result`, `answer_delta`, `usage`, `done`); его же читают история чата и `static/js/chat.js`. Менять — синхронно на обеих сторонах.
- **Инструменты** (`agent/tools.py`) рассчитаны на 4B-модель: плоские аргументы, ошибки — `{"error","hint"}`, пустые поля — «нет в базе». Не выбрасывать эти соглашения: на них завязаны находки перепроверки (`docs/architecture.md`).
- `.env` содержит настройки модели (значения не копировать). `data/` кроме `poi.sqlite` не коммитится (сессии, пользователи, кеш эмбеддингов, `.secret`).
- **evidence gap:** нет git-истории — горячие точки и связанность изменений не посчитаны.
- **evidence gap:** AST-экстрактор не резолвит относительные импорты Python; `query_graph.py --impact/--who-imports` для этого репозитория **недосчитывает** зависимости — сверяйтесь с `arch/dependencies.md` (ручная проверка).

## [操作指南] 强制执行步骤

> 本节是对所有读取本文件的 AI 发出的硬性操作指令，不是建议。

### 步骤1 — 必须先读完以下所有文件（顺序不限）

读完本文件后，在执行任何任务之前，必须依次 read 以下文件完整内容：

- `.nexus-map/arch/systems.md` — 系统边界与代码位置
- `.nexus-map/arch/dependencies.md` — 系统间依赖关系与 Mermaid 图
- `.nexus-map/arch/test_coverage.md` — 测试面与证据缺口
- `.nexus-map/hotspots/git_forensics.md` — Git 热点与耦合风险
- `.nexus-map/concepts/domains.md` — 核心领域概念

> 这些文件均为高密度摘要，总量通常 < 5000 tokens，是必要的上下文成本。
> 不得以"任务简单"或"只改一个文件"为由跳过。

### 步骤2 — 按任务类型追加操作（步骤1 完成后执行）

- 若任务涉及**接口修改、新增跨模块调用、删除/重命名公共函数**：
  → 必须运行 `query_graph.py --impact <目标文件>` 确认影响半径后再写代码。
- 若任务需要**判断某文件被谁引用**：
  → 运行 `query_graph.py --who-imports <模块名>`。
- 若仓库结构已发生重大变化（新增系统、重构模块边界）：
  → 任务完成后评估是否需要重新运行 nexus-mapper 更新知识库。
