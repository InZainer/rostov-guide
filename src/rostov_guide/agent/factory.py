"""Сборка агента на Google ADK: модель, инструменты, плагины, память, сжатие контекста.

- Модель — через LiteLLM к OpenAI-совместимому серверу (по умолчанию LM Studio).
- Память диалога — сессии ADK в SQLite (`SqliteSessionService`): чат пользователя
  и есть сессия ADK, история переживает перезапуск сервера.
- Учёт контекста — `EventsCompactionConfig`: когда промпт перерастает порог,
  старые события сворачиваются в сводку той же моделью, последние остаются как есть.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from ..config import Settings
from ..services import Services
from .plugins import JournalPlugin, RepeatGuard, ToolErrorsAsObservations, UserTurnGuard
from .prompt import SUMMARY_PROMPT, build_instruction
from .tools import make_tools

APP_NAME = "rostov_guide"


def build_model(settings: Settings, *, reasoning_effort: str = "") -> LiteLlm:
    extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    return LiteLlm(
        model=f"openai/{settings.llm_model}",
        api_base=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        num_retries=2,          # сервер модели может ответить 429/503 под нагрузкой
        timeout=120,
        **extra,
    )


def build_agent(services: Services, model: LiteLlm) -> LlmAgent:
    return LlmAgent(
        name="rostov_guide",
        description="Справочник по местам Ростова-на-Дону",
        model=model,
        instruction=build_instruction,
        tools=make_tools(services),
    )


def build_runner(services: Services) -> Runner:
    settings = services.settings
    model = build_model(settings)
    app = App(
        name=APP_NAME,
        root_agent=build_agent(services, model),
        plugins=[ToolErrorsAsObservations(), RepeatGuard(), UserTurnGuard(), JournalPlugin(settings.runs_dir)],
        events_compaction_config=EventsCompactionConfig(
            token_threshold=settings.context_compact_tokens,
            event_retention_size=settings.context_keep_events,
            # сводке рассуждение не нужно: без него сжатие занимает секунды, а не минуты
            summarizer=LlmEventSummarizer(
                llm=build_model(settings, reasoning_effort=settings.summary_reasoning_effort),
                prompt_template=SUMMARY_PROMPT),
        ),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Runner(app=app, session_service=SqliteSessionService(str(settings.sessions_db)))
