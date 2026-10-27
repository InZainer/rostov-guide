"""Текст документа для индекса: из чего векторная база узнаёт, «о чём» место.

У большинства мест в OSM есть только название и тег вида, поэтому документ
расширяется словарём: тег кухни `russian` превращается в «русская кухня:
пельмени, борщ, блины», а вид `atm` получает «снять наличные». Это и позволяет
найти «Хинкальную» по запросу «пельмени» и банкомат по «снять деньги», хотя
в данных этих слов нет.
"""

from __future__ import annotations

from ..places.kinds import GROUPS, KIND_NEEDS, cuisine_dishes, cuisine_labels, kind_label
from ..places.repository import Place

# Меняется формат документа — меняется версия, и индекс пересобирается при старте.
DOC_VERSION = 4

_GROUP_OF = {kind: group for group, kinds in GROUPS.items() for kind in kinds}


def group_of(kind: str) -> str | None:
    return _GROUP_OF.get(kind)


def document(place: Place) -> str:
    parts = [place.title, kind_label(place.kind)]
    if place.kind in KIND_NEEDS:
        parts.append(KIND_NEEDS[place.kind])
    labels = cuisine_labels(place.cuisine)
    if labels:
        dishes = "; ".join(cuisine_dishes(place.cuisine))
        parts.append(f"кухня: {', '.join(labels)}" + (f" ({dishes})" if dishes else ""))
    if place.brand and place.brand != place.title:
        parts.append(f"сеть {place.brand}")
    if place.takeaway == 1:
        parts.append("еда с собой")
    if place.outdoor_seating == 1:
        parts.append("летняя веранда")
    if place.address:
        parts.append(place.address)
    return ". ".join(parts)
