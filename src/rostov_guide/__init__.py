"""Ростов-гид: агент-справочник по местам Ростова-на-Дону.

    uv run rostov-guide                 # веб-приложение на http://127.0.0.1:8000
    uv run rostov-guide index --rebuild # пересобрать векторный индекс
    uv run rostov-guide eval-search     # сравнить поиск: смысл / слова / гибрид
    uv run rostov-guide eval-agent -n 3 # прогнать сценарии на живой модели
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")   # не ходить в сеть за прайсом при импорте


def main() -> None:
    parser = argparse.ArgumentParser(prog="rostov-guide", description="Агент-справочник по Ростову")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="запустить веб-приложение (по умолчанию)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    index = sub.add_parser("index", help="собрать векторный индекс мест")
    index.add_argument("--rebuild", action="store_true", help="пересобрать, даже если индекс актуален")
    sub.add_parser("eval-search", help="оценить качество поиска по размеченным запросам")
    agent_eval = sub.add_parser("eval-agent", help="прогнать сценарии агента на живой модели")
    agent_eval.add_argument("-n", type=int, default=1, help="сколько раз прогнать каждый сценарий")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .config import get_settings

    settings = get_settings()

    if args.command in (None, "serve"):
        import uvicorn

        uvicorn.run("rostov_guide.web.app:create_app", factory=True,
                    host=getattr(args, "host", None) or settings.host,
                    port=getattr(args, "port", None) or settings.port,
                    reload=getattr(args, "reload", False), log_level="info")
        return

    from .services import build_services

    async def run_index() -> None:
        services = await build_services(settings)
        await services.ensure_index(background=False, rebuild=args.rebuild)
        print(f"индекс актуален: {len(services.places)} мест, модель {settings.embed_model}")
        await services.aclose()

    async def run_eval() -> None:
        from .search.evaluate import evaluate

        services = await build_services(settings)
        await services.ensure_index(background=False)
        result = await evaluate(services.index, services.places, settings.runs_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await services.aclose()

    async def run_agent_eval() -> None:
        from .agent.evaluate import evaluate_agent

        services = await build_services(settings)
        await services.ensure_index(background=False)
        summary = await evaluate_agent(services, args.n, settings.runs_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        await services.aclose()

    jobs = {"index": run_index, "eval-search": run_eval, "eval-agent": run_agent_eval}
    asyncio.run(jobs[args.command]())
