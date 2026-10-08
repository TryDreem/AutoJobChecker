"""ORM-модели. SQLite по умолчанию, но схема нейтральна — Postgres подхватится сменой DATABASE_URL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.enums import (
    Employment,
    PostKind,
    SalaryPeriod,
    Seniority,
    SourceKind,
    UserStatus,
    WorkMode,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    """Источник данных: Telegram-канал, биржа, RSS-лента.

    Один ряд = одна «труба», из которой льются посты. Состояние обхода
    (last_external_id, last_fetch_at) хранится здесь, чтобы после рестарта
    не перечитывать историю заново.
    """

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "identifier", name="uq_source_kind_ident"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(String(32), index=True)
    # Для Telegram — @username или -100…, для бирж — ключ провайдера, для RSS — URL.
    identifier: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    # Произвольные настройки конкретного источника (query, теги, лимиты).
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # verified=False, пока Telethon не подтвердил, что канал существует и читается.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)

    # Telegram: id последнего обработанного сообщения. Биржи: их внешний курсор.
    last_external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    # Накопительная статистика — по ней бот показывает, какие каналы реально полезны.
    posts_seen: Mapped[int] = mapped_column(Integer, default=0)
    posts_kept: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    vacancies: Mapped[list["Vacancy"]] = relationship(back_populates="source")

    @property
    def display_name(self) -> str:
        return self.title or self.identifier

    @property
    def keep_rate(self) -> float:
        return (self.posts_kept / self.posts_seen * 100) if self.posts_seen else 0.0


class Vacancy(Base):
    """Обработанный пост: вакансия, заказ или стажировка.

    Сырой текст сохраняем всегда — чтобы можно было переоценить старые посты
    после правки правил, не перекачивая источники.
    """

    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_vacancy_source_external"),
        Index("ix_vacancy_feed", "score", "posted_at"),
        Index("ix_vacancy_dedup", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128))

    # --- Оригинал ----------------------------------------------------------
    url: Mapped[str] = mapped_column(String(1024), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    clean_text: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(8), default="")

    # --- Кто разместил -----------------------------------------------------
    company: Mapped[str] = mapped_column(String(256), default="")
    author: Mapped[str] = mapped_column(String(256), default="")
    # {"telegram": ["@hr"], "email": [...], "links": [...]}
    contacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # --- Классификация -----------------------------------------------------
    kind: Mapped[PostKind] = mapped_column(String(16), default=PostKind.OTHER, index=True)
    # Направление разового заказа. Для вакансий не заполняется — там роль важнее.
    gig_category: Mapped[str] = mapped_column(String(16), default="other", index=True)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    stack: Mapped[list[str]] = mapped_column(JSON, default=list)
    seniority: Mapped[Seniority] = mapped_column(String(16), default=Seniority.UNKNOWN, index=True)
    employment: Mapped[Employment] = mapped_column(String(16), default=Employment.UNKNOWN)
    work_mode: Mapped[WorkMode] = mapped_column(String(16), default=WorkMode.UNKNOWN, index=True)
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)

    # --- Деньги ------------------------------------------------------------
    salary_min: Mapped[float | None] = mapped_column(Float, default=None)
    salary_max: Mapped[float | None] = mapped_column(Float, default=None)
    salary_currency: Mapped[str] = mapped_column(String(8), default="")
    salary_period: Mapped[SalaryPeriod] = mapped_column(String(16), default=SalaryPeriod.UNKNOWN)
    salary_raw: Mapped[str] = mapped_column(String(256), default="")
    # Нормализованная оценка $/мес — единственный способ честно сравнивать офферы.
    salary_usd_month: Mapped[float | None] = mapped_column(Float, default=None)

    # --- Требования и конкуренция ------------------------------------------
    # Сколько лет опыта требует вакансия. Честнее грейда: «Senior» пишут не
    # всегда, а «3+ years» в требованиях стоит почти везде.
    required_years: Mapped[int | None] = mapped_column(Integer, default=None, index=True)

    # Метрики качества заказа. Заполняются источниками, которые их знают
    # (в первую очередь Upwork); у остальных остаются пустыми.
    proposals_min: Mapped[int | None] = mapped_column(Integer, default=None)
    proposals_max: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    client_payment_verified: Mapped[bool | None] = mapped_column(Boolean, default=None, index=True)
    client_rating: Mapped[float | None] = mapped_column(Float, default=None)
    client_reviews: Mapped[int | None] = mapped_column(Integer, default=None)
    client_spent: Mapped[float | None] = mapped_column(Float, default=None)
    client_hires: Mapped[int | None] = mapped_column(Integer, default=None)
    client_country: Mapped[str] = mapped_column(String(64), default="")
    # entry / intermediate / expert — как это называет сам Upwork.
    experience_level: Mapped[str] = mapped_column(String(16), default="")

    # --- Оценка ------------------------------------------------------------
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    llm_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_verdict: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # --- Дедупликация ------------------------------------------------------
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    simhash: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    duplicate_of: Mapped[int | None] = mapped_column(
        ForeignKey("vacancies.id"), default=None, index=True
    )

    # --- Время -------------------------------------------------------------
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="vacancies")

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


class UserProfile(Base):
    """Настройки получателя. Пока это ты один, но схема уже многопользовательская."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")

    # --- Что присылать -----------------------------------------------------
    min_score: Mapped[int] = mapped_column(Integer, default=55)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    seniorities: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, default=list)
    kinds: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Направления фриланс-заказов. Пусто = показывать все.
    gig_categories: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Потолок требуемого опыта. 0 = не ограничивать. Вакансии, где опыт не
    # назван, проходят всегда — иначе отсеялось бы большинство объявлений.
    max_required_years: Mapped[int] = mapped_column(Integer, default=0)

    # Фильтры качества заказа (в первую очередь Upwork). Держим в JSON:
    # набор полей у площадок разный и будет меняться, а заводить под каждое
    # отдельную колонку и миграцию — избыточно.
    # {"payment_verified": bool, "max_proposals": int, "min_client_rating": float,
    #  "allow_new_clients": bool, "experience_levels": [str]}
    quality_filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Условия срабатывания срочного уведомления «звёзды сошлись».
    # Хранится отдельно от обычных фильтров: там «что показывать в ленте»,
    # здесь — «из-за чего будить немедленно».
    alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    alert_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    min_salary_usd: Mapped[int] = mapped_column(Integer, default=0)
    # Посты без указанной зарплаты: показывать или нет.
    allow_no_salary: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    stopwords: Mapped[list[str]] = mapped_column(JSON, default=list)

    # --- Как присылать -----------------------------------------------------
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_from: Mapped[int] = mapped_column(Integer, default=23)   # час начала тишины
    quiet_to: Mapped[int] = mapped_column(Integer, default=8)      # час конца тишины
    digest_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    max_per_hour: Mapped[int] = mapped_column(Integer, default=15)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserVacancy(Base):
    """Связь пользователь↔вакансия: что отправлено, что в избранном, что скрыто."""

    __tablename__ = "user_vacancies"
    __table_args__ = (UniqueConstraint("user_id", "vacancy_id", name="uq_user_vacancy"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)

    status: Mapped[UserStatus] = mapped_column(String(16), default=UserStatus.NEW, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    vacancy: Mapped[Vacancy] = relationship()


class LlmUsage(Base):
    """Счётчик вызовов LLM по дням — страховка от неожиданного счёта."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
