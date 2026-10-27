"""Прогоны агента отдельно от HTTP-соединений.

Вопрос пользователя — это «прогон»: фоновая задача, которая крутит ADK Runner
и складывает события интерфейса в буфер с порядковыми номерами. Браузер
подписывается на буфер через SSE (`EventSource`) и при обрыве связи или
перезагрузке страницы переподключается с `Last-Event-ID`, получая пропущенное.
Закрыл вкладку — агент дорабатывает, ответ сохраняется в истории чата.

Многопользовательский режим упирается не в веб-сервер (он асинхронный и держит
сотни соединений), а в модель: локальная LLM на одной видеокарте считает
ограниченное число запросов одновременно. Поэтому перед моделью стоит очередь:
`max_parallel_runs` прогонов идут, остальные ждут, и каждый видит свою позицию.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.genai import types

from ..config import Settings
from ..places.repository import PlaceRepository
from .events import translate

log = logging.getLogger(__name__)

KEEP_FINISHED_S = 600       # сколько держать завершённый прогон для переподключений


class Busy(Exception):
    """Отказ принять вопрос: в чате уже идёт прогон или очередь переполнена."""


@dataclass
class Run:
    id: str
    user_id: str
    chat_id: str
    question: str
    title: str | None = None        # заголовок нового чата: в сессию ADK попадёт, лишь когда прогон стартует
    status: str = "queued"          # queued | running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    closed: bool = False            # отправлено финальное событие done — больше событий не будет

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "cancelled")

    async def emit(self, event: dict) -> None:
        async with self.changed:
            self.events.append({"seq": len(self.events) + 1, **event})
            self.closed = self.closed or event["type"] == "done"
            self.changed.notify_all()


class RunManager:
    def __init__(self, runner: Runner, places: PlaceRepository, settings: Settings) -> None:
        self.runner = runner
        self.places = places
        self.settings = settings
        self.runs: dict[str, Run] = {}
        self._slots = asyncio.Semaphore(settings.max_parallel_runs)
        self._waiting: list[Run] = []
        self._janitor: asyncio.Task | None = None

    # ---------------------------------------------------------------- приём вопросов

    def active_for_chat(self, chat_id: str) -> Run | None:
        return next((r for r in self.runs.values() if r.chat_id == chat_id and not r.finished), None)

    def start(self, *, user_id: str, chat_id: str, text: str, state_delta: dict | None = None) -> Run:
        if self.active_for_chat(chat_id):
            raise Busy("В этом чате агент ещё отвечает на предыдущий вопрос")
        if sum(1 for r in self.runs.values() if r.user_id == user_id and not r.finished) >= 2:
            raise Busy("У вас уже два вопроса в работе — дождитесь ответа")
        if len(self._waiting) >= self.settings.max_queue:
            raise Busy("Сервер перегружен: очередь к модели заполнена, попробуйте через минуту")
        run = Run(id=uuid.uuid4().hex, user_id=user_id, chat_id=chat_id, question=text,
                  title=(state_delta or {}).get("title"))
        self.runs[run.id] = run
        run.task = asyncio.create_task(self._execute(run, state_delta or {}), name=f"run-{run.id}")
        if self._janitor is None:
            self._janitor = asyncio.create_task(self._cleanup_loop(), name="runs-janitor")
        return run

    def cancel(self, run: Run) -> None:
        run.abort.set()
        if run.task and run.status == "queued":
            run.task.cancel()

    # ---------------------------------------------------------------- исполнение

    async def _broadcast_queue(self) -> None:
        for position, waiting in enumerate(self._waiting, start=1):
            await waiting.emit({"type": "queued", "position": position})

    async def _execute(self, run: Run, state_delta: dict) -> None:
        totals = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "context_tokens": 0}
        answer = ""
        place_ids: list[int] = []
        self._waiting.append(run)
        try:
            await self._broadcast_queue()
            async with self._slots:
                self._waiting.remove(run)
                await self._broadcast_queue()
                run.status = "running"
                run.started = time.monotonic()
                await run.emit({"type": "started"})
                async with asyncio.timeout(self.settings.run_timeout_s):
                    async for event in self.runner.run_async(
                        user_id=run.user_id, session_id=run.chat_id,
                        new_message=types.Content(role="user", parts=[types.Part(text=run.question)]),
                        state_delta=state_delta or None,
                        run_config=RunConfig(streaming_mode=StreamingMode.SSE,
                                             max_llm_calls=self.settings.max_llm_calls),
                        abort_signal=run.abort,
                    ):
                        for ui in translate(event, self.places):
                            if ui["type"] == "usage":
                                totals["input_tokens"] += ui["input_tokens"]
                                totals["output_tokens"] += ui["output_tokens"]
                                totals["llm_calls"] += 1
                                totals["context_tokens"] = ui["input_tokens"]
                                await run.emit({"type": "usage", **totals})
                                continue
                            if ui["type"] == "answer":
                                answer = (answer + "\n\n" + ui["text"]).strip()
                            if ui["type"] == "tool_result":
                                place_ids += [c["id"] for c in ui.get("places", [])]
                            await run.emit(ui)
                        if event.error_message:
                            await run.emit({"type": "error", "message": f"модель вернула ошибку: {event.error_message}"})
            run.status = "cancelled" if run.abort.is_set() else "done"
        except asyncio.CancelledError:
            run.status = "cancelled"
        except TimeoutError:
            run.status = "error"
            await run.emit({"type": "error", "message": f"агент не уложился в {int(self.settings.run_timeout_s)} с"})
        except LlmCallsLimitExceededError:
            run.status = "error"
            await run.emit({"type": "error", "message": "агент сделал слишком много шагов и был остановлен "
                                                        f"(лимит {self.settings.max_llm_calls})"})
        except Exception as exc:                    # noqa: BLE001 — любая ошибка должна дойти до пользователя
            log.exception("прогон %s упал", run.id)
            run.status = "error"
            await run.emit({"type": "error", "message": _humanize(exc)})
        finally:
            if run in self._waiting:
                self._waiting.remove(run)
                await self._broadcast_queue()
            run.finished_at = time.monotonic()
            mentioned = [pid for pid in dict.fromkeys(place_ids)
                         if (p := self.places.get(pid)) and p.title and p.title in answer]
            cost = (totals["input_tokens"] * self.settings.llm_price_in_rub
                    + totals["output_tokens"] * self.settings.llm_price_out_rub) / 1e6
            await run.emit({"type": "done", "status": run.status, **totals,
                            "elapsed_ms": int(1000 * (run.finished_at - run.started)),
                            "cost_rub": round(cost, 4), "mentioned": mentioned})

    # ---------------------------------------------------------------- подписка

    async def subscribe(self, run: Run, after: int = 0) -> AsyncIterator[dict]:
        """События прогона начиная с номера after+1; ждёт новые, пока прогон идёт."""
        sent = after
        while True:
            async with run.changed:
                if len(run.events) <= sent and not run.closed:
                    await run.changed.wait()
                pending = run.events[sent:]
                closed = run.closed
            for event in pending:
                sent = event["seq"]
                yield event
            if closed and sent >= len(run.events):
                return

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for run_id, run in list(self.runs.items()):
                if run.closed and run.finished_at and now - run.finished_at > KEEP_FINISHED_S:
                    self.runs.pop(run_id, None)

    async def shutdown(self) -> None:
        if self._janitor:
            self._janitor.cancel()
        for run in self.runs.values():
            if run.task and not run.task.done():
                run.abort.set()
                run.task.cancel()

    def stats(self) -> dict:
        running = sum(1 for r in self.runs.values() if r.status == "running")
        return {"running": running, "queued": len(self._waiting), "parallel": self.settings.max_parallel_runs}


def _humanize(exc: Exception) -> str:
    """Ошибка модели или сети — человеческими словами, без трейсбэка."""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if any(s in lowered for s in ("connection", "connect", "refused", "unreachable")):
        return "модель недоступна — проверьте, что LM Studio запущен и сервер включён"
    if "rate" in lowered and "limit" in lowered:
        return "сервис модели ограничил частоту запросов, повторите через несколько секунд"
    if "context" in lowered and ("length" in lowered or "window" in lowered or "exceed" in lowered):
        return "разговор не поместился в окно модели — начните новый чат"
    return f"не удалось получить ответ: {text[:300]}"
