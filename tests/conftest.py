"""Общие фикстуры. Тесты не ходят в сеть и не зовут модель: LM Studio,
эмбеддинги и ADK Runner подменяются заглушками."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

import pytest

from rostov_guide.places.repository import PlaceRepository

COLUMNS = ("id", "osm_type", "category", "kind", "name", "brand", "street", "housenumber", "opening_hours",
           "is_24_7", "cuisine", "internet", "wheelchair", "outdoor_seating", "takeaway", "phone", "website",
           "lat", "lon")

# Маленький Ростов: несколько мест с адресами, часами и кухнями.
PLACES = [
    (1, "Пельмень", "cafe", None, "Береговая улица", "10", "10:00-21:00", 0, None, 47.2190, 39.7300),
    (2, "ПАРК", "cafe", None, "проспект Сельмаш", "1В", "11:00-24:00", 0, "russian", 47.2720, 39.7400),
    (3, "Хинкальная", "restaurant", None, "Пушкинская улица", "5", "Mo-Su 12:00-23:00", 0, "georgian", 47.2260, 39.7180),
    (4, "Аптека Ригла", "pharmacy", "Ригла", "Белорусская улица", "42", "24/7", 1, None, 47.2790, 39.7490),
    (5, "Лаки Фарма", "pharmacy", None, "Белорусская улица", "44", "Mo-Su 08:00-21:00", 0, None, 47.27917, 39.74933),
    (6, "Аптека", "pharmacy", None, "Пушкинская улица", "20", None, None, None, 47.2270, 39.7200),
    (7, "Сбербанк", "atm", "Сбербанк", "Пушкинская улица", "30", "24/7", 1, None, 47.2275, 39.7210),
    (8, "Суши Мастер", "fast_food", "Sushi Master", "Большая Садовая улица", "1", "10:00-22:00", 0, "sushi", 47.2220, 39.7100),
    (9, None, "playground", None, None, None, None, None, None, 47.2400, 39.7000),
    (10, "Ozon", "outpost", "Ozon", "Пушкинская улица", "40", "Mo-Su 09:00-21:00", 0, None, 47.2280, 39.7230),
]


def _row(p: tuple) -> tuple:
    pid, name, kind, brand, street, house, hours, is_24_7, cuisine, lat, lon = p
    return (pid, "node", "amenity", kind, name, brand, street, house, hours, is_24_7, cuisine,
            None, None, None, None, "+7 863 000-00-0" + str(pid % 10), None, lat, lon)


@pytest.fixture
def poi_db(tmp_path: Path) -> Path:
    path = tmp_path / "poi.sqlite"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE poi ({', '.join(COLUMNS)})")
    con.executemany(f"INSERT INTO poi VALUES ({', '.join('?' * len(COLUMNS))})", [_row(p) for p in PLACES])
    con.commit()
    con.close()
    return path


@pytest.fixture
async def repo(poi_db: Path) -> PlaceRepository:
    return await PlaceRepository.load(poi_db)


class FakeEmbedder:
    """Детерминированные «эмбеддинги»: мешок символьных триграмм в 64 измерениях.

    Смысла не понимают, но похожие строки дают близкие векторы — для проверки
    конвейера индекса этого достаточно.
    """

    model = "fake-embed"
    batch = 8

    def __init__(self) -> None:
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 64
        t = f"  {text.lower()}  "
        for i in range(len(t) - 2):
            v[int(hashlib.md5(t[i:i + 3].encode()).hexdigest(), 16) % 64] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
