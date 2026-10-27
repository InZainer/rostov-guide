"""Инструменты агента: сжатый ответ, ошибки как наблюдения, точка пользователя из состояния."""

from types import SimpleNamespace

import pytest

from rostov_guide.agent.tools import USER_LOCATION_KEY, make_tools
from rostov_guide.search.store import PlaceIndex


class FakeToolContext:
    def __init__(self, state=None):
        self.state = state or {}


@pytest.fixture
async def tools(repo, fake_embedder, tmp_path):
    index = PlaceIndex.open(url="", collection="t", embedder=fake_embedder, meta_path=tmp_path / "m.json")
    await index.rebuild(repo)
    services = SimpleNamespace(places=repo, index=index)
    fns = {f.__name__: f for f in make_tools(services)}
    yield fns
    await index.aclose()


async def test_find_places_total_answers_how_many(tools):
    result = await tools["find_places"](kind="аптеки", only_24_7=True)
    assert result["total"] == 1 and result["places"][0]["name"] == "Аптека Ригла"
    assert "lat" not in result["places"][0]            # координаты модели не нужны


async def test_unknown_kind_is_an_observation_with_hint(tools):
    result = await tools["find_places"](kind="pharmacie")
    assert "error" in result and "pharmacy" in result["hint"]


async def test_find_places_requires_some_filter(tools):
    assert "error" in await tools["find_places"]()


async def test_nearest_by_address(tools):
    result = await tools["nearest_places"](kind="pharmacy", address="Белорусская 44", limit=2)
    assert result["origin"]["precision"] == "house"
    assert [p["name"] for p in result["places"]] == ["Лаки Фарма", "Аптека Ригла"]
    assert result["places"][0]["distance_m"] == 0


async def test_nearest_uses_point_from_map(tools):
    ctx = FakeToolContext({USER_LOCATION_KEY: {"lat": 47.2275, "lon": 39.7210, "label": "Пушкинская"}})
    result = await tools["nearest_places"](kind="atm", tool_context=ctx)
    assert result["places"][0]["name"] == "Сбербанк"


async def test_nearest_without_anchor_asks_for_one(tools):
    result = await tools["nearest_places"](kind="pharmacy", tool_context=FakeToolContext())
    assert "error" in result and "карте" in result["hint"]


async def test_search_places_marks_how_found(tools):
    result = await tools["search_places"](query="пельмени", limit=3)
    top = {p["name"]: p["found_by"] for p in result["places"][:2]}
    assert top == {"Пельмень": "смысл+слова", "ПАРК": "смысл+слова"}


async def test_search_food_kind_covers_whole_food_group(tools):
    # «ресторан» — но «Пельмень» кафе: еда ищется всей группой
    result = await tools["search_places"](query="пельмени", kind="restaurant", limit=5)
    assert "Пельмень" in [p["name"] for p in result["places"]]


async def test_place_details_unknown_id(tools):
    assert "error" in await tools["place_details"](place_id=99999)
    card = await tools["place_details"](place_id=4)
    assert card["name"] == "Аптека Ригла" and card["hours_label"] == "круглосуточно"


async def test_nearest_can_be_limited_to_round_the_clock(tools):
    result = await tools["nearest_places"](kind="pharmacy", address="Белорусская 44", only_24_7=True)
    assert [p["name"] for p in result["places"]] == ["Аптека Ригла"]


async def test_query_repeating_kind_does_not_shrink_the_pool(tools):
    # найдено на перепроверке: kind + query «круглосуточная аптека» искали ближайшую среди
    # смысловых совпадений, и настоящая ближайшая туда не попадала
    result = await tools["nearest_places"](kind="pharmacy", query="круглосуточная аптека",
                                           address="Белорусская 44", only_24_7=True)
    assert [p["name"] for p in result["places"]] == ["Аптека Ригла"]


def test_query_adds_meaning():
    from rostov_guide.agent.tools import query_adds_meaning
    assert not query_adds_meaning("круглосуточная аптека", "pharmacy")
    assert not query_adds_meaning("ближайшие аптеки", "pharmacy")
    assert query_adds_meaning("грузинская кухня", "restaurant")
    assert query_adds_meaning("шаурма", None)
