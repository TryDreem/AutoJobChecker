"""Оценка релевантности поста от 0 до 100.

Профиль, под который настроены веса:
    Python backend в первую очередь, JS/fullstack во вторую;
    стажировки и junior-позиции; удалёнка из любой точки мира
    либо очно/гибрид в Познани; чем свежее, тем лучше.

Веса вынесены константами наверх — их можно крутить, не трогая логику.
Каждое слагаемое пишет причину, поэтому в карточке всегда видно,
почему у поста именно такая оценка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.enums import PostKind, Role, SalaryPeriod, Seniority, WorkMode
from app.pipeline import keywords as kw
from app.pipeline.extractors import Extracted

# Базовый уровень намеренно низкий: 100 баллов должны набираться только полным
# совпадением (мой стек + мой грейд + удалёнка + свежесть), а не «всем подряд».
BASE = 15

# --- Специализация ----------------------------------------------------------
ROLE_WEIGHTS = {
    Role.PYTHON_BACKEND: 30,
    Role.FULLSTACK: 22,
    Role.JS_NODE: 18,
    Role.BACKEND: 16,
    Role.FRONTEND: 8,
    Role.DATA_ML: 4,
    Role.DEVOPS: -10,
    Role.MOBILE: -15,
    Role.QA: -15,
    Role.OTHER_ROLE: -10,
}

# --- Грейд ------------------------------------------------------------------
SENIORITY_WEIGHTS = {
    Seniority.INTERN: 15,
    Seniority.JUNIOR: 17,
    Seniority.MIDDLE: 5,
    Seniority.SENIOR: -18,
    Seniority.LEAD: -28,
    # Не указан — не награждаем: чаще всего за этим прячется middle+.
    Seniority.UNKNOWN: 0,
}

# --- Формат работы ----------------------------------------------------------
WORK_MODE_WEIGHTS = {
    WorkMode.REMOTE: 14,
    WorkMode.HYBRID: -6,   # подходит, только если это Познань — доплата ниже
    WorkMode.ONSITE: -22,
    WorkMode.UNKNOWN: 0,
}

# Города, в которые я готов ездить. Гибрид и офис здесь — нормальный вариант.
HOME_LOCATIONS = {"Познань"}
NEARBY_LOCATIONS = {"Польша"}

# Работать очно/гибридно здесь физически невозможно — жёсткий отказ, а не
# штраф очков (в отличие от остальных гео-ограничений ниже).
RUSSIA_LOCATIONS = {"Москва", "Санкт-Петербург", "Россия"}

STACK_BONUS_PER_ITEM = 3
STACK_BONUS_CAP = 12
FOREIGN_LANG_PENALTY = -18
NO_STACK_PENALTY = -14
GEO_RESTRICTION_PENALTY = -26
# Требуемый опыт. Порог входа для джуна — года два; дальше шансы падают резко,
# поэтому штраф растёт нелинейно, а не линейно.
EXPERIENCE_PENALTY = {3: -12, 4: -22, 5: -30, 6: -38}
EXPERIENCE_PENALTY_MAX = -45
LOW_EXPERIENCE_BONUS = 6      # «опыт от года» и меньше — прямое попадание

# Метрики качества заказа: чем меньше конкурентов и надёжнее клиент, тем лучше.
FEW_PROPOSALS_BONUS = 12      # до 5 откликов
SOME_PROPOSALS_BONUS = 6      # до 15 откликов
MANY_PROPOSALS_PENALTY = -8   # 50+ откликов — заявка почти наверняка утонет
PAYMENT_VERIFIED_BONUS = 8
PAYMENT_UNVERIFIED_PENALTY = -10
GOOD_CLIENT_RATING_BONUS = 6
BAD_CLIENT_RATING_PENALTY = -12

SALARY_KNOWN_BONUS = 5
CONTACTS_BONUS = 3
SHORT_TEXT_PENALTY = -10
MIN_USEFUL_LENGTH = 90

# --- Свежесть ---------------------------------------------------------------
FRESHNESS_STEPS = [
    (6, 8, "опубликовано менее 6 часов назад"),
    (24, 6, "опубликовано сегодня"),
    (72, 2, "опубликовано на этой неделе"),
    (168, -4, "старше недели"),
    (336, -12, "старше двух недель"),
]
STALE_PENALTY = -22


@dataclass(slots=True)
class ScoreResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    rejected: bool = False
    reject_reason: str = ""

    def add(self, points: int, why: str) -> None:
        if points:
            sign = "+" if points > 0 else ""
            self.reasons.append(f"{sign}{points} {why}")
            self.score += points


def _freshness(posted_at: datetime) -> tuple[int, str]:
    now = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    hours = (now - posted_at).total_seconds() / 3600
    # Посты «из будущего» — обычно кривая дата у источника, не награждаем.
    if hours < -1:
        return 0, "дата публикации в будущем"
    for limit, points, label in FRESHNESS_STEPS:
        if hours <= limit:
            return points, label
    return STALE_PENALTY, "устаревший пост"


def score_post(
    data: Extracted,
    *,
    posted_at: datetime,
    text_length: int,
    normalized_text: str,
    source_priority: int = 5,
    keywords: list[str] | None = None,
    stopwords: list[str] | None = None,
) -> ScoreResult:
    """Считает релевантность. Отсев мусора происходит здесь же, до начисления баллов."""
    result = ScoreResult(score=BASE)

    # --- Жёсткие отсечки: считать баллы дальше бессмысленно ----------------
    if data.kind is PostKind.RESUME:
        return ScoreResult(0, ["это резюме, а не вакансия"], True, "резюме")
    if data.kind is PostKind.OTHER:
        why = data.notes[0] if data.notes else "не вакансия и не заказ"
        return ScoreResult(0, [why], True, why)

    # Россия — исключена полностью: любое упоминание в тексте (город, страна,
    # требование резидентства) отсекает пост, независимо от формата работы.
    if data.russia_restricted or (set(data.locations) & RUSSIA_LOCATIONS):
        why = "связано с Россией"
        return ScoreResult(0, [why], True, why)

    for word in stopwords or []:
        if word.strip() and word.strip().lower() in normalized_text:
            return ScoreResult(0, [f"стоп-слово «{word}»"], True, f"стоп-слово «{word}»")

    # --- Специализация -----------------------------------------------------
    role_points = max((ROLE_WEIGHTS.get(Role(r), 0) for r in data.roles), default=0)
    top_role = max(data.roles, key=lambda r: ROLE_WEIGHTS.get(Role(r), 0), default="")
    if top_role:
        result.add(role_points, f"роль: {Role(top_role).label}")

    # --- Технологии --------------------------------------------------------
    stack_set = set(data.stack)
    relevant = stack_set & (kw.PYTHON_STACK | kw.JS_STACK)
    if relevant:
        bonus = min(len(relevant) * STACK_BONUS_PER_ITEM, STACK_BONUS_CAP)
        result.add(bonus, f"знакомый стек: {', '.join(sorted(relevant)[:4])}")

    foreign = stack_set & kw.FOREIGN_LANGS
    # Чужой язык штрафуем, только если своего в посте нет вовсе.
    if foreign and not relevant:
        result.add(FOREIGN_LANG_PENALTY, f"другой язык: {', '.join(sorted(foreign)[:3])}")

    # Вакансия разработчика почти всегда называет хоть одну технологию.
    # Если не названа ни одна — это чаще всего вообще не про разработку.
    if not stack_set:
        result.add(NO_STACK_PENALTY, "ни одной технологии в тексте")

    # --- Грейд -------------------------------------------------------------
    result.add(SENIORITY_WEIGHTS.get(data.seniority, 0), f"грейд: {data.seniority.label}")

    # --- География и формат ------------------------------------------------
    locations = set(data.locations)
    at_home = bool(locations & HOME_LOCATIONS)
    nearby = bool(locations & NEARBY_LOCATIONS)

    if data.work_mode is WorkMode.REMOTE:
        result.add(WORK_MODE_WEIGHTS[WorkMode.REMOTE], "удалённая работа")
    elif at_home:
        # Познань — единственное место, ради которого стоит выходить из дома.
        result.add(14, f"Познань, формат: {data.work_mode.label.lower()}")
    elif data.work_mode in (WorkMode.HYBRID, WorkMode.ONSITE):
        result.add(WORK_MODE_WEIGHTS[data.work_mode], f"формат: {data.work_mode.label.lower()}")

    if at_home:
        result.add(8, "локация: Познань")
    elif nearby:
        result.add(4, "локация: Польша")

    # «US only» и подобное режет шансы, если ограничение не про мою географию.
    if data.geo_restricted and not (at_home or nearby):
        result.add(GEO_RESTRICTION_PENALTY, "ограничение по стране проживания")

    # --- Деньги ------------------------------------------------------------
    if data.salary.min or data.salary.max:
        result.add(SALARY_KNOWN_BONUS, f"указана оплата: {data.salary.raw}")
        usd = data.salary.usd_month
        if usd and data.salary.period is not SalaryPeriod.PROJECT:
            if usd >= 3000:
                result.add(6, "высокая вилка")
            elif usd < 400:
                result.add(-6, "низкая вилка")

    # --- Требуемый опыт ----------------------------------------------------
    # Самый честный фильтр «не для джуна»: слово Senior пишут не всегда,
    # а «3+ years» в требованиях стоит почти везде.
    if data.required_years is not None:
        if data.required_years <= 1:
            result.add(LOW_EXPERIENCE_BONUS, "опыт до года — мой уровень")
        elif data.required_years >= 3:
            penalty = EXPERIENCE_PENALTY.get(data.required_years, EXPERIENCE_PENALTY_MAX)
            result.add(penalty, f"требуют опыт от {data.required_years} лет")

    # --- Качество заказа (Upwork и площадки, которые отдают эти данные) -----
    if data.proposals_max is not None:
        if data.proposals_max <= 5:
            result.add(FEW_PROPOSALS_BONUS, f"мало откликов: {data.proposals_label}")
        elif data.proposals_max <= 15:
            result.add(SOME_PROPOSALS_BONUS, f"умеренно откликов: {data.proposals_label}")
        elif data.proposals_max >= 50:
            result.add(MANY_PROPOSALS_PENALTY, f"много откликов: {data.proposals_label}")

    if data.client_payment_verified is True:
        result.add(PAYMENT_VERIFIED_BONUS, "оплата у клиента подтверждена")
    elif data.client_payment_verified is False:
        result.add(PAYMENT_UNVERIFIED_PENALTY, "оплата у клиента не подтверждена")

    # Рейтинг оцениваем только при реальных отзывах: 5.0 по одному отзыву
    # и 5.0 по сотне — разные вещи, а у новых клиентов рейтинга просто нет.
    if data.client_rating is not None and (data.client_reviews or 0) >= 3:
        if data.client_rating >= 4.5:
            result.add(GOOD_CLIENT_RATING_BONUS, f"рейтинг клиента {data.client_rating:.1f}")
        elif data.client_rating < 3.5:
            result.add(BAD_CLIENT_RATING_PENALTY, f"низкий рейтинг клиента {data.client_rating:.1f}")

    # --- Тип поста ---------------------------------------------------------
    if data.kind is PostKind.INTERNSHIP:
        result.add(8, "стажировка — целевой формат")
    elif data.kind is PostKind.GIG:
        result.add(4, "разовый заказ")

    # --- Качество поста ----------------------------------------------------
    if text_length < MIN_USEFUL_LENGTH:
        result.add(SHORT_TEXT_PENALTY, "слишком короткое описание")
    if data.contacts.get("telegram") or data.contacts.get("email"):
        result.add(CONTACTS_BONUS, "есть прямой контакт")

    # --- Свежесть ----------------------------------------------------------
    points, label = _freshness(posted_at)
    result.add(points, label)

    # --- Персональные ключевые слова ---------------------------------------
    hits = [w for w in (keywords or []) if w.strip() and w.strip().lower() in normalized_text]
    if hits:
        result.add(min(len(hits) * 5, 15), f"мои ключевые слова: {', '.join(hits[:3])}")

    # --- Доверие к источнику ------------------------------------------------
    # priority 1..10, где 5 — нейтрально.
    result.add(source_priority - 5, "приоритет источника")

    result.score = max(0, min(100, result.score))
    return result
