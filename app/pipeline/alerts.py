"""Срочные уведомления: «звёзды сошлись».

Обычная лента отвечает на вопрос «что вообще подходит». Здесь другой вопрос —
«ради чего стоит бросить дела и откликнуться прямо сейчас».

Разница принципиальная. В ленту пост попадает, если он в целом релевантен.
Алерт срабатывает, только когда совпало ВСЁ сразу: и стек, и уровень, и
деньги, и — главное — пока мало откликов. На Upwork заказ с пятью откликами
и он же через три часа с полусотней — это два разных заказа по шансам, и
первым делом важна именно скорость.

Правила намеренно жёсткие: смысл алерта теряется, если он срабатывает часто.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import PostKind, Seniority
from app.core.models import UserProfile, Vacancy

log = logging.getLogger(__name__)

# Значения по умолчанию. Пользователь может переопределить любое через
# UserProfile.alert_rules, не трогая код.
DEFAULT_RULES: dict[str, Any] = {
    # Насколько пост должен быть релевантен по обычной шкале.
    "min_score": 80,
    # Потолок откликов. None у поста (площадка не сообщила) — не препятствие.
    "max_proposals": 15,
    # Требовать подтверждённую оплату у клиента.
    "require_payment_verified": False,
    # Потолок требуемого опыта в годах.
    "max_required_years": 2,
    # Минимальная оплата в долларах за месяц; 0 — не проверять.
    "min_salary_usd": 0,
    # Грейды, которые точно не подходят.
    "block_seniorities": [str(Seniority.SENIOR), str(Seniority.LEAD)],
    # Сколько алертов максимум за сутки — защита от спама самому себе.
    "daily_limit": 5,
}


def _years_word(n: int) -> str:
    """Склонение: 1 год, 2 года, 5 лет."""
    if 11 <= n % 100 <= 14:
        return "лет"
    return {1: "год", 2: "года", 3: "года", 4: "года"}.get(n % 10, "лет")


@dataclass(slots=True)
class AlertVerdict:
    """Решение по одному посту: слать срочно или нет."""

    fire: bool
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return " · ".join(self.reasons)


def rules_for(profile: UserProfile) -> dict[str, Any]:
    """Правила пользователя поверх значений по умолчанию."""
    rules = dict(DEFAULT_RULES)
    rules.update(profile.alert_rules or {})
    return rules


def evaluate(vacancy: Vacancy, profile: UserProfile) -> AlertVerdict:
    """Проверяет, тянет ли пост на срочное уведомление.

    Возвращает не только вердикт, но и причины: их видно в самом уведомлении,
    чтобы было понятно, почему бот счёл вариант исключительным.
    """
    rules = rules_for(profile)
    reasons: list[str] = []
    blockers: list[str] = []

    # --- Релевантность ------------------------------------------------------
    min_score = int(rules.get("min_score", 80))
    if vacancy.score < min_score:
        blockers.append(f"оценка {vacancy.score} < {min_score}")
    else:
        reasons.append(f"релевантность {vacancy.score}%")

    # --- Конкуренция --------------------------------------------------------
    # Главный фактор для фриланс-бирж: важно успеть, пока откликов мало.
    max_proposals = rules.get("max_proposals")
    if max_proposals is not None and vacancy.proposals_max is not None:
        if vacancy.proposals_max > int(max_proposals):
            blockers.append(f"откликов уже {vacancy.proposals_max}")
        else:
            reasons.append(f"мало откликов ({vacancy.proposals_max})")

    # --- Надёжность клиента -------------------------------------------------
    if rules.get("require_payment_verified") and vacancy.client_payment_verified is False:
        blockers.append("оплата у клиента не подтверждена")
    elif vacancy.client_payment_verified:
        reasons.append("оплата подтверждена")

    if vacancy.client_rating is not None and (vacancy.client_reviews or 0) >= 3:
        if vacancy.client_rating >= 4.5:
            reasons.append(f"рейтинг клиента {vacancy.client_rating:.1f}")

    # --- Требуемый опыт -----------------------------------------------------
    max_years = rules.get("max_required_years")
    if max_years is not None and vacancy.required_years is not None:
        if vacancy.required_years > int(max_years):
            blockers.append(
                f"требуют опыт от {vacancy.required_years} {_years_word(vacancy.required_years)}"
            )
        else:
            reasons.append(
                f"опыт от {vacancy.required_years} {_years_word(vacancy.required_years)} — проходит"
            )

    # --- Грейд --------------------------------------------------------------
    blocked = {str(x) for x in rules.get("block_seniorities") or []}
    if str(vacancy.seniority) in blocked:
        blockers.append(f"грейд {Seniority(vacancy.seniority).label}")

    # --- Деньги -------------------------------------------------------------
    min_salary = int(rules.get("min_salary_usd", 0) or 0)
    if min_salary and vacancy.salary_usd_month is not None:
        if vacancy.salary_usd_month < min_salary:
            blockers.append(f"оплата ниже ${min_salary}")
        else:
            reasons.append(f"оплата ~${int(vacancy.salary_usd_month)}/мес")

    # --- Резюме и мусор сюда попасть не должны в принципе --------------------
    if vacancy.kind in (PostKind.RESUME, PostKind.OTHER):
        blockers.append("не вакансия и не заказ")

    return AlertVerdict(fire=not blockers, reasons=reasons, blockers=blockers)


def describe_rules(profile: UserProfile) -> str:
    """Текст текущих правил для настроек в боте."""
    rules = rules_for(profile)
    lines = [
        f"• релевантность от <b>{rules['min_score']}%</b>",
        f"• откликов не больше <b>{rules['max_proposals']}</b>",
        f"• требуемый опыт до <b>{rules['max_required_years']}</b> лет",
    ]
    if rules.get("require_payment_verified"):
        lines.append("• только с подтверждённой оплатой")
    if rules.get("min_salary_usd"):
        lines.append(f"• оплата от <b>${rules['min_salary_usd']}</b>/мес")
    lines.append(f"• не больше <b>{rules['daily_limit']}</b> уведомлений в сутки")
    return "\n".join(lines)
