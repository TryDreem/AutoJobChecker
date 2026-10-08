"""Сборка бота: диспетчер, роутеры, middleware, меню команд."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.handlers import common, feed, filters, sources, stats
from app.bot.middlewares import AccessMiddleware, DatabaseMiddleware
from app.config import get_settings

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="feed", description="Свежие вакансии"),
    BotCommand(command="favorites", description="Избранное"),
    BotCommand(command="search", description="Поиск по базе"),
    BotCommand(command="digest", description="Сводка за день"),
    BotCommand(command="filters", description="Настроить фильтры"),
    BotCommand(command="sources", description="Каналы и биржи"),
    BotCommand(command="addsource", description="Добавить канал"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="scan", description="Обойти источники сейчас"),
    BotCommand(command="status", description="Состояние бота"),
    BotCommand(command="help", description="Справка"),
]


def create_bot() -> Bot:
    return Bot(
        token=get_settings().bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Порядок важен: сначала проверяем доступ, потом открываем сессию.
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(AccessMiddleware())
        observer.middleware(DatabaseMiddleware())

    # feed регистрируется последним: в нём ловится свободный текст,
    # который иначе перехватил бы ввод для фильтров и источников.
    dispatcher.include_router(common.router)
    dispatcher.include_router(filters.router)
    dispatcher.include_router(sources.router)
    dispatcher.include_router(stats.router)
    dispatcher.include_router(feed.router)

    return dispatcher


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(COMMANDS)
