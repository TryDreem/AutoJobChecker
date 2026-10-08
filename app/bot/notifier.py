"""Рассылка новых вакансий в личку.

Три правила, чтобы бот не стал раздражать:
  * тихие часы — ночью ничего не приходит, накопленное уедет в утреннюю сводку;
  * лимит в час — при всплеске присылается верхушка, остальное списком;
  * режим дайджеста — если хочется получать всё одним сообщением.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot.formatters import format_compact, format_digest, format_vacancy
from app.bot.services import feed_page, mark_sent, unsent_for_user
from app.config import get_settings
from app.core.db import session_scope
from app.core.models import UserProfile, UserVacancy
from app.pipeline import alerts

log = logging.getLogger(__name__)

# Пауза между сообщениями: Telegram не любит частую отправку в один чат.
SEND_DELAY = 0.4

# Пометка в UserVacancy.note — по ней считается суточный лимит алертов.
_ALERT_NOTE = "alert"


def in_quiet_hours(profile: UserProfile, moment: datetime | None = None) -> bool:
    """Тихие часы могут пересекать полночь — учитываем оба случая."""
    tz = ZoneInfo(get_settings().timezone)
    now = (moment or datetime.now(timezone.utc)).astimezone(tz)
    hour = now.hour

    start, end = profile.quiet_from, profile.quiet_to
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _send(bot: Bot, chat_id: int, text: str, markup=None) -> bool:
    """Отправка с обработкой ограничений Telegram."""
    try:
        await bot.send_message(
            chat_id, text, reply_markup=markup, disable_web_page_preview=True
        )
        return True
    except TelegramRetryAfter as exc:
        log.warning("Telegram просит подождать %s сек", exc.retry_after)
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(
                chat_id, text, reply_markup=markup, disable_web_page_preview=True
            )
            return True
        except Exception as inner:  # noqa: BLE001
            log.warning("Повторная отправка не удалась: %s", inner)
            return False
    except TelegramForbiddenError:
        log.warning("Пользователь %s заблокировал бота", chat_id)
        return False
    except Exception as exc:  # noqa: BLE001 - рассылка не должна ронять сбор
        log.warning("Не удалось отправить сообщение: %s", exc)
        return False


async def _alerts_sent_today(session, profile: UserProfile) -> int:
    """Сколько срочных уведомлений уже ушло за сегодня."""
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = await session.execute(
        select(UserVacancy.id).where(
            UserVacancy.user_id == profile.id,
            UserVacancy.sent_at.isnot(None),
            UserVacancy.sent_at >= since,
            UserVacancy.note == _ALERT_NOTE,
        )
    )
    return len(list(rows))


async def _send_alerts(bot: Bot, session, profile: UserProfile, items) -> tuple[int, set[int]]:
    """Отправляет срочные уведомления и возвращает (сколько, какие id).

    Такие сообщения идут отдельно от общей рассылки и, в отличие от неё, не
    ждут окончания тихих часов: смысл алерта в том, чтобы успеть откликнуться
    первым, а к утру заказ уже соберёт полсотни заявок.
    """
    if not profile.alert_enabled:
        return 0, set()

    rules = alerts.rules_for(profile)
    quota = int(rules.get("daily_limit", 5)) - await _alerts_sent_today(session, profile)
    if quota <= 0:
        return 0, set()

    sent = 0
    fired: set[int] = set()
    for vacancy, source in items:
        if quota <= 0:
            break
        verdict = alerts.evaluate(vacancy, profile)
        if not verdict.fire:
            continue

        text = (
            "🔥 <b>Звёзды сошлись — откликайся сейчас</b>\n"
            f"<i>{verdict.summary}</i>\n\n"
            + format_vacancy(vacancy, source)
        )
        if await _send(bot, profile.tg_id, text, kb.vacancy_actions(vacancy.id, vacancy.url)):
            sent += 1
            quota -= 1
            fired.add(vacancy.id)
        await asyncio.sleep(SEND_DELAY)

    return sent, fired


async def notify_new(bot: Bot, vacancy_ids: list[int]) -> int:
    """Рассылает свежие вакансии всем подписчикам. Возвращает число сообщений."""
    if not vacancy_ids:
        return 0

    sent_total = 0
    async with session_scope() as session:
        profiles = list((await session.execute(select(UserProfile))).scalars())

        for profile in profiles:
            if not profile.notify_enabled:
                continue

            items = await unsent_for_user(session, profile, vacancy_ids)
            if not items:
                continue

            # Исключительные варианты уходят сразу и мимо тихих часов.
            alert_count, fired = await _send_alerts(bot, session, profile, items)
            if alert_count:
                sent_total += alert_count
                await mark_sent(session, profile, list(fired), note=_ALERT_NOTE)
                items = [(v, s) for v, s in items if v.id not in fired]
                if not items:
                    continue

            # Ночью не тревожим — утренняя задача пришлёт сводку.
            if in_quiet_hours(profile):
                log.info("Тихие часы у %s: отложено %s постов", profile.tg_id, len(items))
                continue

            if profile.digest_mode:
                ok = await _send(bot, profile.tg_id, format_digest(items))
                if ok:
                    await mark_sent(session, profile, [v.id for v, _ in items])
                    sent_total += 1
                continue

            limit = max(1, profile.max_per_hour)
            head, tail = items[:limit], items[limit:]

            delivered: list[int] = []
            for vacancy, source in head:
                ok = await _send(
                    bot,
                    profile.tg_id,
                    format_vacancy(vacancy, source),
                    kb.vacancy_actions(vacancy.id, vacancy.url),
                )
                if ok:
                    delivered.append(vacancy.id)
                    sent_total += 1
                await asyncio.sleep(SEND_DELAY)

            # Хвост не теряем, но и не спамим — отдаём компактным списком.
            if tail:
                lines = [f"➕ <b>Ещё {len(tail)} подходящих</b>", ""]
                lines += [format_compact(v, i) for i, (v, _) in enumerate(tail, 1)]
                lines += ["", "<i>Открой «📥 Лента», чтобы посмотреть подробно.</i>"]
                if await _send(bot, profile.tg_id, "\n".join(lines)):
                    delivered.extend(v.id for v, _ in tail)
                    sent_total += 1

            if delivered:
                await mark_sent(session, profile, delivered)

    return sent_total


async def send_morning_digest(bot: Bot) -> int:
    """Утренняя сводка: то, что накопилось за тихие часы."""
    sent = 0
    async with session_scope() as session:
        profiles = list((await session.execute(select(UserProfile))).scalars())

        for profile in profiles:
            if not profile.notify_enabled or in_quiet_hours(profile):
                continue

            items, _ = await feed_page(session, profile, offset=0, limit=20, days=1)
            # Из ленты за сутки берём только то, что ещё не отправляли.
            fresh = await unsent_for_user(session, profile, [v.id for v, _ in items])
            if not fresh:
                continue

            text = "☀️ <b>Доброе утро! Что появилось за ночь:</b>\n\n" + format_digest(fresh)
            if await _send(bot, profile.tg_id, text):
                await mark_sent(session, profile, [v.id for v, _ in fresh])
                sent += 1

    return sent
