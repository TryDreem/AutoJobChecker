"""Единый клиент Telethon на весь процесс.

Клиент один намеренно: каждое подключение к MTProto — это отдельная сессия,
а Telegram считает лимиты на аккаунт. Плюс общий кэш entity живёт внутри
клиента, и повторные обращения к каналу не тратят запросы.
"""

from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from app.config import get_settings

log = logging.getLogger(__name__)

_client: TelegramClient | None = None
_lock = asyncio.Lock()

# Ограничитель одновременных обращений: параллельно дёргать десятки каналов —
# верный способ поймать FloodWait на ровном месте.
_semaphore = asyncio.Semaphore(3)


async def get_client() -> TelegramClient:
    """Возвращает подключённый клиент, создавая его при первом обращении."""
    global _client
    async with _lock:
        if _client is not None and _client.is_connected():
            return _client

        settings = get_settings()
        # Готовая строка сессии (TG_SESSION_STRING) имеет приоритет над файлом:
        # так подключается аккаунт, для которого сессия уже получена не через
        # наш python -m app.tools.login, а где-то ещё (тот путь по-прежнему
        # рабочий и ничего в нём не менялось).
        session = (
            StringSession(settings.tg_session_string)
            if settings.use_string_session
            else str(settings.session_path)
        )
        client = TelegramClient(
            session,
            settings.tg_api_id,
            settings.tg_api_hash,
            # Telethon сам поспит, если Telegram просит подождать меньше минуты.
            flood_sleep_threshold=60,
            # Ретраи на обрывах — на длинных прогонах это обычное дело.
            connection_retries=5,
            retry_delay=3,
            request_retries=3,
        )
        await client.connect()

        if not await client.is_user_authorized():
            hint = (
                "Строка в TG_SESSION_STRING недействительна или устарела."
                if settings.use_string_session
                else "Запусти `python -m app.tools.login` и введи код из Telegram."
            )
            raise RuntimeError(f"Сессия Telegram не авторизована. {hint}")

        me = await client.get_me()
        log.info("Telethon подключён как %s (id=%s)", getattr(me, "username", "?"), me.id)
        _client = client
        return client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.disconnect()
        _client = None


def throttle() -> asyncio.Semaphore:
    """Семафор, которым источники ограничивают параллельные обращения."""
    return _semaphore


async def safe_call(coro_factory, *, what: str = "запрос", max_wait: int = 300):
    """Выполняет запрос к Telegram, корректно переживая FloodWait.

    Если Telegram просит ждать дольше max_wait — не ждём, а поднимаем ошибку:
    лучше пропустить один канал в этом проходе, чем заморозить весь сбор.
    """
    try:
        return await coro_factory()
    except FloodWaitError as exc:
        if exc.seconds > max_wait:
            raise
        log.warning("FloodWait %s сек на %s — ждём", exc.seconds, what)
        await asyncio.sleep(exc.seconds + 1)
        return await coro_factory()
