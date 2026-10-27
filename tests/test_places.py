from rostov_guide.places.kinds import cuisine_labels, resolve_kind, suggest_kinds
from rostov_guide.search import sparse
from rostov_guide.search.documents import document

KNOWN = {"pharmacy", "cafe", "restaurant", "fast_food", "atm", "outpost", "playground"}


def test_resolve_kind_accepts_tag_russian_and_variants():
    assert resolve_kind("pharmacy", KNOWN) == "pharmacy"
    assert resolve_kind("аптека", KNOWN) == "pharmacy"
    assert resolve_kind("аптеки", KNOWN) == "pharmacy"
    assert resolve_kind("Fast food", KNOWN) == "fast_food"
    assert resolve_kind("fast-food", KNOWN) == "fast_food"
    assert resolve_kind("космодром", KNOWN) is None


def test_suggestions_for_typos():
    assert "pharmacy" in suggest_kinds("pharmacie", KNOWN)


def test_cuisine_labels_are_russian_and_deduplicated():
    assert cuisine_labels("russian;pizza;italian_pizza") == ["русская", "пицца", "итальянская"]
    assert cuisine_labels(None) == []


async def test_repository_filters(repo):
    assert len(repo) == 10
    pharmacies = repo.filter(kinds={"pharmacy"})
    assert {p.id for p in pharmacies} == {4, 5, 6}
    assert [p.id for p in repo.filter(kinds={"pharmacy"}, only_24_7=True)] == [4]
    assert [p.id for p in repo.filter(kinds={"pharmacy"}, street="Пушкинской")] == [6]
    assert [p.id for p in repo.filter(brand="ozon")] == [10]
    assert [p.id for p in repo.filter(cuisine="грузин")] == [3]


async def test_geocode_house_street_and_place(repo):
    house = repo.geocode("Белорусской улице, 44")
    assert house.precision == "house" and (house.lat, house.lon) == (47.27917, 39.74933)
    street = repo.geocode("улица Пушкинская")
    assert street.precision == "street"
    named = repo.geocode("Хинкальная")
    assert named.precision == "place"
    assert repo.geocode("улица Несуществующая 999") is None


async def test_nearest_by_distance(repo):
    origin = repo.geocode("Белорусская 44")
    ranked = repo.by_distance(repo.filter(kinds={"pharmacy"}), origin.lat, origin.lon)
    assert ranked[0][0].id == 5 and ranked[0][1] == 0
    assert ranked[1][0].id == 4


async def test_brief_says_what_is_missing(repo):
    # пустое поле модель читает как «не запросил» и идёт его искать — поэтому «нет в базе»
    playground = repo.get(9).brief()
    assert playground == {"id": 9, "name": "Детская площадка", "kind": "детская площадка",
                          "address": "нет в базе", "hours": "нет в базе"}


def test_stemming_matches_word_forms():
    q_ids, _ = sparse.encode_query("пельмени")
    d_ids, _ = sparse.encode_document("Пельмень. кафе", avg_len=3)
    assert set(q_ids) & set(d_ids)


def test_query_noise_words_do_not_reach_lexical_search():
    ids, _ = sparse.encode_query("забыл название ресторана")
    assert ids == []
    ids, _ = sparse.encode_query("ресторан Пирс")
    assert len(ids) == 1


async def test_document_is_expanded_with_dishes_and_needs(repo):
    text = document(repo.get(2))
    assert "пельмени" in text and "русская" in text
    assert "снять наличные" in document(repo.get(7))
