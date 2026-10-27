"""Заглушка ADK Runner: тот же интерфейс run_async, сценарий событий вместо модели."""

from __future__ import annotations

import asyncio
import uuid

from google.adk.events import Event, EventActions
from google.genai import types

from rostov_guide.agent.factory import APP_NAME


def scripted_answer(question: str, invocation: str) -> list[Event]:
    """Типичный ход: рассуждение кусками → вызов инструмента → результат → ответ."""
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=1000, candidates_token_count=50)
    model = lambda parts, partial=None, usage=None: Event(  # noqa: E731
        author="rostov_guide", invocation_id=invocation, partial=partial, usage_metadata=usage,
        content=types.Content(role="model", parts=parts))
    return [
        model([types.Part(text="Нужно найти ", thought=True)], partial=True),
        model([types.Part(text="аптеки.", thought=True)], partial=True),
        model([types.Part(text="Нужно найти аптеки.", thought=True),
               types.Part(function_call=types.FunctionCall(id="c1", name="find_places", args={"kind": "pharmacy"}))],
              usage=usage),
        model([types.Part(function_response=types.FunctionResponse(
            id="c1", name="find_places", response={"total": 3, "places": [{"id": 4}, {"id": 5}]}))]),
        model([types.Part(text="Аптек ")], partial=True),
        model([types.Part(text="Аптек три: **Аптека Ригла** и **Лаки Фарма**.")], usage=usage),
    ]


class FakeRunner:
    def __init__(self, session_service=None, *, gate: asyncio.Event | None = None, error: Exception | None = None):
        self.session_service = session_service
        self.gate = gate
        self.error = error
        self.calls = 0

    async def run_async(self, *, user_id, session_id, new_message, state_delta=None, run_config=None,
                        abort_signal=None, **_):
        self.calls += 1
        invocation = "e-" + uuid.uuid4().hex[:8]
        session = None
        if self.session_service:
            session = await self.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
            await self.session_service.append_event(session, Event(
                author="user", invocation_id=invocation, content=new_message,
                actions=EventActions(state_delta=state_delta or {})))
        if self.error:
            raise self.error
        for event in scripted_answer(new_message.parts[0].text, invocation):
            if self.gate:
                await self.gate.wait()
            if abort_signal and abort_signal.is_set():
                return
            if session and not event.partial:
                await self.session_service.append_event(session, event)
            yield event
            await asyncio.sleep(0)
