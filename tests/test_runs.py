"""Менеджер прогонов: очередь к модели, переподключение, отмена, ошибки по-человечески."""

import asyncio

import pytest
from google.adk.agents.invocation_context import LlmCallsLimitExceededError

from fakes import FakeRunner
from rostov_guide.config import Settings
from rostov_guide.web.runs import Busy, RunManager


def _settings(**kw) -> Settings:
    return Settings(max_parallel_runs=kw.pop("parallel", 2), max_queue=kw.pop("queue", 20), run_timeout_s=5, **kw)


async def _collect(manager, run, after=0):
    return [e async for e in manager.subscribe(run, after)]


async def test_events_stream_ends_with_totals(repo):
    manager = RunManager(FakeRunner(), repo, _settings())
    run = manager.start(user_id="u1", chat_id="c1", text="Сколько аптек?")
    events = await asyncio.wait_for(_collect(manager, run), 5)
    types_ = [e["type"] for e in events]
    assert types_[:2] == ["queued", "started"]
    assert types_.count("thought_delta") == 2 and "tool_call" in types_ and "tool_result" in types_
    done = events[-1]
    assert done["type"] == "done" and done["status"] == "done"
    assert done["llm_calls"] == 2 and done["input_tokens"] == 2000
    assert done["mentioned"] == [4, 5]                 # места, названные в ответе
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


async def test_reconnect_replays_only_missed_events(repo):
    manager = RunManager(FakeRunner(), repo, _settings())
    run = manager.start(user_id="u1", chat_id="c1", text="?")
    first = await asyncio.wait_for(_collect(manager, run), 5)
    tail = await asyncio.wait_for(_collect(manager, run, after=5), 5)
    assert tail == first[5:]


async def test_queue_positions_when_model_is_busy(repo):
    gate = asyncio.Event()
    manager = RunManager(FakeRunner(gate=gate), repo, _settings(parallel=1))
    a = manager.start(user_id="u1", chat_id="c1", text="a")
    b = manager.start(user_id="u2", chat_id="c2", text="b")
    await asyncio.sleep(0.05)
    assert a.status == "running" and b.status == "queued"
    assert {"type": "queued", "position": 1} in [{k: v for k, v in e.items() if k != "seq"} for e in b.events]
    assert manager.stats() == {"running": 1, "queued": 1, "parallel": 1}
    gate.set()
    await asyncio.wait_for(asyncio.gather(_collect(manager, a), _collect(manager, b)), 5)
    assert a.status == b.status == "done"


async def test_one_run_per_chat_and_two_per_user(repo):
    gate = asyncio.Event()
    manager = RunManager(FakeRunner(gate=gate), repo, _settings())
    manager.start(user_id="u1", chat_id="c1", text="a")
    with pytest.raises(Busy):
        manager.start(user_id="u1", chat_id="c1", text="again")
    manager.start(user_id="u1", chat_id="c2", text="b")
    with pytest.raises(Busy):
        manager.start(user_id="u1", chat_id="c3", text="c")
    manager.start(user_id="u2", chat_id="c4", text="другой пользователь не ждёт u1")
    gate.set()


async def test_queue_overflow_is_refused(repo):
    gate = asyncio.Event()
    manager = RunManager(FakeRunner(gate=gate), repo, _settings(parallel=1, queue=1))
    manager.start(user_id="u1", chat_id="c1", text="a")
    await asyncio.sleep(0.01)
    manager.start(user_id="u2", chat_id="c2", text="b")
    await asyncio.sleep(0.01)
    with pytest.raises(Busy):
        manager.start(user_id="u3", chat_id="c3", text="c")
    gate.set()


async def test_cancel_stops_the_run(repo):
    gate = asyncio.Event()
    manager = RunManager(FakeRunner(gate=gate), repo, _settings())
    run = manager.start(user_id="u1", chat_id="c1", text="a")
    await asyncio.sleep(0.01)
    manager.cancel(run)
    gate.set()
    events = await asyncio.wait_for(_collect(manager, run), 5)
    assert run.status == "cancelled" and events[-1]["status"] == "cancelled"


@pytest.mark.parametrize("error, text", [
    (ConnectionError("Connection refused"), "LM Studio"),
    (LlmCallsLimitExceededError("limit"), "слишком много шагов"),
])
async def test_errors_reach_the_user_in_plain_words(repo, error, text):
    manager = RunManager(FakeRunner(error=error), repo, _settings())
    run = manager.start(user_id="u1", chat_id="c1", text="a")
    events = await asyncio.wait_for(_collect(manager, run), 5)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and text in errors[0]["message"]
    assert events[-1]["type"] == "done" and events[-1]["status"] == "error"
