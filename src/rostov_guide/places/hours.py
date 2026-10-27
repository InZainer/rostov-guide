"""Часы работы в формате OSM: «открыто ли сейчас» и до скольки.

Разбирается подмножество синтаксиса, которое покрывает почти весь снимок Ростова:
`24/7`, `Mo-Fr 09:00-18:00; Sa 10:00-16:00; Su off`, интервалы через запятую,
ночные интервалы (`22:00-02:00`), правило без дней (`08:00-22:00` — каждый день).
Всё, что сложнее (праздники, недели месяца), честно даёт «неизвестно».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ROSTOV_TZ = timezone(timedelta(hours=3))
DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
DAY_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_TIME = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\+?$")
_DAY_RANGE = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")

Schedule = dict[int, list[tuple[int, int]]]   # день недели → интервалы в минутах от полуночи


@dataclass(frozen=True)
class HoursStatus:
    open: bool | None       # None — часы не указаны или не разобраны
    label: str              # «открыто до 21:00», «закрыто, откроется в пт 09:00»


def _days(spec: str) -> list[int] | None:
    out: list[int] = []
    for chunk in spec.split(","):
        m = _DAY_RANGE.match(chunk.strip())
        if not m:
            return None
        start = DAYS.index(m.group(1))
        end = DAYS.index(m.group(2)) if m.group(2) else start
        span = range(start, end + 1) if end >= start else [*range(start, 7), *range(0, end + 1)]
        out.extend(span)
    return out


def _intervals(spec: str) -> list[tuple[int, int]] | None:
    out = []
    for chunk in spec.split(","):
        m = _TIME.match(chunk.strip())
        if not m:
            return None
        h1, m1, h2, m2 = map(int, m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end <= start:            # через полночь: 22:00-02:00 или 00:00-00:00
            end += 24 * 60
        out.append((start, end))
    return out


def parse(text: str | None) -> Schedule | None:
    if not text:
        return None
    text = text.strip()
    if text == "24/7":
        return {d: [(0, 24 * 60)] for d in range(7)}
    schedule: Schedule = {}
    for rule in (r.strip() for r in text.split(";") if r.strip()):
        parts = rule.split(None, 1)
        if _DAY_RANGE.match(parts[0].split(",")[0]):
            days = _days(parts[0])
            rest = parts[1].strip() if len(parts) > 1 else ""
        else:
            days, rest = list(range(7)), rule
        if days is None:
            return None
        if rest in ("off", "closed"):
            for d in days:
                schedule[d] = []
            continue
        if rest == "24/7" or rest == "00:00-24:00":
            spans: list[tuple[int, int]] | None = [(0, 24 * 60)]
        else:
            spans = _intervals(rest)
        if spans is None:
            return None
        for d in days:              # более позднее правило перекрывает раннее
            schedule[d] = spans
    return schedule


def _fmt(minutes: int) -> str:
    if minutes == 24 * 60:
        return "24:00"                  # «открыто до 24:00», а не «до 00:00»
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def status(text: str | None, now: datetime | None = None) -> HoursStatus:
    if not text:
        return HoursStatus(None, "часы не указаны")
    if text.strip() == "24/7":
        return HoursStatus(True, "круглосуточно")
    schedule = parse(text)
    if schedule is None:
        return HoursStatus(None, text)
    now = (now or datetime.now(ROSTOV_TZ)).astimezone(ROSTOV_TZ)
    day, minute = now.weekday(), now.hour * 60 + now.minute

    # открыто ли: сегодняшние интервалы и хвосты вчерашних ночных
    for start, end in schedule.get(day, []):
        if start <= minute < end:
            return HoursStatus(True, "круглосуточно" if end - start >= 24 * 60 else f"открыто до {_fmt(end)}")
    for start, end in schedule.get((day - 1) % 7, []):
        if end > 24 * 60 and minute < end - 24 * 60:
            return HoursStatus(True, f"открыто до {_fmt(end)}")

    # когда откроется: ближайший старт в пределах недели
    for shift in range(0, 8):
        d = (day + shift) % 7
        starts = sorted(s for s, _ in schedule.get(d, []) if shift > 0 or s > minute)
        if starts:
            when = _fmt(starts[0]) if shift == 0 else (
                f"завтра в {_fmt(starts[0])}" if shift == 1 else f"в {DAY_RU[d]} {_fmt(starts[0])}")
            return HoursStatus(False, f"закрыто, откроется {when}" if shift else f"закрыто, откроется в {when}")
    if not any(schedule.values()):
        return HoursStatus(False, "закрыто")
    return HoursStatus(None, text)
