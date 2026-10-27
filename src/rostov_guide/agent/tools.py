"""Инструменты агента над справочником и векторным индексом.

Принципы, проверенные на маленькой локальной модели:
- инструментов четыре, аргументы плоские, со значениями по умолчанию;
- описание говорит, когда звать и когда НЕ звать, с примером;
- ошибка ввода — не исключение, а ответ `{"error", "hint"}`: модель видит, что
  не так, и исправляет вызов сама;
- результат сжатый: до 10 мест, только непустые поля, без координат для модели
  (координаты для карты интерфейс берёт из справочника по id).
"""

from __future__ import annotations

from datetime import datetime

from google.adk.tools import ToolContext

from ..places import hours
from ..places.hours import ROSTOV_TZ
from ..places.kinds import GROUPS, kind_label, resolve_kind, suggest_kinds
from ..search import sparse
from ..places.repository import NO_DATA, Place, Point
from ..services import Services

MAX_LIMIT = 10
# Слова, которые уже выражены фильтрами инструмента, а не смыслом запроса.
_FILTER_WORDS = "круглосуточная круглосуточно круглосуточные открытая открыто открыт сейчас ближайшая ближайший ближе"


def query_adds_meaning(query: str, kind: str | None) -> bool:
    """Уточняет ли query что-то сверх вида места и фильтров.

    «круглосуточная аптека» при kind=pharmacy ничего не добавляет — тогда ближайшее ищется
    среди всех аптек, а не среди 30 смысловых совпадений, куда нужная может не попасть.
    «грузинская кухня» при kind=restaurant добавляет — тогда ищем по смыслу.
    """
    covered = set(sparse.meaningful_terms(_FILTER_WORDS))
    if kind:
        covered |= set(sparse.meaningful_terms(kind_label(kind)))
    return any(t not in covered for t in sparse.meaningful_terms(query))
FOOD = frozenset(GROUPS["food"])
USER_LOCATION_KEY = "user_location"     # точка, которую пользователь поставил на карте


def _now() -> datetime:
    return datetime.now(ROSTOV_TZ)


def _limit(value: int, default: int) -> int:
    try:
        return max(1, min(int(value or default), MAX_LIMIT))
    except (TypeError, ValueError):
        return default


