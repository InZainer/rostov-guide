"""Перевод событий ADK в события интерфейса.

Один и тот же перевод работает в двух местах: в живом потоке SSE (по мере
того как агент думает) и при загрузке истории чата из сессии ADK. Поэтому
фронтенд рисует ход рассуждения одинаково — и сейчас, и после перезагрузки.

События интерфейса:
    thought_delta / thought   — рассуждение модели (кусок / целиком за шаг)
    tool_call                 — агент зовёт инструмент: имя, аргументы, подпись по-русски
    tool_result               — что вернулось: кратко, плюс карточки мест для карты
    answer_delta / answer     — ответ пользователю (кусок / целиком)
    usage                     — токены шага модели
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from google.adk.events import Event

from ..places.hours import ROSTOV_TZ
from ..places.kinds import kind_label
from ..places.repository import PlaceRepository

MAX_CARDS = 10


def _q(text: Any) -> str:
    return f"«{text}»"


def tool_title(name: str, args: dict, places: PlaceRepository) -> str:
    """Подпись шага для человека: что именно агент сейчас делает."""
    kind = args.get("kind")
    what = kind_label(kind) if kind else None
    if name == "search_places":
        return f"Ищу по смыслу: {_q(args.get('query', ''))}" + (f" среди мест «{what}»" if what else "")
    if name == "find_places":
        bits = [what or "места"]
        if args.get("street"):
            bits.append(f"улица {args['street']}")
        if args.get("name"):
            bits.append(f"название {_q(args['name'])}")
        if args.get("only_24_7"):
            bits.append("круглосуточно")
        if args.get("open_now"):
            bits.append("открыто сейчас")
        return "Фильтрую справочник: " + " · ".join(bits)
    if name == "nearest_places":
        target = what or _q(args.get("query", "")) or "места"
        if args.get("address"):
            anchor = f"от {_q(args['address'])}"
        elif args.get("place_id"):
            place = places.get(int(args["place_id"]))
            anchor = f"от {_q(place.title)}" if place else "от выбранного места"
        else:
            anchor = "от точки на карте"
        return f"Ищу ближайшее: {target} — {anchor}"
    if name == "place_details":
        place = places.get(int(args.get("place_id") or 0))
        return f"Открываю карточку {_q(place.title)}" if place else "Открываю карточку места"
    return f"Вызываю {name}"


def result_view(name: str, response: dict, places: PlaceRepository,
                now: datetime | None = None) -> dict:
    """Сводка результата инструмента и карточки мест (с координатами) для карты."""
    now = now or datetime.now(ROSTOV_TZ)
    if not isinstance(response, dict):
        return {"ok": True, "summary": str(response)[:200], "places": []}
    if "error" in response:
        hint = response.get("hint")
        return {"ok": False, "summary": response["error"] + (f" — {hint}" if hint else ""), "places": []}

    ids: list[tuple[int, int | None, str | None]] = []
    if name == "place_details" and "id" in response:
        ids.append((int(response["id"]), None, None))
    for item in response.get("places", []) or []:
        if isinstance(item, dict) and "id" in item:
            ids.append((int(item["id"]), item.get("distance_m"), item.get("found_by")))
    cards = []
    for pid, dist, found_by in ids[:MAX_CARDS]:
        place = places.get(pid)
        if place:
            card = place.card(now, dist)
            if found_by:
                card["found_by"] = found_by
            cards.append(card)

    view: dict = {"ok": True, "places": cards}
    if "origin" in response:
        view["origin"] = response["origin"]
    if name == "find_places":
        total = response.get("total", 0)
        view["summary"] = f"подходит мест: {total}" + (f", показываю {len(cards)}" if total > len(cards) else "")
    elif name == "nearest_places" and cards:
        first = cards[0]
        view["summary"] = f"ближайшее — {first['name']}, {first['distance_m']} м"
    elif name == "place_details" and cards:
        view["summary"] = f"{cards[0]['name']}: {cards[0]['hours_label']}"
    elif cards:
        names = ", ".join(c["name"] for c in cards[:3])
        view["summary"] = f"нашлось {len(cards)}: {names}" + ("…" if len(cards) > 3 else "")
    else:
        view["summary"] = response.get("note") or "ничего не нашлось"
    return view


def translate(event: Event, places: PlaceRepository) -> Iterator[dict]:
    """Одно событие ADK → ноль или несколько событий интерфейса."""
    parts = event.content.parts if event.content and event.content.parts else []
    if event.partial:
        for part in parts:
            if part.text and part.thought:
                yield {"type": "thought_delta", "text": part.text}
            elif part.text:
                yield {"type": "answer_delta", "text": part.text}
        return

    if event.actions and event.actions.compaction:
        summary = " ".join(p.text or "" for p in (event.actions.compaction.compacted_content.parts or []))
        yield {"type": "compaction", "text": summary.strip()}
        return

    for part in parts:
        if part.thought and part.text:
            yield {"type": "thought", "text": part.text}
        elif part.text and event.author != "user":
            yield {"type": "answer", "text": part.text}
        if part.function_call:
            args = dict(part.function_call.args or {})
            yield {"type": "tool_call", "id": part.function_call.id, "name": part.function_call.name,
                   "args": args, "title": tool_title(part.function_call.name, args, places)}
        if part.function_response:
            resp = part.function_response.response or {}
            yield {"type": "tool_result", "id": part.function_response.id, "name": part.function_response.name,
                   **result_view(part.function_response.name, resp, places)}
    usage = event.usage_metadata
    if usage and usage.prompt_token_count:
        yield {"type": "usage", "input_tokens": usage.prompt_token_count or 0,
               "output_tokens": (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)}


def history(events: list[Event], places: PlaceRepository) -> list[dict]:
    """События сессии → сообщения чата: вопрос, затем ответ со всеми шагами."""
    messages: list[dict] = []
    current: dict | None = None
    for event in events:
        if event.author == "user":
            text = " ".join(p.text or "" for p in (event.content.parts if event.content and event.content.parts else [])
                            if p.text).strip()
            if text:
                messages.append({"role": "user", "text": text, "ts": event.timestamp})
                current = None
            continue
        if event.actions and event.actions.compaction:
            messages.append({"role": "system", "kind": "compaction", "ts": event.timestamp,
                             "text": " ".join(p.text or "" for p in
                                              (event.actions.compaction.compacted_content.parts or [])).strip()})
            continue
        if current is None or current.get("invocation") != event.invocation_id:
            current = {"role": "assistant", "invocation": event.invocation_id, "ts": event.timestamp,
                       "events": [], "text": "", "usage": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0,
                                                            "context_tokens": 0}}
            messages.append(current)
        for ui in translate(event, places):
            if ui["type"] == "answer":
                current["text"] = (current["text"] + "\n\n" + ui["text"]).strip()
            elif ui["type"] == "usage":
                current["usage"]["input_tokens"] += ui["input_tokens"]
                current["usage"]["output_tokens"] += ui["output_tokens"]
                current["usage"]["llm_calls"] += 1
                current["usage"]["context_tokens"] = ui["input_tokens"]
            else:
                current["events"].append(ui)
        if event.error_message:
            current["error"] = event.error_message
    return messages
