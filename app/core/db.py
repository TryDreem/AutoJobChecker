"""Подключение к БД и фабрика сессий."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import BASE_DIR, get_settings
from app.core.models import Base

log = logging.getLogger(__name__)

_settings = get_settings()

# Относительный sqlite-путь резолвим от корня проекта, иначе БД «переезжает»
# вслед за текущей рабочей директорией.
_url = _settings.database_url
if _url.startswith("sqlite+aiosqlite:///") and not _url.startswith("sqlite+aiosqlite:////"):
    rel = _url.removeprefix("sqlite+aiosqlite:///")
    abs_path = (BASE_DIR / rel).resolve()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    _url = f"sqlite+aiosqlite:///{abs_path}"

engine = create_async_engine(_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if _url.startswith("sqlite"):
    # По умолчанию SQLite бросает "database is locked" сразу, если параллельно
    # пишет другое соединение (например, main.py уже запущен, а рядом
    # выполняется python -m app.tools.selfcheck). WAL позволяет читать и
    # писать одновременно, а busy_timeout — ждать освобождения блокировки
    # вместо мгновенного отказа.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


# Колонки, добавленные уже после первого релиза. create_all() умеет создавать
# только отсутствующие таблицы и молча игнорирует новые поля в существующих,
# поэтому их дописываем руками. Полноценный Alembic для одной SQLite-базы
# на одного пользователя — избыточен.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("vacancies", "gig_category", "VARCHAR(16) DEFAULT 'other'"),
    ("users", "gig_categories", "JSON DEFAULT '[]'"),
    # Метрики качества заказа и требуемый опыт.
    ("vacancies", "required_years", "INTEGER"),
    ("vacancies", "proposals_min", "INTEGER"),
    ("vacancies", "proposals_max", "INTEGER"),
    ("vacancies", "client_payment_verified", "BOOLEAN"),
    ("vacancies", "client_rating", "FLOAT"),
    ("vacancies", "client_reviews", "INTEGER"),
    ("vacancies", "client_spent", "FLOAT"),
    ("vacancies", "client_hires", "INTEGER"),
    ("vacancies", "client_country", "VARCHAR(64) DEFAULT ''"),
    ("vacancies", "experience_level", "VARCHAR(16) DEFAULT ''"),
    ("users", "max_required_years", "INTEGER DEFAULT 0"),
    ("users", "quality_filters", "JSON DEFAULT '{}'"),
    ("users", "alert_enabled", "BOOLEAN DEFAULT 1"),
    ("users", "alert_rules", "JSON DEFAULT '{}'"),
]


async def _apply_migrations(conn) -> None:
    """Дописывает недостающие колонки в уже существующие таблицы."""
    from sqlalchemy import text

    for table, column, ddl in _ADDED_COLUMNS:
        exists = await conn.execute(text(f"PRAGMA table_info({table})"))
        names = {row[1] for row in exists}
        if not names:
            continue  # таблицы ещё нет — её создаст create_all
        if column in names:
            continue
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        log.info("Миграция: в %s добавлена колонка %s", table, column)


async def init_db() -> None:
    """Создаёт таблицы и дописывает недостающие колонки."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if _url.startswith("sqlite"):
            await _apply_migrations(conn)
    log.info("База готова: %s", _url)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сессия с автокоммитом и откатом при ошибке."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
