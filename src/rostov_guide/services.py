"""Сборка зависимостей: справочник, эмбеддинги, векторный индекс.

Одно место, где из настроек получаются живые объекты: его зовут и веб-сервер
при старте, и команды CLI (`index`, `eval-search`).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field

from .config import Settings
from .places.repository import PlaceRepository
from .search.embeddings import EmbeddingClient
from .search.store import PlaceIndex

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    places: PlaceRepository
    embedder: EmbeddingClient
    index: PlaceIndex
    _index_task: asyncio.Task | None = field(default=None, repr=False)

    async def ensure_index(self, *, background: bool, rebuild: bool = False) -> None:
        """Собирает индекс, если его нет или он устарел.

        В вебе — в фоне: сервер отвечает сразу, интерфейс показывает прогресс,
        а агент до готовности ищет обычными фильтрами. В CLI — синхронно.
        """
        current = await self.index.attach(self.places)
        if current and not rebuild:
            return
        log.info("векторный индекс отсутствует или устарел — собираю (%s)", self.settings.embed_model)
        if not background:
            await self.index.rebuild(self.places, progress=_progress)
            return

        async def build() -> None:
            try:
                await self.index.rebuild(self.places)
            except Exception:
                log.exception("сборка векторного индекса не удалась")

        self._index_task = asyncio.create_task(build(), name="index-rebuild")

    async def aclose(self) -> None:
        if self._index_task and not self._index_task.done():
            self._index_task.cancel()
        await self.index.aclose()
        await self.embedder.aclose()


def _progress(done: int, total: int) -> None:
    sys.stderr.write(f"\r  индексация мест: {done}/{total}")
    if done >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


async def build_services(settings: Settings) -> Services:
    if not settings.poi_db.exists():
        raise SystemExit(f"нет базы мест {settings.poi_db}: она лежит в репозитории, проверь data/")
    places = await PlaceRepository.load(settings.poi_db)
    embedder = EmbeddingClient(settings.embed_url, settings.embed_key, settings.embed_model,
                               batch=settings.embed_batch, query_prefix=settings.embed_query_prefix,
                               cache_path=settings.data_dir / "embed_cache.sqlite")
    index = PlaceIndex.open(url=settings.qdrant_url, collection=settings.qdrant_collection, embedder=embedder,
                            meta_path=settings.data_dir / "index_meta.json", min_dense=settings.search_min_dense)
    return Services(settings, places, embedder, index)
