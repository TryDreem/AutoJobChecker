"""Статистика и ручной запуск обхода источников."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatters import esc
from app.bot.services import collect_stats, unhide_all, unsent_for_user
from app.core.models import UserProfile

log = logging.getLogger(__name__)
router = Router(name="stats")


@router.message(F.text == kb.BTN_STATS)
@router.message(Command("stats"))
async def show_stats(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    data = await collect_stats(session, profile)

    lines = [
        "📊 <b>Статистика</b>",
        "",
        "<b>Собрано постов</b>",
        f"• всего в базе: <b>{data['total']}</b>",
        f"• за сутки: <b>{data['today']}</b>",
        f"• за неделю: <b>{data['week']}</b>",
        f"• отсеяно как перепечатки: <b>{data['duplicates']}</b>",
        "",
        "<b>Подходит тебе</b>",
        f"• проходит порог {profile.min_score}%: <b>{data['relevant']}</b>",
        f"• в избранном: <b>{data['favorites']}</b>",
        f"• откликнулся: <b>{data['applied']}</b>",
        f"• скрыто кнопкой «Не то»: <b>{data['hidden']}</b>",
        "",
        "<b>Источники</b>",
        f"• включено {data['sources_on']} из {data['sources_total']}",
    ]

    # Скрытия копятся незаметно и в какой-то момент опустошают ленту —
    # подсказываем выход, пока пользователь не решил, что бот сломался.
    if data["hidden"] >= 20:
        lines += [
            "",
            f"<i>Скрытых уже {data['hidden']} — они не показываются в ленте. "
            "Вернуть все обратно: /unhide</i>",
        ]

    if data["top_sources"]:
        lines += ["", "<b>Самые полезные каналы</b>"]
        for item in data["top_sources"]:
            rate = (item["kept"] / item["seen"] * 100) if item["seen"] else 0
            lines.append(
                f"• {esc(item['name'][:32])} — <b>{item['kept']}</b> "
                f"<i>(из {item['seen']}, {rate:.0f}%)</i>"
            )
    else:
        lines += ["", "<i>Полезность каналов появится после первых обходов.</i>"]

    await message.answer("\n".join(lines))


@router.message(Command("unhide"))
async def cmd_unhide(
    message: Message, session: AsyncSession, profile: UserProfile
) -> None:
    """Возвращает в ленту всё скрытое кнопкой «Не то»."""
    count = await unhide_all(session, profile)
    await session.commit()

    if not count:
        await message.answer("Скрытых постов нет — возвращать нечего.")
        return

    await message.answer(
        f"♻️ Вернул в ленту постов: <b>{count}</b>\n\n"
        "<i>Отметки «Откликнулся» и избранное не тронуты.</i>",
        reply_markup=kb.main_menu(),
    )


@router.message(Command("scan"))
async def cmd_scan(
    message: Message, session: AsyncSession, profile: UserProfile
) -> None:
    """Ручной обход — удобно сразу после добавления канала."""
    from app.scheduler import run_scan

    notice = await message.answer("🔄 Обхожу источники… Это может занять пару минут.")

    try:
        report = await run_scan(reason="ручной запуск")
    except Exception as exc:  # noqa: BLE001 - показываем причину пользователю
        log.exception("Ручной обход упал")
        await notice.edit_text(f"❌ Обход не удался: <i>{esc(str(exc)[:300])}</i>")
        return

    # «Сохранено» и «попало в ленту» — разные числа: в базу кладём всё, что
    # выше порога хранения, а показываем только прошедшее твои фильтры.
    # Без этой строки отчёт выглядел как обещание 22 новых постов в ленте.
    in_feed = len(await unsent_for_user(session, profile, report.new_ids))

    lines = [
        "✅ <b>Обход завершён</b>",
        "",
        f"📡 Источников обойдено: <b>{report.sources}</b>",
        f"📥 Постов просмотрено: <b>{report.fetched}</b>",
        f"🗑 Отсеяно как мусор: <b>{report.rejected}</b>",
        f"♻️ Перепечаток: <b>{report.duplicates}</b>",
        f"💾 Сохранено в базу: <b>{report.saved}</b>",
        f"📬 Из них подходит тебе: <b>{in_feed}</b>",
    ]
    if report.llm_checked:
        lines.append(f"🧠 Проверено моделью: <b>{report.llm_checked}</b>")
    if report.errors:
        lines += ["", "<b>Ошибки:</b>"]
        for item in report.errors[:5]:
            lines.append(f"• {esc(item[:90])}")

    if in_feed:
        lines += ["", "<i>Новое ушло в ленту — загляни в «📥 Лента».</i>"]
    elif report.saved:
        # Частый случай: посты сохранились, но не прошли порог или фильтры.
        lines += [
            "",
            f"<i>В ленту ничего не добавилось: сохранённое не прошло твои фильтры "
            f"(порог {profile.min_score}%). Смягчить — в «⚙️ Фильтры».</i>",
        ]

    await notice.edit_text("\n".join(lines))
