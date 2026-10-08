"""AutoChecker — точка входа.

Запуск:
    python main.py

Перед первым запуском:
    1. cp .env.example .env  и заполнить ключи
    2. python -m app.tools.login  — вход в Telegram-аккаунт для парсера
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram.exceptions import TelegramUnauthorizedError

from app.bot.bot import create_bot, create_dispatcher, setup_commands
from app.config import get_settings
from app.core.db import init_db, session_scope
from app.scheduler import build_scheduler
from app.sources import manager, registry
from app.sources.tg_client import close_client, get_client

log = logging.getLogger(__name__)

BANNER = r"""
    _         _         ____ _               _
   / \  _   _| |_ ___  / ___| |__   ___  ___| | _____ _ __
  / _ \| | | | __/ _ \| |   | '_ \ / _ \/ __| |/ / _ \ '__|
 / ___ \ |_| | || (_) | |___| | | |  __/ (__|   <  __/ |
/_/   \_\__,_|\__\___/ \____|_| |_|\___|\___|_|\_\___|_|

        поиск работы и заказов на автопилоте
"""


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s │ %(levelname)-7s │ %(name)-26s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # Библиотеки шумят на INFO — оставляем от них только предупреждения.
    for noisy in ("aiogram.event", "apscheduler.executors", "telethon.network", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def prepare_telegram() -> bool:
    """Подключает аккаунт-парсер. Без него работают только биржи."""
    try:
        await get_client()
        return True
    except Exception as exc:  # noqa: BLE001 - причину нужно показать пользователю
        log.warning("%s", "─" * 68)
        log.warning("Парсер Telegram недоступен: %s", exc)
        log.warning("Каналы читаться не будут, биржи продолжат работать.")
        log.warning("Чтобы починить, выполни: python -m app.tools.login")
        log.warning("%s", "─" * 68)
        return False


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    print(BANNER)

    if not settings.admin_ids:
        log.warning(
            "ADMIN_IDS пуст — бот ответит любому, кто его найдёт. "
            "Укажи свой Telegram id в .env"
        )

    await init_db()
    registry.load_all()

    async with session_scope() as session:
        added, total = await manager.load_catalog(session)
        log.info("Каталог источников: в файле %s, добавлено новых %s", total, added)

    telegram_ok = await prepare_telegram()

    bot = create_bot()
    dispatcher = create_dispatcher()

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        log.error("BOT_TOKEN недействителен. Возьми свежий токен у @BotFather "
                  "и впиши его в .env")
        await bot.session.close()
        return
    except Exception as exc:  # noqa: BLE001 - причину показываем пользователю
        log.error("Не удалось подключиться к Telegram: %s", exc)
        await bot.session.close()
        return

    await setup_commands(bot)
    log.info("Бот запущен: @%s", me.username)
    log.info("Парсер каналов: %s", "работает" if telegram_ok else "выключен")

    scheduler = build_scheduler(bot)
    scheduler.start()
    for job in scheduler.get_jobs():
        log.info("Задача: %-22s следующий запуск %s", job.name, job.next_run_time)

    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        log.info("Останавливаюсь…")
        scheduler.shutdown(wait=False)
        await close_client()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nОстановлено вручную.")
