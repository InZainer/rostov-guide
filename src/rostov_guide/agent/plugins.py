"""Сквозная логика агента — плагинами ADK, а не кодом внутри инструментов.

- ToolErrorsAsObservations: упавший инструмент не роняет прогон, модель
  получает `{"error": ...}` как наблюдение и может исправиться.
- RepeatGuard: повтор вызова с теми же аргументами — наблюдение, а не круг в цикле.
- UserTurnGuard: после сжатия контекста вопрос пользователя остаётся в запросе.
- JournalPlugin: каждый шаг прогона — строка JSONL в `runs/`: вызовы модели
  с usage и задержкой, вызовы инструментов с аргументами и сжатым результатом.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

log = logging.getLogger(__name__)


class ToolErrorsAsObservations(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="tool_errors_as_observations")

    async def on_tool_error_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                     tool_context: ToolContext, error: Exception) -> dict | None:
        log.warning("инструмент %s упал: %r", tool.name, error)
        return {"error": f"инструмент {tool.name} не смог выполниться: {error}",
                "hint": "проверь аргументы или попробуй другой инструмент"}


class RepeatGuard(BasePlugin):
    """Повторный вызов инструмента с теми же аргументами в одном прогоне не выполняется.

    Маленькая модель, не найдя в инструменте нужного фильтра, зовёт его снова и снова
    с теми же аргументами, пока не кончится лимит шагов. Повтор ничего нового не даст,
    поэтому вместо выполнения модель получает наблюдение: результат уже есть выше.
    """

    def __init__(self) -> None:
        super().__init__(name="repeat_guard")
        self._seen: dict[str, set[str]] = {}

    async def before_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                   tool_context: ToolContext) -> dict | None:
        key = f"{tool.name}:{json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str)}"
        seen = self._seen.setdefault(tool_context.invocation_id, set())
        if key in seen:
            return {"error": f"{tool.name} уже вызывался с теми же аргументами — результат выше не изменится",
                    "hint": "ответь пользователю по уже полученным данным; если их не хватает — честно скажи, "
                            "чего нет в базе, или вызови инструмент с другими аргументами"}
        seen.add(key)
        return None

    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        self._seen.pop(invocation_context.invocation_id, None)


class UserTurnGuard(BasePlugin):
    """В запросе к модели всегда есть реплика пользователя.

    Сжатие контекста ADK срабатывает и посреди прогона: старые события, включая
    сам вопрос, уходят в сводку, которая подаётся как реплика агента, а остаются
    только вызовы инструментов. Шаблон чата Qwen на таком запросе падает
    («No user query found in messages»), да и модели полезно видеть вопрос дословно.
    Поэтому, если вопроса в запросе не осталось, возвращаем его в начало.
    """

    def __init__(self) -> None:
        super().__init__(name="user_turn_guard")

    async def before_model_callback(self, *, callback_context: CallbackContext,
                                    llm_request: LlmRequest) -> None:
        has_question = any(c.role == "user" and any(p.text for p in (c.parts or []))
                           for c in llm_request.contents)
        question = callback_context.user_content
        if not has_question and question is not None:
            llm_request.contents.insert(0, question)
            log.info("вопрос пользователя возвращён в запрос после сжатия контекста")


def _short(value: Any, limit: int = 600) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else text[:limit] + "…"


class JournalPlugin(BasePlugin):
    def __init__(self, runs_dir: Path) -> None:
        super().__init__(name="journal")
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._started: dict[str, float] = {}       # начало текущего шага модели / инструмента
        self._lock = asyncio.Lock()

    async def _write(self, record: dict) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record}
        path = self.runs_dir / f"agent-{datetime.now():%Y-%m-%d}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        async with self._lock:
            await asyncio.to_thread(_append, path, line)

    async def before_run_callback(self, *, invocation_context: InvocationContext) -> None:
        text = ""
        if invocation_context.user_content and invocation_context.user_content.parts:
            text = " ".join(p.text or "" for p in invocation_context.user_content.parts)
        await self._write({"type": "run_start", "invocation": invocation_context.invocation_id,
                           "user": invocation_context.user_id, "session": invocation_context.session.id,
                           "question": text})

    async def before_model_callback(self, *, callback_context: CallbackContext,
                                    llm_request: LlmRequest) -> None:
        self._started[f"model:{callback_context.invocation_id}"] = time.perf_counter()

    async def after_model_callback(self, *, callback_context: CallbackContext,
                                   llm_response: LlmResponse) -> None:
        if llm_response.partial:
            return
        started = self._started.pop(f"model:{callback_context.invocation_id}", None)
        usage = llm_response.usage_metadata
        calls = [p.function_call.name for p in (llm_response.content.parts if llm_response.content else [])
                 if p.function_call]
        await self._write({
            "type": "model", "invocation": callback_context.invocation_id,
            "ms": round(1000 * (time.perf_counter() - started)) if started else None,
            "input_tokens": usage.prompt_token_count if usage else None,
            "output_tokens": usage.candidates_token_count if usage else None,
            "thought_tokens": usage.thoughts_token_count if usage else None,
            "tool_calls": calls, "error": llm_response.error_message,
        })

    async def before_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                   tool_context: ToolContext) -> None:
        self._started[f"tool:{tool_context.function_call_id}"] = time.perf_counter()

    async def after_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                  tool_context: ToolContext, result: dict) -> None:
        started = self._started.pop(f"tool:{tool_context.function_call_id}", None)
        await self._write({
            "type": "tool", "invocation": tool_context.invocation_id, "name": tool.name, "args": tool_args,
            "ms": round(1000 * (time.perf_counter() - started)) if started else None,
            "ok": "error" not in (result or {}), "result": _short(result),
        })

    async def on_model_error_callback(self, *, callback_context: CallbackContext,
                                      llm_request: LlmRequest, error: Exception) -> None:
        await self._write({"type": "model_error", "invocation": callback_context.invocation_id,
                           "error": repr(error)[:500]})

    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        await self._write({"type": "run_end", "invocation": invocation_context.invocation_id})


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
