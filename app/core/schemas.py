"""DTO между источниками и пайплайном.

Источник обязан вернуть RawPost — и ничего не знать о том, как его будут
обрабатывать. Пайплайн обязан принять RawPost — и ничего не знать о том,
откуда он взялся. Это и есть точка расширения: добавить Upwork = научиться
отдавать RawPost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class RawPost:
    """Сырой пост из любого источника, приведённый к общему виду."""

    external_id: str
    text: str
    posted_at: datetime

    url: str = ""
    title: str = ""
    company: str = ""
    author: str = ""

    # Поля ниже источник заполняет, только если знает их достоверно
    # (у бирж они приходят структурировано). Пустые — допишет экстрактор.
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""
    locations: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    is_remote: bool | None = None
    employment_hint: str = ""
    seniority_hint: str = ""

    # Метрики качества заказа. Их отдают площадки вроде Upwork; из текста
    # такое не вытащить, поэтому источник передаёт их напрямую.
    proposals_min: int | None = None
    proposals_max: int | None = None
    client_payment_verified: bool | None = None
    client_rating: float | None = None
    client_reviews: int | None = None
    client_spent: float | None = None
    client_hires: int | None = None
    client_country: str = ""
    experience_level: str = ""

    # Всё, что не влезло в общую схему, но может пригодиться при отладке.
    extra: dict[str, Any] = field(default_factory=dict)

    def searchable(self) -> str:
        """Текст, по которому работают экстракторы и скоринг."""
        parts = [self.title, self.company, self.text, " ".join(self.tags)]
        return "\n".join(p for p in parts if p)
