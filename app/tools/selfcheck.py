"""Самопроверка: конфиг, база, источники, весь пайплайн на живых данных.

Запуск:
    python -m app.tools.selfcheck            # только биржи, без Telegram
    python -m app.tools.selfcheck --telegram # ещё и каналы

Полезно после первой настройки и после правки словарей: видно, что реально
приходит из источников и как оценивается, до того как это начнёт падать в личку.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.core.db import init_db, session_scope
from app.core.enums import PostKind, SourceKind, WorkMode
from app.core.models import Source, Vacancy
from app.pipeline.llm import get_classifier
from app.pipeline.processor import process_source
from app.sources import manager, registry

log = logging.getLogger(__name__)


def line(char: str = "─", width: int = 68) -> None:
    print(char * width)


async def check_config() -> bool:
    print("\n1. Конфигурация")
    line()
    settings = get_settings()

    # Значения из .env.example считаем незаполненными — иначе проверка
    # «зелёная», а бот при запуске падает.
    token_ok = bool(settings.bot_token) and not settings.bot_token.startswith("1234567890")
    tg_ok = settings.tg_api_id not in (0, 1234567)

    problems: list[str] = []
    if not token_ok:
        problems.append("BOT_TOKEN не заполнен — получи токен у @BotFather")
    if not settings.admin_ids:
        problems.append("ADMIN_IDS пуст — бот будет отвечать кому угодно")
    if not tg_ok:
        problems.append("TG_API_ID не заполнен — нужен для чтения каналов")

    print(f"  Бот-токен ............ {'✅ задан' if token_ok else '❌ нет'}")
    print(f"  Владельцы ............ {settings.admin_ids or '❌ не заданы'}")
    print(f"  Telegram API ......... {'✅ задан' if tg_ok else '❌ нет'}")
    print(f"  LLM-классификатор .... {get_classifier().status}")
    print(f"  Часовой пояс ......... {settings.timezone}")
    print(f"  Порог показа ......... {settings.min_score_notify}%")

    for problem in problems:
        print(f"  ⚠️  {problem}")
    return not problems


async def check_database() -> None:
    print("\n2. База данных")
    line()
    await init_db()
    async with session_scope() as session:
        vacancies = await session.scalar(select(func.count(Vacancy.id))) or 0
        sources = await session.scalar(select(func.count(Source.id))) or 0
    print(f"  ✅ Схема готова, записей: {vacancies}, источников: {sources}")


async def check_catalog() -> None:
    print("\n3. Каталог источников")
    line()
    registry.load_all()
    async with session_scope() as session:
        added, total = await manager.load_catalog(session)

        by_kind = await session.execute(
            select(Source.kind, func.count(Source.id))
            .where(Source.enabled.is_(True))
            .group_by(Source.kind)
        )
        counts = dict(by_kind.all())

    print(f"  В файле записей: {total}, добавлено новых: {added}")
    for kind, count in counts.items():
        print(f"  • {kind:<10} включено: {count}")
    print(f"  Реализовано парсеров: {', '.join(registry.known_keys())}")


async def check_sources(include_telegram: bool) -> list[int]:
    print("\n4. Обход источников (живые данные)")
    line()

    kinds = [SourceKind.JOBBOARD, SourceKind.RSS]
    if include_telegram:
        kinds.append(SourceKind.TELEGRAM)
    else:
        print("  Telegram пропущен (запусти с --telegram, чтобы включить)\n")

    new_ids: list[int] = []
    async with session_scope() as session:
        rows = await session.execute(
            select(Source).where(Source.enabled.is_(True), Source.kind.in_(kinds))
            .order_by(Source.priority.desc())
        )
        records = list(rows.scalars())

        if not records:
            print("  ⚠️  Нет включённых источников")
            return []

        for record in records:
            print(f"  ▸ {record.display_name:<30}", end=" ", flush=True)
            stats = await process_source(session, record)
            if stats.error:
                print(f"❌ {stats.error[:60]}")
                continue
            print(
                f"получено {stats.fetched:>4} · "
                f"мусор {stats.rejected:>4} · "
                f"дубли {stats.duplicates:>3} · "
                f"сохранено {stats.saved:>3}"
            )
            new_ids.extend(stats.new_ids)

    return new_ids


async def show_results(new_ids: list[int]) -> None:
    print("\n5. Что нашлось")
    line()

    if not new_ids:
        print("  Новых записей нет. Это нормально, если запуск повторный —")
        print("  уже обработанные посты второй раз не сохраняются.")
        return

    async with session_scope() as session:
        rows = await session.execute(
            select(Vacancy)
            .where(Vacancy.id.in_(new_ids))
            .order_by(Vacancy.score.desc())
            .limit(10)
        )
        top = list(rows.scalars())

        by_kind = await session.execute(
            select(Vacancy.kind, func.count(Vacancy.id))
            .where(Vacancy.id.in_(new_ids))
            .group_by(Vacancy.kind)
        )
        kinds = dict(by_kind.all())

    print(f"  Сохранено новых: {len(new_ids)}")
    print("  По типам: " + ", ".join(
        f"{PostKind(k).label} — {c}" for k, c in kinds.items()
    ))
    print("\n  Лучшие находки:\n")

    for number, vacancy in enumerate(top, 1):
        mode = WorkMode(vacancy.work_mode)
        salary = ""
        if vacancy.salary_min or vacancy.salary_max:
            salary = f" · 💰 {vacancy.salary_raw or ''} {vacancy.salary_currency}".rstrip()
        print(f"  {number:>2}. [{vacancy.score:>3}%] {vacancy.title[:58]}")
        print(f"      {PostKind(vacancy.kind).label} · {mode.label}{salary}")
        if vacancy.stack:
            print(f"      стек: {', '.join(vacancy.stack[:6])}")
        print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Самопроверка AutoChecker")
    parser.add_argument(
        "--telegram", action="store_true", help="обойти ещё и Telegram-каналы"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    print()
    print("═" * 68)
    print("  AutoChecker — самопроверка")
    print("═" * 68)

    config_ok = await check_config()
    await check_database()
    await check_catalog()

    if args.telegram:
        from app.sources.tg_client import close_client, get_client

        print("\n   Подключаюсь к Telegram…")
        try:
            await get_client()
            print("   ✅ Аккаунт-парсер подключён")
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ {exc}")
            print("      Выполни: python -m app.tools.login")
            args.telegram = False

    new_ids = await check_sources(args.telegram)
    await show_results(new_ids)

    if args.telegram:
        from app.sources.tg_client import close_client

        await close_client()

    print()
    line("═")
    if config_ok:
        print("  ✅ Всё в порядке. Запускай: python main.py")
    else:
        print("  ⚠️  Заполни .env и запусти проверку ещё раз.")
    line("═")
    print()
    return 0 if config_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОтменено.")
        sys.exit(1)
