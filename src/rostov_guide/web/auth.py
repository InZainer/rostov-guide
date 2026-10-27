"""Пользователи и вход.

- Пароли — scrypt из стандартной библиотеки с солью; хеширование нарочно
  медленное (~50 мс), поэтому идёт в пуле потоков и не стопорит цикл событий.
- Сессия — подписанный JWT в cookie `HttpOnly; SameSite=Lax`: JavaScript её
  не читает, а с чужого сайта она не уходит в POST-запросах.
- Изоляция данных: `user_id` из токена — это `user_id` сессий ADK, поэтому
  чужой чат невозможно даже найти, а не просто «запрещено открыть».
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = "rg_session"
_USERNAME = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]{3,32}$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


@dataclass(frozen=True)
class User:
    id: str
    username: str


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


async def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = await asyncio.to_thread(_hash, password, salt)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


async def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    digest = await asyncio.to_thread(_hash, password, base64.b64decode(salt_b64))
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL)""")
            await db.commit()

    async def create(self, username: str, password: str) -> User | None:
        user = User(str(uuid.uuid4()), username)
        password_hash = await hash_password(password)
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                                 (user.id, username, password_hash, time.time()))
                await db.commit()
            except aiosqlite.IntegrityError:
                return None
        return user

    async def authenticate(self, username: str, password: str) -> User | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT id, username, password_hash FROM users WHERE username = ?",
                                          (username,))).fetchone()
        if row is None:
            await hash_password(password)       # одинаковое время ответа для «нет такого» и «не тот пароль»
            return None
        return User(row[0], row[1]) if await verify_password(password, row[2]) else None


class LoginLimiter:
    """Не больше N попыток входа с одного адреса за окно: перебор паролей упирается в стену."""

    def __init__(self, attempts: int = 10, window_s: float = 300) -> None:
        self.attempts, self.window_s = attempts, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_s:
            hits.popleft()
        if len(hits) >= self.attempts:
            raise HTTPException(429, "Слишком много попыток входа. Подождите несколько минут.")
        hits.append(now)


def issue_token(user: User, secret: str, days: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user.id, "name": user.username, "iat": now, "exp": now + days * 86400},
                      secret, algorithm="HS256")


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Нужно войти")
    try:
        data = jwt.decode(token, request.app.state.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Сессия истекла, войдите снова") from None
    return User(data["sub"], data["name"])


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(request: Request, response: Response, user: User) -> None:
    settings = request.app.state.settings
    response.set_cookie(COOKIE, issue_token(user, request.app.state.jwt_secret, settings.session_days),
                        max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, path="/")


@router.post("/register")
async def register(body: Credentials, request: Request, response: Response) -> dict:
    username = body.username.strip()
    if not _USERNAME.match(username):
        raise HTTPException(422, "Имя: 3–32 символа, буквы, цифры, точка, дефис или подчёркивание")
    if len(body.password) < 6:
        raise HTTPException(422, "Пароль — не короче 6 символов")
    request.app.state.login_limiter.check(f"ip:{request.client.host if request.client else '?'}")
    user = await request.app.state.users.create(username, body.password)
    if user is None:
        raise HTTPException(409, "Такое имя уже занято")
    _set_cookie(request, response, user)
    return {"id": user.id, "username": user.username}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response) -> dict:
    request.app.state.login_limiter.check(f"ip:{request.client.host if request.client else '?'}")
    user = await request.app.state.users.authenticate(body.username.strip(), body.password)
    if user is None:
        raise HTTPException(401, "Неверное имя или пароль")
    _set_cookie(request, response, user)
    return {"id": user.id, "username": user.username}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username}
