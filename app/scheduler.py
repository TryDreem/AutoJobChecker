"""Фоновые задачи: обход источников, рассылка, обслуживание.

Telegram и биржи обходятся по отдельным расписаниям: каналы дают свежие посты
и опрашиваются часто, биржи обновляются медленно и опрашиваются реже.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from app.config import get_settings
from app.core.db import session_scope
from app.core.enums import SourceKind
from app.core.models import Source, UserVacancy, Vacancy
from app.pipeline.processor import process_source
from app.sources import manager

log = logging.getLogger(__name__)

# Сколько дней держим посты. Вакансия месячной давности почти всегда закрыта.
RETENTION_DAYS = 45

# Один обход за раз: параллельные сборы дерутся за лимиты Telegram и за базу.
_scan_lock = asyncio.Lock()


@dataclass(slots=True)
class ScanReport:
    sources: int = 0
    fetched: int = 0
    rejected: int = 0
    duplicates: int = 0
    llm_checked: int = 0
    saved: int = 0
    errors: list[str] = field(default_factory=list)
    new_ids: list[int] = field(default_factory=list)


async def run_scan(
    *, kinds: list[SourceKind] | None = None, reason: str = "по расписанию"
) -> ScanReport:
    """Обходит включённые источники и сохраняет всё подходящее."""
    report = ScanReport()

    if _scan_lock.locked():
        log.info("Обход уже идёт — пропускаю запуск (%s)", reason)
        return report

    async with _scan_lock:
        log.info("Старт обхода (%s)", reason)

        async with session_scope() as session:
            query = select(Source).where(Source.enabled.is_(True))
            if kinds:
                query = query.where(Source.kind.in_(kinds))
            records = list(
                (await session.execute(query.order_by(Source.priority.desc(), Source.id)))
                .scalars()
            )

            for record in records:
                stats = await process_source(session, record)
                report.sources += 1
                report.fetched += stats.fetched
                report.rejected += stats.rejected
                report.duplicates += stats.duplicates
                report.llm_checked += stats.llm_checked
                report.saved += stats.saved
                report.new_ids.extend(stats.new_ids)
                if stats.error:
                    report.errors.append(f"{record.display_name}: {stats.error}")

        log.info(
            "Обход завершён (%s): источников %s, постов %s, сохранено %s, ошибок %s",
            reason, report.sources, report.fetched, report.saved, len(report.errors),
        )
    return report


async def scan_and_notify(bot: Bot, kinds: list[SourceKind] | None = None) -> None:
    """Обход + рассылка. Это и есть основной рабочий цикл бота."""
    from app.bot.notifier import notify_new

    report = await run_scan(kinds=kinds)
    if report.new_ids:
        sent = await notify_new(bot, report.new_ids)
        log.info("Разослано сообщений: %s", sent)


async def cleanup_old() -> None:
    """Чистит устаревшие записи, чтобы база не росла бесконечно."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    async with session_scope() as session:
        stale = list(
            (
                await session.execute(
                    select(Vacancy.id).where(Vacancy.posted_at < cutoff)
                )
            ).scalars()
        )
        if not stale:
            return

        # Избранное и отклики оставляем — их пользователь отметил осознанно.
        keep = set(
            (
                await session.execute(
                    select(UserVacancy.vacancy_id).where(
                        UserVacancy.vacancy_id.in_(stale),
                        UserVacancy.status.in_(["favorite", "applied"]),
                    )
                )
            ).scalars()
        )
        removable = [vid for vid in stale if vid not in keep]
        if not removable:
            return

        await session.execute(
            delete(UserVacancy).where(UserVacancy.vacancy_id.in_(removable))
        )
        await session.execute(delete(Vacancy).where(Vacancy.id.in_(removable)))
        log.info("Удалено устаревших постов: %s", len(removable))


async def health_check() -> None:
    """Раз в сутки проверяет источники и отключает мёртвые."""
    async with session_scope() as session:
        results = await manager.verify_all(session, only_enabled=True)
    dead = [r for r in results if not r.ok]
    if dead:
        log.warning("Не отвечают источники: %s", ", ".join(r.name for r in dead[:10]))


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Собирает расписание фоновых задач."""
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    scheduler.add_job(
        scan_and_notify,
        trigger=IntervalTrigger(minutes=settings.telegram_poll_minutes),
        kwargs={"bot": bot, "kinds": [SourceKind.TELEGRAM]},
        id="scan_telegram",
        name="Обход Telegram-каналов",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        scan_and_notify,
        trigger=IntervalTrigger(minutes=settings.jobboard_poll_minutes),
        kwargs={
            "bot": bot,
            "kinds": [SourceKind.JOBBOARD, SourceKind.RSS, SourceKind.UPWORK],
        },
        id="scan_boards",
        name="Обход бирж и лент",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        _morning_digest,
        trigger=CronTrigger(hour=9, minute=0),
        kwargs={"bot": bot},
        id="morning_digest",
        name="Утренняя сводка",
    )

    scheduler.add_job(
        health_check,
        trigger=CronTrigger(hour=4, minute=30),
        id="health_check",
        name="Проверка источников",
    )

    scheduler.add_job(
        cleanup_old,
        trigger=CronTrigger(hour=5, minute=0),
        id="cleanup",
        name="Чистка старых постов",
    )

    return scheduler


async def _morning_digest(bot: Bot) -> None:
    from app.bot.notifier import send_morning_digest

    sent = await send_morning_digest(bot)
    log.info("Утренняя сводка отправлена: %s", sent)
