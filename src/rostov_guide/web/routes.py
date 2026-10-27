"""HTTP API: чаты, вопросы, поток событий агента, места, состояние сервиса."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..agent.factory import APP_NAME
from ..agent.tools import USER_LOCATION_KEY
from ..places.hours import ROSTOV_TZ
from .auth import User, current_user
from .events import history
from .runs import Busy

router = APIRouter(prefix="/api")

NEW_CHAT_TITLE = "Новый чат"


class Location(BaseModel):
    lat: float = Field(ge=47.0, le=47.5)        # рамка Ростова: точку за городом не принимаем
    lon: float = Field(ge=39.3, le=40.1)
    label: str | None = Field(default=None, max_length=120)


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    location: Location | None = None


def _title(state, active) -> str:
    """Заголовок чата; пока первый вопрос стоит в очереди, он ещё не в сессии — берём из прогона."""
    title = state.get("title") or NEW_CHAT_TITLE
    if title == NEW_CHAT_TITLE and active is not None and active.title:
        return active.title
    return title


def _sessions(request: Request):
    return request.app.state.runner.session_service


async def _own_session(request: Request, user: User, chat_id: str):
    session = await _sessions(request).get_session(app_name=APP_NAME, user_id=user.id, session_id=chat_id)
    if session is None:
        raise HTTPException(404, "Чат не найден")
    return session


# ------------------------------------------------------------------------- чаты

@router.get("/chats")
async def list_chats(request: Request, user: User = Depends(current_user)) -> list[dict]:
    listed = await _sessions(request).list_sessions(app_name=APP_NAME, user_id=user.id)
    manager = request.app.state.runs
    chats = []
    for s in listed.sessions:
        active = manager.active_for_chat(s.id)
        chats.append({"id": s.id, "title": _title(s.state, active), "updated_at": s.last_update_time,
                      "busy": active is not None})
    return sorted(chats, key=lambda c: c["updated_at"], reverse=True)


@router.post("/chats", status_code=201)
async def create_chat(request: Request, user: User = Depends(current_user)) -> dict:
    session = await _sessions(request).create_session(app_name=APP_NAME, user_id=user.id,
                                                      state={"title": NEW_CHAT_TITLE})
    return {"id": session.id, "title": NEW_CHAT_TITLE, "updated_at": session.last_update_time}


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    session = await _own_session(request, user, chat_id)
    active = request.app.state.runs.active_for_chat(chat_id)
    messages = history(session.events, request.app.state.services.places)
    context = next((m["usage"]["context_tokens"] for m in reversed(messages)
                    if m["role"] == "assistant" and m["usage"]["context_tokens"]), 0)
    return {"id": session.id, "title": _title(session.state, active),
            "messages": messages, "active_run": active.id if active else None,
            "active_question": active.question if active else None,
            "location": session.state.get(USER_LOCATION_KEY), "context_tokens": context}


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, request: Request, user: User = Depends(current_user)) -> Response:
    await _own_session(request, user, chat_id)
    active = request.app.state.runs.active_for_chat(chat_id)
    if active:
        request.app.state.runs.cancel(active)
    await _sessions(request).delete_session(app_name=APP_NAME, user_id=user.id, session_id=chat_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------- вопросы и поток

@router.post("/chats/{chat_id}/messages", status_code=202)
async def ask(chat_id: str, body: Ask, request: Request, user: User = Depends(current_user)) -> dict:
    session = await _own_session(request, user, chat_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Пустой вопрос")
    # Контекст хода, который нужен агенту, едет в состояние сессии ADK вместе с вопросом:
    # точка на карте (её читают инструменты) и название чата по первому вопросу.
    # точку передаём всегда, в том числе «нет точки»: иначе снятая на карте точка
    # осталась бы в состоянии сессии, и агент считал бы, что знает, где пользователь
    delta: dict = {USER_LOCATION_KEY: body.location.model_dump() if body.location else None}
    if (session.state.get("title") or NEW_CHAT_TITLE) == NEW_CHAT_TITLE:
        delta["title"] = text if len(text) <= 60 else text[:57].rstrip() + "…"
    try:
        run = request.app.state.runs.start(user_id=user.id, chat_id=chat_id, text=text, state_delta=delta)
    except Busy as exc:
        raise HTTPException(409, str(exc)) from None
    return {"run_id": run.id, "title": delta.get("title")}


def _own_run(request: Request, user: User, run_id: str):
    run = request.app.state.runs.runs.get(run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(404, "Прогон не найден или уже завершён")
    return run


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, user: User = Depends(current_user),
                     after: int = Query(0, ge=0)):
    run = _own_run(request, user, run_id)
    last = request.headers.get("last-event-id")
    start = int(last) if last and last.isdigit() else after
    if run.closed and start >= len(run.events):
        return Response(status_code=204)        # всё уже доставлено: EventSource перестанет переподключаться

    async def stream():
        async for event in request.app.state.runs.subscribe(run, start):
            if await request.is_disconnected():
                return
            yield {"id": str(event["seq"]), "data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(stream(), ping=15, headers={"X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    run = _own_run(request, user, run_id)
    request.app.state.runs.cancel(run)
    return {"ok": True}


# ------------------------------------------------------------------------- места и состояние

@router.get("/places/{place_id}")
async def place(place_id: int, request: Request, user: User = Depends(current_user)) -> dict:
    found = request.app.state.services.places.get(place_id)
    if found is None:
        raise HTTPException(404, "Места нет")
    return found.card(datetime.now(ROSTOV_TZ))


@router.get("/reverse")
async def reverse(request: Request, lat: float = Query(ge=47.0, le=47.5), lon: float = Query(ge=39.3, le=40.1),
                  user: User = Depends(current_user)) -> dict:
    """Подпись для точки на карте: ближайший к ней адрес из справочника."""
    places = request.app.state.services.places
    with_address = [p for p in places.places.values() if p.address]
    nearest = places.by_distance(with_address, lat, lon)[:1]
    if not nearest or nearest[0][1] > 600:
        return {"label": f"{lat:.4f}, {lon:.4f}", "lat": lat, "lon": lon}
    place, dist = nearest[0]
    return {"label": ("" if dist < 60 else "≈ ") + place.address, "lat": lat, "lon": lon}


_llm_probe: dict = {"at": 0.0, "ok": None, "loaded": None, "context": None}


async def _llm_probe_state(request: Request) -> dict:
    """Отвечает ли сервер модели и загружена ли она. Кешируется на 10 с, чтобы не долбить LM Studio.

    LM Studio выгружает простаивающие модели, а при первом запросе поднимает их заново —
    с окном по умолчанию, которое может оказаться меньше нужного. Его собственный REST
    (`/api/v0/models/<id>`) говорит, загружена ли модель и с каким окном; у облачных
    провайдеров этого адреса нет, и тогда достаточно того, что `/models` отвечает.
    """
    if time.monotonic() - _llm_probe["at"] < 10:
        return _llm_probe
    settings = request.app.state.settings
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    ok, loaded, context = False, None, None
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(settings.llm_base_url.rstrip("/") + "/models", headers=headers)
            ok = resp.status_code == 200
            if ok:
                root = settings.llm_base_url.rstrip("/").removesuffix("/v1")
                info = await client.get(f"{root}/api/v0/models/{settings.llm_model}", headers=headers)
                if info.status_code == 200 and "state" in info.json():
                    data = info.json()
                    loaded = data["state"] == "loaded"
                    context = data.get("loaded_context_length")
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
        pass
    _llm_probe.update(at=time.monotonic(), ok=ok, loaded=loaded, context=context)
    return _llm_probe


@router.get("/status")
async def status(request: Request) -> dict:
    settings = request.app.state.settings
    services = request.app.state.services
    llm = await _llm_probe_state(request)
    return {
        "model": settings.llm_model, "embed_model": settings.embed_model,
        "llm_ok": llm["ok"], "llm_loaded": llm["loaded"], "llm_context": llm["context"],
        "index": services.index.status.as_dict(),
        "places": len(services.places), "queue": request.app.state.runs.stats(),
        "context_window": settings.context_window, "max_llm_calls": settings.max_llm_calls,
        "price_in_rub": settings.llm_price_in_rub, "price_out_rub": settings.llm_price_out_rub,
    }
