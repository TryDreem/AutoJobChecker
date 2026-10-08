"""Middleware: доступ, сессия БД и профиль пользователя.

Каждый хендлер получает готовые `session` и `profile` — не нужно открывать
сессию вручную и не нужно проверять права в каждом обработчике.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot.services import get_or_create_profile
from app.config import get_settings
from app.core.db import session_scope

log = logging.getLogger(__name__)

DENIED_TEXT = (
    "🔒 Это личный бот, доступ закрыт.\n\n"
    "Если он твой — добавь свой Telegram id в переменную ADMIN_IDS в файле .env "
    "и перезапусти бота."
)


class AccessMiddleware(BaseMiddleware):
    """Пускает только владельцев из ADMIN_IDS."""

    def __init__(self) -> None:
        self.allowed = set(get_settings().admin_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return None

        # Пустой список означает «ещё не настроено» — тогда пускаем всех,
        # иначе бот молча не отвечает и это выглядит как поломка.
        if self.allowed and user.id not in self.allowed:
            log.warning("Отказано в доступе: %s (@%s)", user.id, user.username)
            if isinstance(event, Message):
                await event.answer(DENIED_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ закрыт", show_alert=True)
            return None

        return await handler(event, data)


class DatabaseMiddleware(BaseMiddleware):
    """Открывает сессию на время обработки и подкладывает профиль."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        async with session_scope() as session:
            data["session"] = session
            data["profile"] = await get_or_create_profile(
                session, user.id, user.username or ""
            )
            return await handler(event, data)
