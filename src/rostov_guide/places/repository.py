"""Справочник мест Ростова: снимок OSM, 8860 объектов.

База только читается и весит 2 МБ, поэтому при старте она целиком поднимается
в память, а фильтры и геопоиск идут по списку: это микросекунды против
похода в SQLite на каждый вызов инструмента. Загрузка асинхронная (aiosqlite),
чтобы старт не блокировал цикл событий.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiosqlite

from . import hours
from .kinds import cuisine_labels, kind_label

NO_DATA = "нет в базе"
LAT_M = 111_320                                   # метров в градусе широты
LON_M = LAT_M * math.cos(math.radians(47.23))     # ... и долготы на широте Ростова

# служебные слова адреса в любом падеже: «на Белорусской улице» → «Белорусской»
_STREET_NOISE = re.compile(
    r"^(улиц\w*|ул|проспект\w*|просп|пр|переул\w*|пер|бульвар\w*|бул|площад\w*|пл|шоссе|"
    r"проезд\w*|дом\w*|д|г|город\w*|ростов|ростове|ростов-на-дону|на|дону|рядом|около|возле|"
    r"street|avenue)$")


@dataclass(slots=True)
class Place:
    id: int
    kind: str
    category: str
    name: str | None
    brand: str | None
    street: str | None
    housenumber: str | None
    opening_hours: str | None
    is_24_7: int | None
    cuisine: str | None
    internet: int | None
    wheelchair: int | None
    outdoor_seating: int | None
    takeaway: int | None
    phone: str | None
    website: str | None
    lat: float
    lon: float

    @property
    def address(self) -> str | None:
        if not self.street:
            return None
        return f"{self.street}, {self.housenumber}" if self.housenumber else self.street

    @property
    def title(self) -> str:
        return self.name or self.brand or kind_label(self.kind).capitalize()

    def brief(self, now: datetime | None = None, distance_m: int | None = None) -> dict:
        """Короткая строка для модели: только то, что нужно для ответа.

        Адрес и часы есть всегда — если в OSM их нет, так и написано: «нет в базе».
        Выброшенное пустое поле модель читает как «не запросил» и идёт искать его
        другими инструментами, пока не кончится лимит шагов (видно в журнале прогонов).
        """
        item: dict = {"id": self.id, "name": self.title, "kind": kind_label(self.kind),
                      "address": self.address or NO_DATA}
        if self.cuisine:
            item["cuisine"] = ", ".join(cuisine_labels(self.cuisine))
        state = hours.status(self.opening_hours, now)
        item["hours"] = self.opening_hours or NO_DATA
        if state.open is not None:
            item["now"] = state.label
        if distance_m is not None:
            item["distance_m"] = distance_m
        return item

    def card(self, now: datetime | None = None, distance_m: int | None = None) -> dict:
        """Полная карточка для интерфейса: с координатами для карты."""
        state = hours.status(self.opening_hours, now)
        return {
            "id": self.id, "name": self.title, "kind": self.kind, "kind_label": kind_label(self.kind),
            "brand": self.brand, "address": self.address, "hours": self.opening_hours,
            "open_now": state.open, "hours_label": state.label,
            "cuisine": cuisine_labels(self.cuisine), "phone": self.phone, "website": self.website,
            "wheelchair": self.wheelchair, "internet": self.internet,
            "lat": self.lat, "lon": self.lon, "distance_m": distance_m,
        }


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    label: str
    precision: str = "house"     # house — точный дом, street — середина улицы, place — по названию места


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Расстояние по прямой: на масштабе города плоской формулы достаточно."""
    return int(math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M))


def _stem(word: str) -> str:
    return word[:-2] if len(word) > 5 else word


