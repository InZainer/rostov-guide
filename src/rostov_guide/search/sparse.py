"""Разреженные векторы BM25 для лексической половины гибридного поиска.

Вес термина в документе — TF-часть BM25 (k1, b, длина документа), а IDF
считает сам Qdrant (`Modifier.IDF`) по всей коллекции. Запрос — множество
основ с весом 1. Основы — snowball-стемминг: «пельмени» и «Пельмень»
сводятся к одному «пельмен».
"""

from __future__ import annotations

import re
import zlib
from collections import Counter
from functools import lru_cache

import snowballstemmer

K1, B = 1.2, 0.75
_TOKEN = re.compile(r"[а-яёa-z0-9]+")
_RU = snowballstemmer.stemmer("russian")
_EN = snowballstemmer.stemmer("english")
_STOP = frozenset("и в во на с со к ко по о об от до из за для у не а но или же ли the and of".split())
# Слова запроса, которые описывают намерение, а не место: «забыл название ресторана,
# где можно выпить» совпадает по словам с десятком заведений по имени «Ресторан».
# Из лексической половины они выкидываются, смысловая половина их по-прежнему видит.
_QUERY_NOISE_WORDS = ("где найти найди хочу хотел забыл помню название названием вроде типа место места "
                      "заведение там были был было есть какой какая какое который ресторан ресторана "
                      "кафе поесть выпить попить можно подскажи посоветуй ростов ростове рядом недалеко")


@lru_cache(maxsize=50_000)
def _stem(token: str) -> str:
    if re.match(r"[a-z]", token):
        return _EN.stemWord(token)
    return _RU.stemWord(token.replace("ё", "е"))


def terms(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def term_id(term: str) -> int:
    """Номер измерения: стабильный хеш основы (crc32 не зависит от запуска)."""
    return zlib.crc32(term.encode("utf-8")) & 0x7FFFFFFF


def encode_document(text: str, avg_len: float) -> tuple[list[int], list[float]]:
    counts = Counter(terms(text))
    length = sum(counts.values()) or 1
    weights: dict[int, float] = {}
    for term, tf in counts.items():
        idx = term_id(term)
        w = tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg_len))
        weights[idx] = max(weights.get(idx, 0.0), w)
    return list(weights), list(weights.values())


def encode_query(text: str) -> tuple[list[int], list[float]]:
    ids = sorted({term_id(t) for t in terms(text) if t not in _QUERY_NOISE})
    return ids, [1.0] * len(ids)


_QUERY_NOISE = frozenset(_stem(w) for w in _QUERY_NOISE_WORDS.split())


def meaningful_terms(text: str) -> list[str]:
    """Основы запроса без слов намерения: то, что запрос на самом деле уточняет."""
    return [t for t in terms(text) if t not in _QUERY_NOISE]
