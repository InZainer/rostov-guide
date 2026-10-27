"""Клиент эмбеддингов к любому OpenAI-совместимому `/embeddings` (LM Studio, облако).

Локальный LM Studio считает около 40 коротких документов в секунду, и батч
это не ускоряет, поэтому посчитанные векторы документов кешируются на диске
(ключ — модель и текст): пересборка индекса после правки формата документа
пересчитывает только изменившиеся тексты.
"""

from __future__ import annotations

import array
import asyncio
import hashlib
from collections import OrderedDict
from pathlib import Path

import aiosqlite
import httpx

_RETRY_STATUS = {400, 408, 429, 500, 502, 503, 504}   # LM Studio отвечает 400, пока перегружает модель


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model: str, *, batch: int = 64,
                 query_prefix: str = "", cache_path: Path | None = None, cache_size: int = 512,
                 timeout: float = 60.0, retries: int = 3) -> None:
        self.model = model
        self.batch = batch
        self.query_prefix = query_prefix
        self.cache_path = cache_path
        self.retries = retries
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout,
                                       headers={"Authorization": f"Bearer {api_key}"})
        # Один и тот же запрос («пельмени») приходит от разных пользователей:
        # держим последние векторы запросов в памяти.
        self._queries: OrderedDict[str, list[float]] = OrderedDict()
        self._queries_size = cache_size

    async def _request(self, texts: list[str]) -> list[list[float]]:
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                resp = await self._http.post("/embeddings", json={"model": self.model, "input": texts})
            except httpx.HTTPError as exc:
                error = f"сервис эмбеддингов недоступен ({self.model}): {exc!r}"
            else:
                if resp.status_code == 200:
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    return [d["embedding"] for d in data]
                error = f"сервис эмбеддингов ответил {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in _RETRY_STATUS:
                    break
            if attempt < self.retries:
                await asyncio.sleep(delay)
                delay *= 2
        raise EmbeddingError(error)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторы документов: из дискового кеша, недостающие — у модели."""
        keys = [self._key(t) for t in texts]
        found = await self._cache_get(keys)
        missing = [i for i, k in enumerate(keys) if k not in found]
        for start in range(0, len(missing), self.batch):
            idx = missing[start:start + self.batch]
            vectors = await self._request([texts[i] for i in idx])
            fresh = {keys[i]: v for i, v in zip(idx, vectors)}
            found.update(fresh)
            await self._cache_put(fresh)
        return [found[k] for k in keys]

    async def embed_query(self, text: str) -> list[float]:
        key = text.strip().lower()
        if key in self._queries:
            self._queries.move_to_end(key)
            return self._queries[key]
        vector = (await self._request([self.query_prefix + text.strip()]))[0]
        self._queries[key] = vector
        if len(self._queries) > self._queries_size:
            self._queries.popitem(last=False)
        return vector

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.model}\x00{text}".encode("utf-8")).hexdigest()

    async def _cache_get(self, keys: list[str]) -> dict[str, list[float]]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        out: dict[str, list[float]] = {}
        async with aiosqlite.connect(self.cache_path) as db:
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                rows = await db.execute_fetchall(
                    f"SELECT key, vec FROM embeddings WHERE key IN ({','.join('?' * len(chunk))})", chunk)
                for key, blob in rows:
                    out[key] = array.array("f", blob).tolist()
        return out

    async def _cache_put(self, vectors: dict[str, list[float]]) -> None:
        if not self.cache_path or not vectors:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.cache_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vec BLOB NOT NULL)")
            await db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?)",
                                 [(k, array.array("f", v).tobytes()) for k, v in vectors.items()])
            await db.commit()

    async def aclose(self) -> None:
        await self._http.aclose()
