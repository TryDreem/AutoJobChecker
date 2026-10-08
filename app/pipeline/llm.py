"""LLM-классификатор для пограничных постов.

Правила справляются с явными случаями: «ищу работу» — резюме, «Senior Java,
релокация в Берлин» — не моё. Спорное остаётся в середине шкалы: короткий пост
без ключевых слов, вакансия с необычной формулировкой, заказ, где стек указан
описательно. Такие посты и уходят в модель.

Работает через Groq (бесплатный облачный API, Llama 3.3 70B) — быстро и без
затрат. Экономия дополнительно заложена в саму конструкцию:
  * вызывается только для постов в узком коридоре баллов;
  * посты уходят пачкой — один запрос на десяток;
  * дневной лимит запросов есть на случай, если у Groq сменится тарификация.

Без GROQ_API_KEY модуль просто отключается — бот продолжает работать
на правилах.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.config import get_settings
from app.core.db import session_scope
from app.core.models import LlmUsage
from app.pipeline.text import truncate

log = logging.getLogger(__name__)

# Сколько постов отправлять за один запрос.
BATCH_SIZE = 10
# Обрезка поста: для решения «моё/не моё» хватает начала.
MAX_POST_CHARS = 1200

SYSTEM_PROMPT = """\
Ты — фильтр вакансий для конкретного разработчика. Твоя работа: по тексту поста \
из Telegram-канала или с биржи решить, стоит ли показывать его этому человеку.

ПРОФИЛЬ РАЗРАБОТЧИКА
- Основное: Python backend (Django, FastAPI, Flask, aiogram, PostgreSQL, Docker).
- Дополнительно: JavaScript/TypeScript, Node.js, React, fullstack.
- Уровень: стажировка, junior, начальный middle. Senior и Lead — не подходят.
- Формат: удалённая работа из любой точки мира ЛИБО офис/гибрид в Познани (Польша).
  Офис в любом другом городе — не подходит, переезд не рассматривается.
- Интересует и постоянная работа, и разовые заказы, и стажировки.

ЧТО ОТСЕИВАТЬ БЕЗУСЛОВНО
- Резюме: человек сам ищет работу («ищу работу», «open to work», описание своего опыта).
- Реклама курсов, менторства, наставничества, «обучу с нуля», набор на обучение.
- Крипта, ставки, казино, «заработок в интернете», финансовые пирамиды.
- Вакансии не по разработке: продажи, маркетинг, дизайн, поддержка, HR.
- Вакансии на других стеках без Python/JS: Java, PHP, C#, C++, 1C, Go, Ruby.
- Требование опыта от 4 лет и выше, позиции Senior/Lead/Head/Architect.
- Жёсткая привязка к стране или городу, кроме Польши: «only US», «только Москва».

КАК СТАВИТЬ ОЦЕНКУ (0-100)
- 85-100: Python backend, junior или стажировка, удалённо или Познань.
- 65-84: подходящий стек и уровень, но что-то одно неидеально.
- 45-64: смежная роль (fullstack, JS) или уровень чуть выше нужного.
- 20-44: слабое совпадение, но это всё же настоящая вакансия или заказ.
- 0-19: не подходит совсем либо мусор из списка выше.

Верни СТРОГО валидный JSON и ничего больше — без markdown, без пояснений вокруг.
Формат ответа (results — массив, по одному объекту на каждый пост из запроса):

{"results": [
  {"id": "<id из запроса>", "kind": "job|gig|internship|resume|other",
   "score": <0-100>, "seniority": "intern|junior|middle|senior|lead|unknown",
   "remote": <true|false>, "reason": "краткая фраза на русском"}
]}

