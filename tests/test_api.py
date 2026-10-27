"""HTTP API целиком, но без модели: вход, изоляция пользователей, CSRF, вопрос → поток SSE → история."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from fakes import FakeRunner
from rostov_guide.config import Settings
from rostov_guide.search.store import IndexStatus
from rostov_guide.web import auth
from rostov_guide.web.app import create_app
from rostov_guide.web.runs import RunManager

BASE = "http://testserver"


@pytest.fixture
async def app(tmp_path, repo):
    settings = Settings(data_dir=tmp_path, runs_dir=tmp_path / "runs", secret_key="test-secret")
    users = auth.UserStore(tmp_path / "app.sqlite")
    await users.init()
    runner = FakeRunner(SqliteSessionService(str(tmp_path / "sessions.sqlite")))
    app = create_app()
    app.state.settings = settings
    app.state.services = SimpleNamespace(places=repo, index=SimpleNamespace(status=IndexStatus(state="ready")))
    app.state.users = users
    app.state.jwt_secret = settings.jwt_secret()
    app.state.login_limiter = auth.LoginLimiter()
    app.state.runner = runner
    app.state.runs = RunManager(runner, repo, settings)
    yield app
    await app.state.runs.shutdown()


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _signup(client, name="anna", password="secret1"):
    resp = await client.post("/api/auth/register", json={"username": name, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


async def test_register_login_me(app):
    async with _client(app) as client:
        resp = await _signup(client)
        cookie = resp.headers["set-cookie"]
        assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
        assert (await client.get("/api/auth/me")).json()["username"] == "anna"
        assert (await client.post("/api/auth/register", json={"username": "ANNA", "password": "x" * 8})).status_code == 409
    async with _client(app) as anon:
        assert (await anon.get("/api/chats")).status_code == 401
        assert (await anon.post("/api/auth/login", json={"username": "anna", "password": "wrong!"})).status_code == 401
        assert (await anon.post("/api/auth/login", json={"username": "anna", "password": "secret1"})).status_code == 200


async def test_weak_credentials_rejected(app):
    async with _client(app) as client:
        assert (await client.post("/api/auth/register", json={"username": "a", "password": "secret1"})).status_code == 422
        assert (await client.post("/api/auth/register", json={"username": "bob", "password": "123"})).status_code == 422


async def test_foreign_origin_is_blocked(app):
    async with _client(app) as client:
        resp = await client.post("/api/auth/register", json={"username": "eve", "password": "secret1"},
                                 headers={"Origin": "https://evil.example"})
        assert resp.status_code == 403


async def _ask_and_stream(client, chat_id, text, **extra):
    resp = await client.post(f"/api/chats/{chat_id}/messages", json={"text": text, **extra})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    stream = await asyncio.wait_for(client.get(f"/api/runs/{run_id}/events"), 10)
    events = [json.loads(line[5:]) for line in stream.text.splitlines() if line.startswith("data:")]
    return resp.json(), run_id, events


async def test_question_streams_and_lands_in_history(app):
    async with _client(app) as client:
        await _signup(client)
        chat = (await client.post("/api/chats", json={})).json()
        meta, run_id, events = await _ask_and_stream(client, chat["id"], "Сколько аптек на Белорусской?")
        assert meta["title"] == "Сколько аптек на Белорусской?"
        assert events[-1]["type"] == "done" and events[-1]["status"] == "done"
        assert any(e["type"] == "tool_result" and e["places"][0]["lat"] for e in events)

        # всё доставлено — повторная подписка не крутит EventSource по кругу
        again = await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(events[-1]["seq"])})
        assert again.status_code == 204

        data = (await client.get(f"/api/chats/{chat['id']}")).json()
        assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
        assert "Аптека Ригла" in data["messages"][1]["text"]
        assert data["context_tokens"] == 1000 and data["active_run"] is None
        chats = (await client.get("/api/chats")).json()
        assert chats[0]["title"] == "Сколько аптек на Белорусской?"


async def test_users_cannot_see_each_other(app):
    async with _client(app) as anna, _client(app) as boris:
        await _signup(anna, "anna")
        await _signup(boris, "boris")
        chat = (await anna.post("/api/chats", json={})).json()
        _, run_id, _ = await _ask_and_stream(anna, chat["id"], "Привет")
        assert (await boris.get(f"/api/chats/{chat['id']}")).status_code == 404
        assert (await boris.delete(f"/api/chats/{chat['id']}")).status_code == 404
        assert (await boris.post(f"/api/chats/{chat['id']}/messages", json={"text": "x"})).status_code == 404
        assert (await boris.get(f"/api/runs/{run_id}/events")).status_code == 404
        assert (await boris.get("/api/chats")).json() == []


async def test_location_must_be_in_rostov(app):
    async with _client(app) as client:
        await _signup(client)
        chat = (await client.post("/api/chats", json={})).json()
        far = {"text": "рядом", "location": {"lat": 55.75, "lon": 37.62}}
        assert (await client.post(f"/api/chats/{chat['id']}/messages", json=far)).status_code == 422
        _, _, events = await _ask_and_stream(client, chat["id"], "рядом",
                                             location={"lat": 47.2275, "lon": 39.7210, "label": "Пушкинская"})
        assert events[-1]["status"] == "done"
        data = (await client.get(f"/api/chats/{chat['id']}")).json()
        assert data["location"]["label"] == "Пушкинская"     # точка уехала в состояние сессии ADK
        await _ask_and_stream(client, chat["id"], "а теперь без точки")
        data = (await client.get(f"/api/chats/{chat['id']}")).json()
        assert data["location"] is None                       # снятая точка не остаётся в сессии


async def test_delete_chat(app):
    async with _client(app) as client:
        await _signup(client)
        chat = (await client.post("/api/chats", json={})).json()
        assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
        assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 404


async def test_reverse_geocode_label(app):
    async with _client(app) as client:
        await _signup(client)
        resp = await client.get("/api/reverse", params={"lat": 47.27917, "lon": 39.74933})
        assert resp.json()["label"] == "Белорусская улица, 44"


async def test_queued_question_survives_reload(app):
    """Вопрос ждёт очереди и в сессию ADK ещё не попал — после перезагрузки страницы его не теряем."""
    gate = asyncio.Event()
    app.state.runner.gate = gate
    app.state.runs = RunManager(app.state.runner, app.state.services.places,
                                Settings(data_dir=app.state.settings.data_dir, max_parallel_runs=1))
    async with _client(app) as anna, _client(app) as boris:
        await _signup(anna, "anna")
        await _signup(boris, "boris")
        busy_chat = (await boris.post("/api/chats", json={})).json()
        await boris.post(f"/api/chats/{busy_chat['id']}/messages", json={"text": "занимаю модель"})
        await asyncio.sleep(0.05)

        chat = (await anna.post("/api/chats", json={})).json()
        await anna.post(f"/api/chats/{chat['id']}/messages", json={"text": "Где поесть плов?"})
        data = (await anna.get(f"/api/chats/{chat['id']}")).json()
        assert data["active_run"] and data["active_question"] == "Где поесть плов?"
        assert data["title"] == "Где поесть плов?" and data["messages"] == []
        assert (await anna.get("/api/chats")).json()[0]["busy"] is True
        gate.set()
