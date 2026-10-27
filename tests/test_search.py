import json

from rostov_guide.search.store import PlaceIndex


def _index(fake_embedder, tmp_path) -> PlaceIndex:
    return PlaceIndex.open(url="", collection="places", embedder=fake_embedder, meta_path=tmp_path / "meta.json")


async def test_search_before_build_is_refused(fake_embedder, tmp_path, repo):
    index = _index(fake_embedder, tmp_path)
    assert index.status.serving is False
    try:
        await index.search("пельмени")
    except RuntimeError as exc:
        assert "строится" in str(exc)
    else:
        raise AssertionError("поиск без индекса должен отказывать")
    await index.aclose()


async def test_rebuild_then_hybrid_search(fake_embedder, tmp_path, repo):
    index = _index(fake_embedder, tmp_path)
    await index.rebuild(repo)
    assert index.status.state == "ready" and index.status.serving
    assert index.status.done == index.status.total == len(repo)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["embed_model"] == "fake-embed" and meta["count"] == len(repo)

    hits = await index.search("пельмени", limit=3)
    # «Пельмень» — по названию, «ПАРК» — благодаря словарю кухни (russian → пельмени)
    top = {h.place_id: h.found_by for h in hits[:2]}
    assert top == {1: "смысл+слова", 2: "смысл+слова"}
    await index.aclose()


async def test_kind_filter_and_lexical_mode(fake_embedder, tmp_path, repo):
    index = _index(fake_embedder, tmp_path)
    await index.rebuild(repo)
    hits = await index.search("пельмени", kinds={"restaurant"}, limit=5)
    assert all(repo.get(h.place_id).kind == "restaurant" for h in hits)
    lexical = await index.search("суши", mode="lexical", limit=5)
    assert [h.place_id for h in lexical] == [8] and lexical[0].dense is None
    await index.aclose()


async def test_embedded_mode_always_rebuilds_from_cache(fake_embedder, tmp_path, repo):
    index = _index(fake_embedder, tmp_path)
    assert await index.attach(repo) is False        # коллекция в памяти: после старта её нет
    await index.aclose()