Поле reason — одна короткая фраза на русском языке, объясняющая решение по существу \
(«Python backend junior, удалённо» или «резюме соискателя»). Верни результат для \
каждого поста из запроса — ни один id не должен потеряться."""


@dataclass(slots=True)
class LlmRequest:
    """Пост, отправляемый на проверку."""

    id: str
    title: str
    text: str
    source: str
    rule_score: int


@dataclass(slots=True)
class LlmVerdict:
    kind: str
    score: int
    seniority: str
    remote: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "score": self.score,
            "seniority": self.seniority,
            "remote": self.remote,
            "reason": self.reason,
        }


class LlmClassifier:
    """Обёртка над Groq API (Llama 3.3 70B). Молчаливо выключается без ключа."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = None
        self._disabled_reason = ""

        if not self.settings.llm_ready:
            self._disabled_reason = "нет GROQ_API_KEY или LLM выключен в .env"
            return

        try:
            from groq import AsyncGroq
        except ImportError:
            self._disabled_reason = "не установлен пакет groq"
            log.warning("LLM отключён: %s", self._disabled_reason)
            return

        self._client = AsyncGroq(api_key=self.settings.groq_api_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def status(self) -> str:
        if self.enabled:
            return f"включён ({self.settings.llm_model}, Groq)"
        return f"выключен — {self._disabled_reason}"

    # --- Учёт расхода -------------------------------------------------------
    async def _quota_left(self) -> int:
        today = date.today().isoformat()
        async with session_scope() as session:
            row = await session.scalar(select(LlmUsage).where(LlmUsage.day == today))
            used = row.calls if row else 0
        return max(0, self.settings.llm_daily_limit - used)

    async def _record_usage(self, calls: int, usage) -> None:
        today = date.today().isoformat()
        async with session_scope() as session:
            row = await session.scalar(select(LlmUsage).where(LlmUsage.day == today))
            if row is None:
                row = LlmUsage(day=today)
                session.add(row)
            row.calls += calls
            if usage is not None:
                row.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                row.output_tokens += getattr(usage, "completion_tokens", 0) or 0

    # --- Основной вызов -----------------------------------------------------
    async def classify(self, items: list[LlmRequest]) -> dict[str, LlmVerdict]:
        """Классифицирует посты. При любой проблеме возвращает пустой словарь,
        и вызывающая сторона остаётся на оценке по правилам."""
        if not self.enabled or not items:
            return {}

        quota = await self._quota_left()
        if quota <= 0:
            log.info("Дневной лимит LLM исчерпан — работаем на правилах")
            return {}

        verdicts: dict[str, LlmVerdict] = {}
        for start in range(0, min(len(items), quota * BATCH_SIZE), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            try:
                verdicts.update(await self._classify_batch(batch))
            except Exception as exc:  # noqa: BLE001 - падение LLM не должно ронять сбор
                log.warning("LLM-классификация не удалась: %s", exc)
                break
        return verdicts

    async def _classify_batch(self, batch: list[LlmRequest]) -> dict[str, LlmVerdict]:
        payload = [
            {
                "id": item.id,
                "source": item.source,
                "title": truncate(item.title, 200),
                "text": truncate(item.text, MAX_POST_CHARS),
            }
            for item in batch
        ]

        user_text = (
            "Оцени каждый пост и верни результат для всех без исключения.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
        )

        response = await self._client.chat.completions.create(
            model=self.settings.llm_model,
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )

        await self._record_usage(1, getattr(response, "usage", None))

        choice = response.choices[0] if response.choices else None
        raw = choice.message.content if choice and choice.message else ""
        if not raw:
            return {}

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("LLM вернул неразбираемый JSON")
            return {}

        result: dict[str, LlmVerdict] = {}
        known = {item.id for item in batch}
        for entry in data.get("results", []):
            post_id = str(entry.get("id", ""))
            if post_id not in known:
                continue
            result[post_id] = LlmVerdict(
                kind=str(entry.get("kind", "other")),
                score=max(0, min(100, int(entry.get("score", 0)))),
                seniority=str(entry.get("seniority", "unknown")),
                remote=bool(entry.get("remote", False)),
                reason=str(entry.get("reason", ""))[:200],
            )
        return result


_classifier: LlmClassifier | None = None


def get_classifier() -> LlmClassifier:
    global _classifier
    if _classifier is None:
        _classifier = LlmClassifier()
    return _classifier
