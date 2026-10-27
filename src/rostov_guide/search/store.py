"""Векторный индекс мест в Qdrant: гибридный поиск «по смыслу» + «по словам».

Почему Qdrant: в одной коллекции живут плотные векторы (эмбеддинги) и
разреженные (BM25 с IDF на стороне базы), есть фильтр по payload и
асинхронный клиент. В проде коллекция живёт в сервере Qdrant (`QDRANT_URL`)
и переживает перезапуски. Без сервера Qdrant встраивается в процесс и держит
коллекцию в памяти: встроенный режим на диске пишет ~40 мс на точку и при этом
блокирует цикл событий, а в памяти 8860 мест загружаются за секунды. Долговечное
хранилище в этом режиме — дисковый кеш эмбеддингов (`embeddings.py`), так что
модель считает векторы один раз, а при старте коллекция собирается из кеша.

Слияние двух выдач делаем сами (Reciprocal Rank Fusion), а не внутри Qdrant:
так у каждого результата видно, кто его нашёл — смысл, слова или оба, —
и это показывается пользователю в ходе рассуждения агента.

Пересборка без простоя: новая коллекция строится под своим именем, старая
всё это время отвечает на запросы; в конце `index_meta.json` переключается
на новую, старая удаляется.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import AsyncQdrantClient, models

from ..places.repository import PlaceRepository
from . import sparse
from .documents import DOC_VERSION, document, group_of
from .embeddings import EmbeddingClient

log = logging.getLogger(__name__)

RRF_K = 60


@dataclass(frozen=True)
class Hit:
    place_id: int
    score: float                # RRF: чем больше, тем выше в обеих выдачах
    dense: float | None         # косинус с запросом, если нашёл плотный поиск
    lexical: bool               # нашёлся ли по совпадению слов

    @property
    def found_by(self) -> str:
        if self.dense is not None and self.lexical:
            return "смысл+слова"
        return "слова" if self.lexical else "смысл"


@dataclass
class IndexStatus:
    state: str = "missing"      # missing | building | ready | error
    done: int = 0
    total: int = 0
    error: str | None = None
    serving: bool = False       # есть ли готовая коллекция, по которой уже можно искать

    def as_dict(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total,
                "error": self.error, "serving": self.serving}


@dataclass
class PlaceIndex:
    client: AsyncQdrantClient
    prefix: str
    embedder: EmbeddingClient
    meta_path: Path
    min_dense: float = 0.0
    persistent: bool = False
    status: IndexStatus = field(default_factory=IndexStatus)
    _active: str | None = None

    @classmethod
    def open(cls, *, url: str, collection: str, embedder: EmbeddingClient,
             meta_path: Path, min_dense: float = 0.0) -> "PlaceIndex":
        if url:
            return cls(AsyncQdrantClient(url=url), collection, embedder, meta_path, min_dense, persistent=True)
        return cls(AsyncQdrantClient(location=":memory:"), collection, embedder, meta_path, min_dense)

    def _expected(self, repo: PlaceRepository) -> dict:
        return {"embed_model": self.embedder.model, "doc_version": DOC_VERSION, "count": len(repo)}

    def _read_meta(self) -> dict:
        if not self.meta_path.exists():
            return {}
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    async def attach(self, repo: PlaceRepository) -> bool:
        """Подключает готовую коллекцию из meta. True — если она актуальна."""
        if not self.persistent:
            return False                # в памяти после старта пусто: собираем из кеша
        meta = self._read_meta()
        name = meta.get("collection")
        if name and await self.client.collection_exists(name):
            self._active = name
            self.status.serving = True
            current = all(meta.get(k) == v for k, v in self._expected(repo).items())
            self.status.state = "ready" if current else "missing"
            return current
        return False

    async def rebuild(self, repo: PlaceRepository,
                      progress: Callable[[int, int], None] | None = None) -> None:
        started = time.perf_counter()
        places = list(repo.places.values())
        texts = [document(p) for p in places]
        avg_len = sum(len(sparse.terms(t)) for t in texts) / max(len(texts), 1)
        tag = hashlib.sha1(f"{self.embedder.model}:{DOC_VERSION}".encode()).hexdigest()[:8]
        name = f"{self.prefix}_{tag}_{int(time.time())}"
        self.status.state, self.status.done, self.status.total, self.status.error = "building", 0, len(places), None
        try:
            probe = await self.embedder.embed([texts[0]])
            await self.client.create_collection(
                name,
                vectors_config={"dense": models.VectorParams(size=len(probe[0]), distance=models.Distance.COSINE)},
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
            await self.client.create_payload_index(name, "kind", models.PayloadSchemaType.KEYWORD)

            # Конвейер: пока Qdrant пишет одну пачку, модель уже считает следующую.
            step = 256                  # пачка в памяти пишется ~0.15 с: цикл событий не замирает
            pending: asyncio.Task | None = None
            for start in range(0, len(places), step):
                chunk, chunk_texts = places[start:start + step], texts[start:start + step]
                vectors = await self.embedder.embed(chunk_texts)
                points = []
                for place, text, vector in zip(chunk, chunk_texts, vectors):
                    idx, val = sparse.encode_document(text, avg_len)
                    points.append(models.PointStruct(
                        id=place.id,
                        vector={"dense": vector, "bm25": models.SparseVector(indices=idx, values=val)},
                        payload={"kind": place.kind, "group": group_of(place.kind)},
                    ))
                if pending:
                    await pending
                pending = asyncio.create_task(self.client.upsert(name, points=points, wait=True))
                self.status.done = min(start + step, len(places))
                if progress:
                    progress(self.status.done, len(places))
            if pending:
                await pending
        except Exception as exc:
            self.status.state, self.status.error = "error", str(exc)
            if await self.client.collection_exists(name):
                await self.client.delete_collection(name)
            raise

        old = self._active
        meta = self._expected(repo) | {"collection": name, "dim": len(probe[0]),
                                       "built_s": round(time.perf_counter() - started, 1)}
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._active = name
        self.status.state, self.status.serving = "ready", True
        if old and old != name and await self.client.collection_exists(old):
            await self.client.delete_collection(old)
        log.info("индекс собран: %s", meta)

    async def search(self, query: str, *, kinds: set[str] | None = None, limit: int = 8,
                     candidates: int = 40, mode: str = "hybrid") -> list[Hit]:
        """mode: hybrid — оба поиска и RRF; dense / lexical — только один (для оценки)."""
        if not self._active:
            raise RuntimeError("векторный индекс ещё строится")
        flt = None
        if kinds:
            flt = models.Filter(must=[models.FieldCondition(key="kind", match=models.MatchAny(any=sorted(kinds)))])
        requests, sources = [], []
        if mode in ("hybrid", "dense"):
            dense_vec = await self.embedder.embed_query(query)
            requests.append(models.QueryRequest(query=dense_vec, using="dense", filter=flt, limit=candidates))
            sources.append("dense")
        idx, val = sparse.encode_query(query)
        if idx and mode in ("hybrid", "lexical"):
            requests.append(models.QueryRequest(query=models.SparseVector(indices=idx, values=val),
                                                using="bm25", filter=flt, limit=candidates))
            sources.append("lexical")
        if not requests:
            return []
        responses = await self.client.query_batch_points(self._active, requests=requests)

        fused: dict[int, float] = {}
        dense_score: dict[int, float] = {}
        lexical: set[int] = set()
        for source, response in zip(sources, responses):
            for rank, point in enumerate(response.points):
                pid = int(point.id)
                if source == "dense":
                    if point.score < self.min_dense:
                        continue
                    dense_score[pid] = point.score
                else:
                    lexical.add(pid)
                fused[pid] = fused.get(pid, 0.0) + 1 / (RRF_K + rank + 1)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [Hit(pid, round(score, 4), dense_score.get(pid), pid in lexical) for pid, score in ranked]

    async def aclose(self) -> None:
        await self.client.close()
