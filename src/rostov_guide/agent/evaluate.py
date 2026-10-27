"""Оценка агента на живой модели: сценарии с эталоном, посчитанным по базе.

    uv run rostov-guide eval-agent --n 3

Каждый сценарий — один или несколько вопросов в одной сессии (так проверяется
и память диалога). Эталон не захардкожен: «сколько круглосуточных аптек» и
«ближайшая аптека к дому» считаются по справочнику без модели. Метрики:
доля пройденных (pass@1 по всем прогонам и pass^n — сценарий прошёл во всех n),
средние токены, шаги модели и время.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..places.repository import PlaceRepository
from ..services import Services
from .factory import APP_NAME, build_runner


def wilson_low(successes: int, n: int, z: float = 1.96) -> float:
    """Нижняя граница 95% интервала Уилсона: «8 из 8» — это не «всегда», а «не меньше 0.68»."""
    if n == 0:
        return 0.0
    p = successes / n
    centre = p + z * z / (2 * n)
    spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return round((centre - spread) / (1 + z * z / n), 3)


def _norm(text: str) -> str:
    return re.sub(r"[«»\"'„“”]", "", (text or "").lower()).replace("ё", "е")


@dataclass
class Turn:
    text: str
    answer: str = ""
    tools: list[str] | None = None


@dataclass
class Case:
    name: str
    questions: list[str]
    check: Callable[[list[Turn]], tuple[bool, str]]


def _contains(expected: str) -> Callable[[list[Turn]], tuple[bool, str]]:
    def check(turns: list[Turn]) -> tuple[bool, str]:
        ok = _norm(expected) in _norm(turns[-1].answer)
        return ok, f"ждём «{expected}» в ответе"
    return check


def build_cases(places: PlaceRepository) -> list[Case]:
    pharmacies_24 = len(places.filter(kinds={"pharmacy"}, only_24_7=True))
    home = places.geocode("Белорусская 44")
    nearest_pharmacy = places.by_distance(places.filter(kinds={"pharmacy"}), home.lat, home.lon)[0][0]
    nearest_24_7, dist_24_7 = places.by_distance(places.filter(kinds={"pharmacy"}, only_24_7=True),
                                                 home.lat, home.lon)[0]
    market = places.geocode("Центральный рынок")
    nearest_atm = places.by_distance(places.filter(kinds={"atm"}), market.lat, market.lon)[0][0] if market else None
    ozon = len(places.filter(kinds={"outpost"}, brand="ozon"))
    shawarma = {_norm(p.title) for p in places.filter(kinds={"fast_food", "cafe", "restaurant"})
                if "шаурм" in _norm(p.title) or "шаверм" in _norm(p.title)
                or (p.cuisine and any(c in p.cuisine for c in ("kebab", "shawarma")))}

    def phone_followup(turns: list[Turn]) -> tuple[bool, str]:
        if nearest_pharmacy.phone:
            digits = re.sub(r"\D", "", nearest_pharmacy.phone)[-7:]
            return digits in re.sub(r"\D", "", turns[-1].answer), f"телефон {nearest_pharmacy.phone}"
        return "не указ" in turns[-1].answer.lower() or "нет" in turns[-1].answer.lower(), "телефона в базе нет"

    def nearest_24_7_check(turns: list[Turn]) -> tuple[bool, str]:
        # у ближайшей круглосуточной имя может быть просто «Аптека», поэтому сверяем расстояние
        answer = turns[-1].answer.replace("\u00a0", " ")
        km = f"{dist_24_7 / 1000:.1f}".replace(".", ",")
        ok = str(dist_24_7) in answer or f"{km} км" in answer
        return ok, f"ждём «{nearest_24_7.title}», {dist_24_7} м"

    def any_shawarma(turns: list[Turn]) -> tuple[bool, str]:
        answer = _norm(turns[-1].answer)
        return any(name in answer for name in shawarma), "хотя бы одно место с шаурмой из базы"

    def off_topic(turns: list[Turn]) -> tuple[bool, str]:
        return not turns[-1].tools, "посторонний вопрос — без вызова инструментов"

    cases = [
        Case("count_24_7", ["Сколько в Ростове круглосуточных аптек?"], _contains(str(pharmacies_24))),
        Case("nearest_by_address", ["Где ближайшая аптека к Белорусской, 44?"], _contains(nearest_pharmacy.title)),
        Case("forgot_name", ["Забыл название кафе — помню только, что там были пельмени"], _contains("Пельмень")),
        Case("memory_followup", ["Где ближайшая аптека к Белорусской, 44?", "А какой у неё телефон?"], phone_followup),
        # найдено на перепроверке: «из них» требует фильтра 24/7 в ближайших, раньше агент зацикливался
        Case("filter_followup", ["Сколько в Ростове круглосуточных аптек?",
                                 "А какая из них ближе всего к Белорусской, 44?"], nearest_24_7_check),
        Case("brand_count", ["Сколько в городе пунктов выдачи Ozon?"], _contains(str(ozon))),
        Case("dish_search", ["Где в Ростове поесть шаурму?"], any_shawarma),
        Case("off_topic", ["Реши уравнение x^2 - 5x + 6 = 0"], off_topic),
    ]
    if nearest_atm:
        cases.append(Case("nearest_by_landmark", ["Где ближайший банкомат к Центральному рынку?"],
                          _contains(nearest_atm.title)))
    return cases


async def _run_case(runner: Runner, case: Case, max_llm_calls: int) -> dict:
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="eval")
    turns: list[Turn] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    started = time.perf_counter()
    error = None
    for question in case.questions:
        turn = Turn(question, tools=[])
        try:
            async for event in runner.run_async(
                user_id="eval", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=question)]),
                run_config=RunConfig(streaming_mode=StreamingMode.NONE, max_llm_calls=max_llm_calls),
            ):
                for part in (event.content.parts if event.content and event.content.parts else []):
                    if part.function_call:
                        turn.tools.append(part.function_call.name)
                    elif part.text and not part.thought and event.author != "user":
                        turn.answer = (turn.answer + "\n" + part.text).strip()
                if event.usage_metadata and event.usage_metadata.prompt_token_count:
                    usage["input_tokens"] += event.usage_metadata.prompt_token_count or 0
                    usage["output_tokens"] += (event.usage_metadata.candidates_token_count or 0) + \
                        (event.usage_metadata.thoughts_token_count or 0)
                    usage["llm_calls"] += 1
        except Exception as exc:                    # noqa: BLE001 — ошибка прогона — это провал сценария
            error = f"{type(exc).__name__}: {exc}"[:300]
        turns.append(turn)
    ok, why = (False, error) if error else case.check(turns)
    return {"case": case.name, "ok": ok, "why": why, "seconds": round(time.perf_counter() - started, 1),
            **usage, "turns": [{"q": t.text, "tools": t.tools, "answer": t.answer[:600]} for t in turns]}


async def evaluate_agent(services: Services, repeats: int, out_dir: Path) -> dict:
    runner = build_runner(services)
    runner.session_service = InMemorySessionService()      # оценка не засоряет базу сессий
    cases = build_cases(services.places)
    runs: list[dict] = []
    for attempt in range(repeats):
        for case in cases:
            result = await _run_case(runner, case, services.settings.max_llm_calls)
            result["attempt"] = attempt + 1
            runs.append(result)
            print(f"  [{attempt + 1}/{repeats}] {'✓' if result['ok'] else '✗'} {case.name:20} "
                  f"{result['seconds']:5.1f} с, шагов {result['llm_calls']}  {'' if result['ok'] else result['why']}")
    await runner.close()

    by_case: dict[str, list[bool]] = {}
    for r in runs:
        by_case.setdefault(r["case"], []).append(r["ok"])
    n = len(runs)
    summary = {
        "model": services.settings.llm_model, "cases": len(cases), "repeats": repeats,
        "pass_at_1": round(sum(r["ok"] for r in runs) / n, 3),
        "pass_at_1_wilson_low": wilson_low(sum(r["ok"] for r in runs), n),
        "pass_all_n": round(sum(all(v) for v in by_case.values()) / len(by_case), 3),
        "pass_all_n_wilson_low": wilson_low(sum(all(v) for v in by_case.values()), len(by_case)),
        "avg_seconds": round(sum(r["seconds"] for r in runs) / n, 1),
        "avg_llm_calls": round(sum(r["llm_calls"] for r in runs) / n, 2),
        "avg_input_tokens": round(sum(r["input_tokens"] for r in runs) / n),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in runs) / n),
        "per_case": {k: f"{sum(v)}/{len(v)}" for k, v in by_case.items()},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"agent-eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["saved_to"] = str(path)
    return summary
