"""Плагины ADK: защита от зацикливания и вопрос пользователя после сжатия контекста."""

from types import SimpleNamespace

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from rostov_guide.agent.plugins import RepeatGuard, UserTurnGuard


def _tool_ctx(invocation="inv1"):
    return SimpleNamespace(invocation_id=invocation)


async def test_repeat_guard_blocks_identical_call_only():
    guard = RepeatGuard()
    tool = SimpleNamespace(name="nearest_places")
    args = {"kind": "pharmacy", "address": "Белорусская 44"}
    assert await guard.before_tool_callback(tool=tool, tool_args=args, tool_context=_tool_ctx()) is None
    repeated = await guard.before_tool_callback(tool=tool, tool_args=dict(args), tool_context=_tool_ctx())
    assert "error" in repeated and "уже вызывался" in repeated["error"]
    changed = {**args, "only_24_7": True}
    assert await guard.before_tool_callback(tool=tool, tool_args=changed, tool_context=_tool_ctx()) is None
    # в другом прогоне тот же вызов — снова законный
    assert await guard.before_tool_callback(tool=tool, tool_args=args, tool_context=_tool_ctx("inv2")) is None


async def test_repeat_guard_forgets_finished_runs():
    guard = RepeatGuard()
    tool = SimpleNamespace(name="find_places")
    await guard.before_tool_callback(tool=tool, tool_args={"kind": "cafe"}, tool_context=_tool_ctx())
    await guard.after_run_callback(invocation_context=SimpleNamespace(invocation_id="inv1"))
    assert guard._seen == {}


def _content(role, **part):
    return types.Content(role=role, parts=[types.Part(**part)])


async def test_question_is_restored_after_compaction():
    question = _content("user", text="А какая из них ближе к Белорусской, 44?")
    request = LlmRequest(contents=[
        _content("model", text="Сводка: пользователь спрашивал про круглосуточные аптеки…"),
        _content("model", function_call=types.FunctionCall(name="nearest_places", args={"kind": "pharmacy"})),
        _content("user", function_response=types.FunctionResponse(name="nearest_places", response={"places": []})),
    ])
    await UserTurnGuard().before_model_callback(callback_context=SimpleNamespace(user_content=question),
                                                llm_request=request)
    assert request.contents[0] is question and len(request.contents) == 4


async def test_request_with_question_is_left_alone():
    question = _content("user", text="Сколько аптек?")
    request = LlmRequest(contents=[question, _content("model", text="330")])
    await UserTurnGuard().before_model_callback(callback_context=SimpleNamespace(user_content=question),
                                                llm_request=request)
    assert len(request.contents) == 2