def make_tools(services: Services) -> list:
    places = services.places
    known = set(places.kinds)

    def kind_or_error(kind: str) -> tuple[str | None, dict | None]:
        resolved = resolve_kind(kind, known)
        if resolved:
            return resolved, None
        hint = suggest_kinds(kind, known)
        return None, {"error": f"вид места {kind!r} не найден в базе",
                      "hint": f"похожие виды: {', '.join(hint)}" if hint
                      else "назови вид латиницей (pharmacy, cafe, restaurant) или используй search_places"}

    def user_point(tool_context: ToolContext) -> Point | None:
        loc = tool_context.state.get(USER_LOCATION_KEY)
        if isinstance(loc, dict) and "lat" in loc and "lon" in loc:
            return Point(float(loc["lat"]), float(loc["lon"]), loc.get("label") or "точка на карте", "user")
        return None

    def with_distance(items: list[Place], origin: Point | None, now: datetime) -> list[dict]:
        if origin is None:
            return [p.brief(now) for p in items]
        return [p.brief(now, places.by_distance([p], origin.lat, origin.lon)[0][1]) for p in items]

    async def search_places(query: str, kind: str = "", limit: int = 5,
                            tool_context: ToolContext | None = None) -> dict:
        """Ищет места по смыслу описания: блюдо, кухня, атмосфера, кусок забытого названия.

        Зови, когда человек не знает точного названия или описывает место словами:
        «там были пельмени», «грузинская кухня», «где выпить кофе с собой»,
        «ресторан с названием вроде Пирс». Находит и по смыслу, и по совпадению слов.
        Не зови для подсчёта («сколько») и для «ближайшего» — для этого find_places
        и nearest_places. found_by «смысл+слова» — самое надёжное совпадение;
        в ответ бери только правдоподобные места, а не весь список.

        Args:
            query: только суть — блюдо, кухня или кусок названия: «пельмени», «грузинская кухня», «Пирс».
            kind: необязательный вид места: restaurant, cafe, fast_food, bar, pharmacy… Еда (ресторан, кафе, фастфуд, бар) ищется вся вместе.
            limit: сколько мест вернуть, от 1 до 10.
        """
        query = (query or "").strip()
        if not query:
            return {"error": "пустой запрос", "hint": "передай в query, что искать: блюдо, кухню, часть названия"}
        kinds = None
        if kind:
            resolved, error = kind_or_error(kind)
            if error:
                return error
            # «забыл название ресторана» часто оказывается кафе: еда ищется всей группой
            kinds = set(FOOD) if resolved in FOOD else {resolved}
        if not services.index.status.serving:
            return {"error": "смысловой поиск ещё готовится (строится индекс)",
                    "hint": "пока используй find_places с фильтром name или kind"}
        hits = await services.index.search(query, kinds=kinds, limit=_limit(limit, 5))
        now = _now()
        origin = user_point(tool_context) if tool_context else None
        items = []
        for hit in hits:
            place = places.get(hit.place_id)
            if place is None:
                continue
            item = with_distance([place], origin, now)[0]
            item["found_by"] = hit.found_by
            items.append(item)
        if not items:
            return {"query": query, "places": [], "note": "ничего похожего не нашлось"}
        return {"query": query, "places": items}

    async def find_places(kind: str = "", street: str = "", name: str = "", open_now: bool = False,
                          only_24_7: bool = False, limit: int = 5,
                          tool_context: ToolContext | None = None) -> dict:
        """Точный поиск по фильтрам: вид места, улица, название или сеть, режим работы.

        Поле total — сколько всего мест подходит, поэтому на вопросы «сколько»
        отвечай по total. Список places — первые limit мест.
        Примеры: «сколько аптек» → kind="pharmacy"; «круглосуточные аптеки» →
        kind="pharmacy", only_24_7=true; «кафе на Пушкинской» → kind="cafe",
        street="Пушкинская"; «пункты Ozon» → kind="outpost", name="Ozon".
        Не зови, если название неизвестно и место описано словами — это search_places.

        Args:
            kind: вид места латиницей: pharmacy, cafe, restaurant, fast_food, bar, supermarket, outpost, bank, atm, school, park, hotel, fuel, hairdresser. Можно по-русски: «аптека».
            street: часть названия улицы, например «Пушкинская».
            name: часть названия места или сети, например «Магнит», «Ozon».
            open_now: только открытые прямо сейчас.
            only_24_7: только круглосуточные.
            limit: сколько мест показать в списке, от 1 до 10.
        """
        kinds = None
        if kind:
            resolved, error = kind_or_error(kind)
            if error:
                return error
            kinds = {resolved}
        if not (kinds or street or name):
            return {"error": "нужен хотя бы один фильтр", "hint": "задай kind, street или name"}
        now = _now()
        found = places.filter(kinds=kinds, street=street or None, brand=name or None,
                              open_now=open_now, only_24_7=only_24_7, now=now)
        found.sort(key=lambda p: (p.name is None, p.title))
        origin = user_point(tool_context) if tool_context else None
        result: dict = {"total": len(found), "places": with_distance(found[:_limit(limit, 5)], origin, now)}
        if not found:
            result["note"] = "по этим фильтрам мест нет; попробуй ослабить фильтр или search_places"
        return result

    async def nearest_places(kind: str = "", query: str = "", address: str = "", place_id: int = 0,
                             open_now: bool = False, only_24_7: bool = False, limit: int = 3,
                             tool_context: ToolContext | None = None) -> dict:
        """Ближайшие места к точке, с расстоянием в метрах.

        Точка отсчёта — одно из трёх: address (адрес или известное место:
        «Белорусская 44», «Центральный рынок»), place_id (id места из прошлого
        ответа) или, если оба пусты, точка, которую пользователь поставил на карте.
        Что искать — kind (вид места) или query (описание словами: «пельмени»).
        Пример: «ближайшая аптека к Белорусской 44» → kind="pharmacy", address="Белорусская 44".

        Args:
            kind: вид места латиницей: pharmacy, cafe, restaurant, atm, outpost, fuel, supermarket.
            query: описание словами, только если вид места его не передаёт: «шаурма», «грузинская кухня». Не повторяй в нём вид и фильтры.
            address: адрес или название места, от которого считать расстояние.
            place_id: id места, от которого считать расстояние.
            open_now: только открытые прямо сейчас.
            only_24_7: только круглосуточные — например, «а какая из круглосуточных ближе».
            limit: сколько мест вернуть, от 1 до 10.
        """
        if not kind and not query:
            return {"error": "не сказано, что искать", "hint": "задай kind (например pharmacy) или query"}
        origin: Point | None = None
        if place_id:
            anchor = places.get(int(place_id))
            if anchor is None:
                return {"error": f"места с id {place_id} нет", "hint": "возьми id из результата другого инструмента"}
            origin = Point(anchor.lat, anchor.lon, anchor.title + (f", {anchor.address}" if anchor.address else ""), "place")
        elif address:
            origin = places.geocode(address)
            if origin is None:
                return {"error": f"адрес {address!r} не найден",
                        "hint": "уточни улицу и номер дома или назови известное место рядом"}
        elif tool_context is not None:
            origin = user_point(tool_context)
        if origin is None:
            return {"error": "неизвестна точка отсчёта",
                    "hint": "спроси у пользователя адрес или попроси поставить точку на карте"}

        now = _now()
        resolved_kind = None
        if kind:
            resolved_kind, error = kind_or_error(kind)
            if error:
                return error
        if query and not query_adds_meaning(query, resolved_kind):
            query = ""                  # запрос повторяет вид и фильтры — ищем по всем местам вида
        if not query and not resolved_kind:
            return {"error": "не сказано, что искать", "hint": "задай kind (например pharmacy) или query"}
        if query:
            if not services.index.status.serving:
                return {"error": "смысловой поиск ещё готовится", "hint": "используй kind вместо query"}
            kinds = {resolved_kind} if resolved_kind else None
            hits = await services.index.search(query, kinds=kinds, limit=30)
            pool = [p for h in hits if (p := places.get(h.place_id))]
        else:
            pool = places.filter(kinds={resolved_kind})
        if open_now:
            pool = [p for p in pool if hours.status(p.opening_hours, now).open is True]
        if only_24_7:
            pool = [p for p in pool if p.is_24_7 == 1]
        ranked = places.by_distance(pool, origin.lat, origin.lon)[:_limit(limit, 3)]
        return {
            "origin": {"label": origin.label, "precision": origin.precision,
                       "lat": round(origin.lat, 5), "lon": round(origin.lon, 5)},
            "places": [p.brief(now, d) for p, d in ranked],
        }

    async def place_details(place_id: int) -> dict:
        """Подробная карточка одного места по id: адрес, часы, открыто ли сейчас, телефон, сайт, кухня.

        Зови, когда спрашивают подробности о конкретном месте из прошлого ответа:
        «а до скольки работает второе?», «какой у них телефон?».

        Args:
            place_id: id места из результата search_places, find_places или nearest_places.
        """
        place = places.get(int(place_id or 0))
        if place is None:
            return {"error": f"места с id {place_id} нет", "hint": "возьми id из результата другого инструмента"}
        card = place.card(_now())
        details = {k: v for k, v in card.items()
                   if v not in (None, [], "") and k not in ("lat", "lon", "kind", "distance_m")}
        for key in ("address", "hours", "phone"):       # «нет в базе», а не пропавшее поле
            details.setdefault(key, NO_DATA)
        return details

    return [search_places, find_places, nearest_places, place_details]
