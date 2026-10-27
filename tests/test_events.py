"""Перевод событий ADK в события интерфейса — одинаково для потока и для истории."""

from google.adk.events import Event
from google.genai import types

from rostov_guide.web.events import history, translate


def _event(author="rostov_guide", parts=(), partial=None, invocation="inv1", usage=None) -> Event:
    return Event(author=author, invocation_id=invocation, partial=partial,
                 content=types.Content(role="model" if author != "user" else "user", parts=list(parts)),
                 usage_metadata=usage)


def test_partial_thought_and_answer_become_deltas(repo):
    ev = _event(parts=[types.Part(text="думаю", thought=True)], partial=True)
    assert list(translate(ev, repo)) == [{"type": "thought_delta", "text": "думаю"}]
    ev = _event(parts=[types.Part(text="Отв")], partial=True)
    assert list(translate(ev, repo)) == [{"type": "answer_delta", "text": "Отв"}]


def test_tool_call_gets_human_title(repo):
    call = types.Part(function_call=types.FunctionCall(id="c1", name="nearest_places",
                                                       args={"kind": "pharmacy", "address": "Белорусская 44"}))
    out = list(translate(_event(parts=[call]), repo))
    assert out[0]["type"] == "tool_call"
    assert out[0]["title"] == "Ищу ближайшее: аптека — от «Белорусская 44»"


def test_tool_result_carries_cards_with_coordinates(repo):
    resp = types.Part(function_response=types.FunctionResponse(
        id="c1", name="nearest_places",
        response={"origin": {"label": "x", "lat": 47.2, "lon": 39.7},
                  "places": [{"id": 5, "distance_m": 0}, {"id": 4, "distance_m": 120}]}))
    out = list(translate(_event(parts=[resp]), repo))[0]
    assert out["ok"] and out["summary"] == "ближайшее — Лаки Фарма, 0 м"
    assert out["places"][1]["lat"] == 47.279 and out["places"][1]["distance_m"] == 120
    assert out["origin"]["label"] == "x"


def test_tool_error_is_shown_as_failed_step(repo):
    resp = types.Part(function_response=types.FunctionResponse(
        id="c1", name="find_places", response={"error": "вид не найден", "hint": "pharmacy"}))
    out = list(translate(_event(parts=[resp]), repo))[0]
    assert out["ok"] is False and out["summary"] == "вид не найден — pharmacy"


def test_usage_is_reported(repo):
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=1200, candidates_token_count=40,
                                                       thoughts_token_count=60)
    out = list(translate(_event(parts=[types.Part(text="ok")], usage=usage), repo))
    assert {"type": "usage", "input_tokens": 1200, "output_tokens": 100} in out


def test_history_groups_steps_under_question(repo):
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=900, candidates_token_count=10)
    events = [
        _event(author="user", parts=[types.Part(text="Сколько аптек?")]),
        _event(parts=[types.Part(function_call=types.FunctionCall(id="c1", name="find_places",
                                                                  args={"kind": "pharmacy"}))], usage=usage),
        _event(parts=[types.Part(function_response=types.FunctionResponse(
            id="c1", name="find_places", response={"total": 3, "places": [{"id": 4}]}))]),
        _event(parts=[types.Part(text="Три аптеки.")], usage=usage),
    ]
    messages = history(events, repo)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    answer = messages[1]
    assert answer["text"] == "Три аптеки."
    assert [e["type"] for e in answer["events"]] == ["tool_call", "tool_result"]
    assert answer["usage"]["llm_calls"] == 2 and answer["usage"]["context_tokens"] == 900
