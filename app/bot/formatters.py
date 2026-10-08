"""Сборка красивых карточек вакансий для Telegram.

Задача карточки — чтобы за три секунды было понятно: моё или нет, сколько
платят, куда писать. Поэтому важное вынесено в шапку, а текст поста идёт
последним и обрезается.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.core.enums import GigCategory, PostKind, Role, SalaryPeriod, Seniority, WorkMode
from app.core.models import Source, Vacancy
from app.pipeline.text import truncate

# Лимит Telegram — 4096 символов; оставляем запас на разметку.
MAX_MESSAGE = 3800
MAX_BODY = 1500

CURRENCY_SIGNS = {
    "USD": "$", "EUR": "€", "PLN": "zł", "RUB": "₽",
    "UAH": "₴", "GBP": "£", "KZT": "₸",
}


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _local(moment: datetime) -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz)


def format_when(moment: datetime) -> str:
    """«Сегодня 14:20», «Вчера 09:05» или полная дата — так проще ориентироваться."""
    local = _local(moment)
    now = _local(datetime.now(timezone.utc))
    delta_days = (now.date() - local.date()).days

    if delta_days == 0:
        minutes = int((now - local).total_seconds() // 60)
        if minutes < 60:
            return f"только что" if minutes < 5 else f"{minutes} мин назад"
        return f"сегодня {local:%H:%M}"
    if delta_days == 1:
        return f"вчера {local:%H:%M}"
    if delta_days < 7:
        return f"{delta_days} дн назад, {local:%H:%M}"
    return f"{local:%d.%m.%Y %H:%M}"


def _number(value: float) -> str:
    """12500.0 -> «12 500», 25.5 -> «25.5»."""
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def format_salary(vacancy: Vacancy) -> str:
    if not (vacancy.salary_min or vacancy.salary_max):
        return ""

    sign = CURRENCY_SIGNS.get(vacancy.salary_currency, vacancy.salary_currency)
    if vacancy.salary_min and vacancy.salary_max and vacancy.salary_min != vacancy.salary_max:
        amount = f"{_number(vacancy.salary_min)} – {_number(vacancy.salary_max)}"
    else:
        value = vacancy.salary_max or vacancy.salary_min
        prefix = "от " if vacancy.salary_min and not vacancy.salary_max else ""
        amount = f"{prefix}{_number(value)}"

    period = SalaryPeriod(vacancy.salary_period).label
    line = f"{amount} {sign} {period}".strip()

    # Пересчёт в доллары помогает сравнивать офферы в разных валютах.
    if vacancy.salary_usd_month and vacancy.salary_currency not in ("USD", ""):
        line += f"  <i>(~${_number(vacancy.salary_usd_month)}/мес)</i>"
    return line


def score_bar(score: int) -> str:
    """Визуальная шкала — глазом быстрее, чем читать число."""
    filled = round(score / 20)
    return "🟩" * filled + "⬜️" * (5 - filled)


def _headline_emoji(vacancy: Vacancy) -> str:
    for role in vacancy.roles or []:
        try:
            return Role(role).emoji
        except ValueError:
            continue
    return PostKind(vacancy.kind).emoji


def format_vacancy(
    vacancy: Vacancy,
    source: Source | None = None,
    *,
    full: bool = False,
    index: str = "",
) -> str:
    """Полная карточка вакансии."""
    kind = PostKind(vacancy.kind)
    mode = WorkMode(vacancy.work_mode)
    seniority = Seniority(vacancy.seniority)

    lines: list[str] = []

    header = f"{_headline_emoji(vacancy)} <b>{esc(vacancy.title)}</b>"
    if index:
        header = f"{index}  {header}"
    lines.append(header)
    if vacancy.company:
        lines.append(f"<i>{esc(vacancy.company)}</i>")
    lines.append("")

    # --- Ключевые факты ----------------------------------------------------
    facts = [f"{kind.emoji} {kind.label}", f"{mode.emoji} {mode.label}"]
    # У заказа направление важнее грейда: по нему сразу видно, стоит ли открывать.
    if kind is PostKind.GIG:
        category = GigCategory(vacancy.gig_category or "other")
        if category is not GigCategory.OTHER:
            facts.append(f"{category.emoji} {category.label}")
    if seniority is not Seniority.UNKNOWN:
        facts.append(f"🎓 {seniority.label}")
    lines.append(" · ".join(facts))

    salary = format_salary(vacancy)
    lines.append(f"💰 {salary}" if salary else "💰 <i>оплата не указана</i>")

    # Требуемый опыт показываем всегда, когда он назван: для джуна это первое,
    # на что стоит смотреть.
    if vacancy.required_years:
        lines.append(f"📅 Требуют опыт: <b>{vacancy.required_years}+</b>")

    # Метрики площадки (Upwork): по ним видно шансы, а не только соответствие.
    metrics: list[str] = []
    if vacancy.proposals_max is not None:
        low, high = vacancy.proposals_min, vacancy.proposals_max
        shown = f"{low}–{high}" if low is not None and low != high else str(high)
        # Мало откликов — главный сигнал «успей первым».
        icon = "🟢" if high <= 5 else "🟡" if high <= 15 else "🔴"
        metrics.append(f"{icon} откликов: {shown}")
    if vacancy.client_payment_verified is True:
        metrics.append("✅ оплата подтверждена")
    elif vacancy.client_payment_verified is False:
        metrics.append("⚠️ оплата не подтверждена")
    if vacancy.client_rating is not None and (vacancy.client_reviews or 0) > 0:
        metrics.append(f"⭐️ {vacancy.client_rating:.1f} ({vacancy.client_reviews})")
    if vacancy.client_spent:
        metrics.append(f"💵 потрачено ${int(vacancy.client_spent):,}".replace(",", " "))
    if metrics:
        lines.append(" · ".join(metrics))

    if vacancy.stack:
        lines.append(f"🛠 {esc(', '.join(vacancy.stack[:8]))}")
    if vacancy.locations:
        lines.append(f"📍 {esc(', '.join(vacancy.locations[:4]))}")

    lines.append(f"⭐️ {score_bar(vacancy.score)} <b>{vacancy.score}%</b>")
    lines.append("")

    # --- Откуда и когда ----------------------------------------------------
    lines.append(f"🕐 {format_when(vacancy.posted_at)}")
    if source is not None:
        name = esc(source.display_name)
        if vacancy.url:
            lines.append(f'📡 <a href="{esc(vacancy.url)}">{name}</a>')
        else:
            lines.append(f"📡 {name}")

    contacts = vacancy.contacts or {}
    telegram = contacts.get("telegram") or []
    emails = contacts.get("email") or []
    if telegram:
        lines.append(f"👤 {esc(' · '.join(telegram[:3]))}")
    if emails:
        lines.append(f"✉️ {esc(' · '.join(emails[:2]))}")

    # --- Текст поста -------------------------------------------------------
    body = vacancy.clean_text or vacancy.raw_text
    if body:
        limit = MAX_BODY if not full else MAX_MESSAGE - len("\n".join(lines)) - 200
        lines.append("")
        lines.append("─────────────")
        lines.append(esc(truncate(body, max(300, limit))))

    if full and vacancy.score_reasons:
        lines.append("")
        lines.append("<i>Почему такая оценка:</i>")
        lines.append(f"<i>{esc('; '.join(vacancy.score_reasons[:8]))}</i>")

    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "…"


def format_compact(vacancy: Vacancy, index: int) -> str:
    """Одна строка для списков и дайджестов."""
    salary = format_salary(vacancy)
    money = f" · 💰 {salary.split('<i>')[0].strip()}" if salary else ""
    mode = WorkMode(vacancy.work_mode)
    return (
        f"<b>{index}.</b> {_headline_emoji(vacancy)} {esc(truncate(vacancy.title, 60))}\n"
        f"    {vacancy.score}% · {mode.emoji} {mode.label}{money} · {format_when(vacancy.posted_at)}"
    )


def format_digest(items: list[tuple[Vacancy, Source | None]]) -> str:
    """Сводка за период — чтобы не заваливать лентой по одному сообщению."""
    if not items:
        return "📭 За это время ничего подходящего не нашлось."

    lines = [f"📬 <b>Новых подходящих: {len(items)}</b>", ""]
    for number, (vacancy, _) in enumerate(items, 1):
        lines.append(format_compact(vacancy, number))
        lines.append("")
    lines.append("<i>Открой «📥 Лента», чтобы посмотреть подробно.</i>")

    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "…"
