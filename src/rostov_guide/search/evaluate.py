"""Оценка поиска: насколько гибрид лучше «только смысла» и «только слов».

Разметка — правилами, а не списком id: запрос «пельмени» считается найденным
верно, если у места кухня русская/узбекская/грузинская или в названии есть
«пельмен», «варенич», «хинкал», «манты». Правило недосчитывает (хорошее место
с пустыми тегами засчитается промахом), поэтому числа — нижняя оценка, но для
сравнения режимов между собой этого достаточно.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..places.repository import Place, PlaceRepository
from .store import PlaceIndex


def _name(*needles: str) -> Callable[[Place], bool]:
    return lambda p: any(n in (p.name or "").lower() for n in needles)


def _cuisine(*tags: str) -> Callable[[Place], bool]:
    return lambda p: any(t in (p.cuisine or "").lower().split(";") for t in tags)


def _kind(*kinds: str) -> Callable[[Place], bool]:
    return lambda p: p.kind in kinds


def _any(*rules: Callable[[Place], bool]) -> Callable[[Place], bool]:
    return lambda p: any(r(p) for r in rules)


CASES: list[tuple[str, Callable[[Place], bool]]] = [
    ("пельмени", _any(_name("пельмен", "варенич", "хинкал", "манты"), _cuisine("russian", "uzbek", "georgian"))),
    ("суши и роллы", _any(_cuisine("sushi", "japanese"), _name("суши", "ролл", "sushi", "япон"))),
    ("пицца", _any(_cuisine("pizza", "italian", "italian_pizza"), _name("пицц", "pizza"))),
    ("шаурма", _any(_cuisine("kebab", "shawarma"), _name("шаур", "шаве", "донер", "kebab", "кебаб"))),
    ("выпить кофе", _any(_cuisine("coffee_shop"), _name("кофе", "coffee", "кофейн"), _kind("cafe"))),
    ("грузинская кухня", _any(_cuisine("georgian", "кавказская"), _name("хинкал", "хачапур", "грузи", "тбилис"))),
    ("бургер", _any(_cuisine("burger", "american"), _name("бургер", "burger"))),
    ("попить пива", _any(_kind("pub", "bar", "alcohol", "beverages"), _name("пив", "beer", "паб", "pub"))),
    ("плов", _any(_cuisine("uzbek", "oriental"), _name("плов", "чайхан", "узбек"))),
    ("блины", _any(_cuisine("pancake", "crepe", "savory_pancakes"), _name("блин"))),
    ("торт на день рождения", _any(_kind("confectionery", "pastry", "bakery"), _cuisine("cake"), _name("торт"))),
    ("купить лекарства", _kind("pharmacy")),
    ("погулять с ребёнком", _kind("playground", "park")),
    ("постричься", _kind("hairdresser", "beauty")),
    ("отправить посылку", _kind("post_office", "outpost")),
    ("снять наличные", _kind("atm", "bank")),
    ("заправить машину", _kind("fuel")),
    ("букет цветов", _kind("florist")),
    ("рыба и раки", _any(_cuisine("fish", "seafood", "regional"), _kind("seafood"), _name("рыб", "раки", "рак"))),
    ("поесть мороженое", _any(_kind("ice_cream"), _name("морожен", "джелат"))),
]


@dataclass
class ModeScore:
    p_at_5: float
    hit_at_1: float
    ms: float


async def evaluate(index: PlaceIndex, repo: PlaceRepository, out_dir: Path | None = None) -> dict:
    modes = {"смысл": "dense", "слова": "lexical", "гибрид": "hybrid"}
    report: dict[str, dict] = {}
    per_query: list[dict] = []
    for mode, engine in modes.items():
        p5 = h1 = 0.0
        spent = 0.0
        for query, relevant in CASES:
            started = time.perf_counter()
            hits = await index.search(query, limit=5, mode=engine)
            spent += time.perf_counter() - started
            top = [repo.get(h.place_id) for h in hits[:5]]
            good = [p is not None and relevant(p) for p in top]
            p5 += sum(good) / 5
            h1 += float(bool(good and good[0]))
            per_query.append({"mode": mode, "query": query, "good": sum(good),
                              "top": [p.title if p else None for p in top]})
        n = len(CASES)
        report[mode] = ModeScore(round(p5 / n, 3), round(h1 / n, 3), round(1000 * spent / n, 1)).__dict__
    result = {"embed_model": index.embedder.model, "queries": len(CASES), "modes": report}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"search-eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(result | {"per_query": per_query}, ensure_ascii=False, indent=2), encoding="utf-8")
        result["saved_to"] = str(path)
    return result
