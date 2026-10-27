"""Настройки приложения: всё берётся из окружения и `.env`, в коде ключей нет."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Чат-модель агента: любой OpenAI-совместимый сервер. По умолчанию — локальный LM Studio.
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "qwen3.5-4b"
    llm_temperature: float = 0.2
    # Цена за миллион токенов в рублях: для локальной модели ноль, для облака — из прайса.
    llm_price_in_rub: float = 0.0
    llm_price_out_rub: float = 0.0

    # Эмбеддинги для семантического поиска. Пусто — тот же сервер, что у чат-модели.
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "text-embedding-bge-m3"
    embed_batch: int = 64
    embed_query_prefix: str = ""        # для моделей, которым нужна инструкция перед запросом
    search_min_dense: float = 0.0       # ниже этого косинуса «смысловые» находки отбрасываются

    # Векторная база: пустой URL — Qdrant встроен в процесс (коллекция в памяти,
    # векторы в дисковом кеше), иначе — сервер Qdrant, например из docker-compose.
    qdrant_url: str = ""
    qdrant_collection: str = "places"

    data_dir: Path = ROOT / "data"
    runs_dir: Path = ROOT / "runs"

    # Предохранители агента.
    max_llm_calls: int = 8              # шагов модели на один вопрос
    run_timeout_s: float = 180.0        # потолок времени на один вопрос
    max_parallel_runs: int = 2          # сколько вопросов модель обрабатывает одновременно
    max_queue: int = 20                 # сколько вопросов может ждать очереди

    # Учёт контекста: после ответа, если промпт вырос выше порога, старые события
    # сворачиваются в сводку, а последние N событий остаются как есть.
    context_window: int = 16384         # окно, с которым модель загружена в LM Studio
    context_compact_tokens: int = 10000  # ~60% окна: остаётся место на шаги текущего вопроса
    context_keep_events: int = 8
    summary_reasoning_effort: str = "none"  # сводку пишем без рассуждения; пусто — не передавать

    # Авторизация.
    secret_key: str = ""
    session_days: int = 14
    cookie_secure: bool = False

    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def embed_url(self) -> str:
        return self.embed_base_url or self.llm_base_url

    @property
    def embed_key(self) -> str:
        return self.embed_api_key or self.llm_api_key

    @property
    def poi_db(self) -> Path:
        return self.data_dir / "poi.sqlite"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.sqlite"

    @property
    def sessions_db(self) -> Path:
        return self.data_dir / "sessions.sqlite"

    def jwt_secret(self) -> str:
        """Секрет подписи сессий: из окружения или однажды сгенерированный файл."""
        if self.secret_key:
            return self.secret_key
        path = self.data_dir / ".secret"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