@dataclass
class PlaceRepository:
    places: dict[int, Place] = field(default_factory=dict)
    kinds: Counter = field(default_factory=Counter)

    @classmethod
    async def load(cls, path: Path) -> "PlaceRepository":
        repo = cls()
        async with aiosqlite.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as con:
            con.row_factory = aiosqlite.Row
            async with con.execute(
                "SELECT id, kind, category, name, brand, street, housenumber, opening_hours, is_24_7, "
                "cuisine, internet, wheelchair, outdoor_seating, takeaway, phone, website, lat, lon "
                "FROM poi"
            ) as cursor:
                async for row in cursor:
                    place = Place(**dict(row))
                    repo.places[place.id] = place
                    repo.kinds[place.kind] += 1
        return repo

    def __len__(self) -> int:
        return len(self.places)

    def get(self, place_id: int) -> Place | None:
        return self.places.get(place_id)

    def filter(self, *, kinds: set[str] | None = None, street: str | None = None,
               name: str | None = None, brand: str | None = None, cuisine: str | None = None,
               only_24_7: bool = False, open_now: bool = False,
               now: datetime | None = None) -> list[Place]:
        street_key = _street_key(street) if street else None
        name_key = name.lower().strip() if name else None
        brand_key = brand.lower().strip() if brand else None
        cuisine_key = cuisine.lower().strip() if cuisine else None
        out = []
        for p in self.places.values():
            if kinds and p.kind not in kinds:
                continue
            if street_key and not (p.street and street_key in p.street.lower()):
                continue
            if name_key and not (p.name and name_key in p.name.lower()):
                continue
            if brand_key and not ((p.brand and brand_key in p.brand.lower())
                                  or (p.name and brand_key in p.name.lower())):
                continue
            if cuisine_key and not (p.cuisine and (cuisine_key in p.cuisine.lower()
                                                   or any(cuisine_key in c for c in cuisine_labels(p.cuisine)))):
                continue
            if only_24_7 and p.is_24_7 != 1:
                continue
            if open_now and hours.status(p.opening_hours, now).open is not True:
                continue
            out.append(p)
        return out

    @staticmethod
    def by_distance(places: list[Place], lat: float, lon: float,
                    radius_m: int | None = None) -> list[tuple[Place, int]]:
        ranked = sorted(((p, distance_m(lat, lon, p.lat, p.lon)) for p in places), key=lambda x: x[1])
        return [(p, d) for p, d in ranked if radius_m is None or d <= radius_m]

    def geocode(self, address: str) -> Point | None:
        """Адрес или название места → точка на карте.

        Геокодера у нас нет, но есть 8860 мест с адресами: сначала ищем дом
        по улице и номеру, потом середину улицы, потом место с таким названием
        («ТЦ Горизонт», «Центральный рынок»).
        """
        text = address.strip()
        if not text:
            return None
        number_match = re.search(r"\b(\d+[а-яА-Я]?(?:/\d+)?)\b(?!.*\d)", text)
        number = number_match.group(1).lower() if number_match else None
        street_key = _street_key(text)
        if street_key:
            on_street = [p for p in self.places.values() if p.street and street_key in p.street.lower()]
            if on_street and number:
                exact = [p for p in on_street if (p.housenumber or "").lower() == number]
                same_base = exact or [p for p in on_street
                                      if re.match(rf"{re.escape(number)}(\D|$)", (p.housenumber or "").lower())]
                if same_base:
                    p = same_base[0]
                    return Point(p.lat, p.lon, f"{p.street}, {p.housenumber}", "house")
            if on_street:
                lat = sum(p.lat for p in on_street) / len(on_street)
                lon = sum(p.lon for p in on_street) / len(on_street)
                return Point(lat, lon, f"{on_street[0].street} (середина улицы)", "street")
        key = text.lower()
        named = [p for p in self.places.values() if p.name and key in p.name.lower()]
        if named:
            p = min(named, key=lambda x: len(x.name or ""))
            return Point(p.lat, p.lon, f"{p.title}" + (f", {p.address}" if p.address else ""), "place")
        return None


def _street_key(text: str) -> str | None:
    """«ул. Пушкинская, 10» → «пушкинск»: основа самого длинного значимого слова."""
    words = [w for w in re.findall(r"[а-яёa-z0-9-]+", text.lower())
             if not _STREET_NOISE.match(w) and not w.isdigit() and len(w) >= 3
             and not re.fullmatch(r"\d+[а-я]?", w)]
    if not words:
        return None
    return _stem(max(words, key=len))
