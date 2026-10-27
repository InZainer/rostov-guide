> generated_by: nexus-mapper v2
> verified_at: 2026-10-22
> provenance: AST-backed (Python + JavaScript, 41 files, 0 parse errors) for module/class/function inventory; internal import edges are from manual inspection (grep of `from .`/`from ..` and JS `import`) because the AST extractor does not resolve Python relative imports; hotspots skipped: no git metadata.

# Git-криминалистика

**hotspots skipped: no git metadata.** В `rostov-guide/` нет `.git`, поэтому `git_detective.py` не запускался, `raw/git_stats.json` не создан.

Косвенные признаки зон риска (не git-данные, `inferred from manual inspection` и `docs/architecture.md`, раздел «Что нашла перепроверка»):
- `agent/tools.py`, `agent/plugins.py`, `agent/prompt.py` — правились последними волнами исправлений поведения агента.
- `web/runs.py`, `web/routes.py`, `static/js/app.js`, `static/js/chat.js` — правки очереди, перезагрузки и согласования потока с историей.
- Крупнейшие модули: `static/js/app.js` (525 строк), `static/js/chat.js` (374), `agent/tools.py` (250).

Чтобы получить настоящие горячие точки: `git init` + история коммитов, затем повторный запуск nexus-mapper.
