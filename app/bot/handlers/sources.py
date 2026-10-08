"""Управление источниками: список, включение, добавление, проверка, автопоиск."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatters import esc, format_when
from app.bot.keyboards import SourceCB
from app.core.enums import SourceKind
from app.core.models import Source, Vacancy
from app.sources import manager

log = logging.getLogger(__name__)
router = Router(name="sources")

PER_PAGE = 8


class AddSourceState(StatesGroup):
    waiting_channel = State()


async def _page(session: AsyncSession, page: int) -> tuple[list[Source], int, int]:
    total = await session.scalar(select(func.count(Source.id))) or 0
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    rows = await session.execute(
        select(Source)
        .order_by(Source.kind, Source.enabled.desc(), Source.id)
        .offset(page * PER_PAGE)
        .limit(PER_PAGE)
    )
    return list(rows.scalars()), page, pages


async def _overview(session: AsyncSession) -> str:
    total = await session.scalar(select(func.count(Source.id))) or 0
    enabled = await session.scalar(
        select(func.count(Source.id)).where(Source.enabled.is_(True))
    ) or 0
    channels = await session.scalar(
        select(func.count(Source.id)).where(Source.kind == SourceKind.TELEGRAM)
    ) or 0
    boards = await session.scalar(
        select(func.count(Source.id)).where(Source.kind != SourceKind.TELEGRAM)
    ) or 0
    broken = await session.scalar(
        select(func.count(Source.id)).where(Source.error_count > 2)
    ) or 0

    lines = [
        "📡 <b>Источники</b>",
        "",
        f"Всего: <b>{total}</b> · включено: <b>{enabled}</b>",
        f"Telegram-каналов: <b>{channels}</b> · бирж и лент: <b>{boards}</b>",
    ]
    if broken:
        lines.append(f"⚠️ С ошибками: <b>{broken}</b> — стоит нажать «🩺 Проверить все»")
    lines += [
        "",
        "<i>Тап по строке включает и выключает источник.</i>",
    ]
    return "\n".join(lines)


@router.message(F.text == kb.BTN_SOURCES)
@router.message(Command("sources"))
async def show_sources(message: Message, session: AsyncSession) -> None:
    sources, page, pages = await _page(session, 0)
    await message.answer(
        await _overview(session), reply_markup=kb.sources_menu(sources, page, pages)
    )


@router.callback_query(SourceCB.filter(F.action == "list"))
async def paginate(
    callback: CallbackQuery, callback_data: SourceCB, session: AsyncSession
) -> None:
    sources, page, pages = await _page(session, callback_data.page)
    if callback.message:
        await callback.message.edit_text(
            await _overview(session), reply_markup=kb.sources_menu(sources, page, pages)
        )
    await callback.answer()


@router.callback_query(SourceCB.filter(F.action == "toggle"))
async def toggle_source(
    callback: CallbackQuery, callback_data: SourceCB, session: AsyncSession
) -> None:
    record = await session.get(Source, callback_data.source_id)
    if record is None:
        await callback.answer("Источник не найден", show_alert=True)
        return

    record.enabled = not record.enabled
    if record.enabled:
        # Даём выключенному источнику чистый лист.
        record.error_count = 0
        record.last_error = None
    await session.flush()

    # Сразу говорим, сколько постов появилось или ушло из ленты: выключение
    # источника скрывает и то, что уже собрано, и это стоит показать явно.
    affected = await session.scalar(
        select(func.count(Vacancy.id)).where(
            Vacancy.source_id == record.id,
            Vacancy.duplicate_of.is_(None),
            Vacancy.score >= 1,
        )
    ) or 0

    if record.enabled:
        detail = f"🟢 Включён: {record.display_name}"
        if affected:
            detail += f"\nВ ленту вернулось постов: {affected}"
    else:
        detail = f"⚪️ Выключен: {record.display_name}"
        detail += (
            f"\nИз ленты убрано постов: {affected}" if affected
            else "\nНовые посты больше не собираю"
        )
    if record.last_error:
        detail += f"\nПоследняя ошибка: {record.last_error[:100]}"
    await callback.answer(detail, show_alert=bool(affected))

    sources, page, pages = await _page(session, callback_data.page)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=kb.sources_menu(sources, page, pages)
        )


@router.callback_query(SourceCB.filter(F.action == "check"))
async def check_all(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer("Проверяю — это займёт до минуты")
    message = callback.message
    if message is None:
        return

    notice = await message.answer("🩺 Проверяю источники…")
    results = await manager.verify_all(session, only_enabled=True)
    await session.flush()

    alive = [r for r in results if r.ok]
    dead = [r for r in results if not r.ok]

    lines = [
        "🩺 <b>Результат проверки</b>",
        "",
        f"✅ Работают: <b>{len(alive)}</b>",
        f"❌ Не отвечают: <b>{len(dead)}</b>",
    ]
    if dead:
        lines += ["", "<b>Проблемные:</b>"]
        for item in dead[:12]:
            lines.append(f"• {esc(item.name)} — <i>{esc(item.detail[:70])}</i>")
        lines.append("")
        lines.append("<i>После трёх неудач подряд источник отключается сам.</i>")

    await notice.edit_text("\n".join(lines))

    sources, page, pages = await _page(session, 0)
    await message.answer(
        await _overview(session), reply_markup=kb.sources_menu(sources, page, pages)
    )


@router.callback_query(SourceCB.filter(F.action == "dgroup"))
async def choose_discovery_group(callback: CallbackQuery) -> None:
    """Спрашиваем направление до поиска: точечный проход сильно чище общего."""
    if callback.message:
        await callback.message.edit_text(
            "🔎 <b>Что искать?</b>\n\n"
            "Поиск по одному направлению даёт заметно меньше мусора, "
            "чем общий проход по всем сразу.",
            reply_markup=kb.discovery_groups_menu(),
        )
    await callback.answer()


@router.callback_query(SourceCB.filter(F.action == "discover"))
async def discover(
    callback: CallbackQuery,
    callback_data: SourceCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer("Ищу каналы…")
    message = callback.message
    if message is None:
        return

    group = callback_data.group or None
    label = manager.DISCOVERY_GROUP_LABELS.get(group or "", "все направления")
    notice = await message.answer(
        f"🔎 Ищу каналы: <b>{esc(label)}</b>\n"
        "Проверяю описания и число подписчиков — это может занять минуту-две."
    )
    try:
        candidates = await manager.discover_channels(session, group=group)
    except Exception as exc:  # noqa: BLE001 - причину показываем как есть
        await notice.edit_text(
            f"❌ Поиск не удался: <i>{esc(str(exc)[:200])}</i>\n\n"
            "Обычно это значит, что аккаунт-парсер не авторизован — "
            "запусти <code>python -m app.tools.login</code>."
        )
        return

    if not candidates:
        await notice.edit_text(
            "🤷 Ничего нового не нашлось.\n\n"
            "Можно добавить канал вручную кнопкой «➕ Добавить канал»."
        )
        return

    await state.update_data(
        discovered=[(c.username, c.title) for c in candidates]
    )

    lines = [f"🔎 <b>Нашёл каналы: {esc(label)}</b>", ""]
    for candidate in candidates:
        subs = f" · 👥 {candidate.participants:,}".replace(",", " ") if candidate.participants else ""
        lines.append(
            f"• <b>{esc(candidate.title[:45])}</b>\n"
            f"  {candidate.username}{subs} · ⭐️ {candidate.score}"
        )
    lines += ["", "<i>Нажми на канал, чтобы добавить его в список.</i>"]

    await notice.edit_text(
        "\n".join(lines),
        reply_markup=kb.confirm_add_sources([(c.username, c.title) for c in candidates]),
    )


@router.callback_query(SourceCB.filter(F.action == "add"))
async def add_source(
    callback: CallbackQuery,
    callback_data: SourceCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    discovered = data.get("discovered") or []

    # Кнопка из результатов автопоиска несёт индекс в source_id.
    if discovered and 0 <= callback_data.source_id < len(discovered):
        username, title = discovered[callback_data.source_id]
        record, note = await manager.add_channel(session, username, title=title)
        if record is not None:
            ok, detail = (await manager.verify_source(session, record)).ok, ""
            await session.flush()
            note += " Проверка: " + ("✅ читается." if ok else "⚠️ пока не читается.")
        await callback.answer(note, show_alert=True)
        return

    # Иначе это кнопка «Добавить канал» — просим ввести вручную.
    await state.set_state(AddSourceState.waiting_channel)
    if callback.message:
        await callback.message.answer(
            "➕ <b>Добавление канала</b>\n\n"
            "Пришли @username канала или ссылку вида <code>t.me/название</code>.\n"
            "Можно несколько — каждый с новой строки.\n\n"
            "<i>Канал должен быть публичным.</i>"
        )
    await callback.answer()


@router.message(AddSourceState.waiting_channel)
async def receive_channel(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    raw_lines = [line.strip() for line in (message.text or "").split("\n") if line.strip()]
    if not raw_lines:
        await message.answer("Пусто — попробуй ещё раз.")
        return

    report: list[str] = []
    for raw in raw_lines[:10]:
        record, note = await manager.add_channel(session, raw)
        if record is None:
            report.append(f"❌ {esc(raw)} — {note}")
            continue

        result = await manager.verify_source(session, record)
        await session.flush()
        mark = "✅" if result.ok else "⚠️"
        report.append(f"{mark} {esc(note)} <i>{esc(result.detail[:60])}</i>")

    await message.answer("\n".join(report), reply_markup=kb.main_menu())

    sources, page, pages = await _page(session, 0)
    await message.answer(
        await _overview(session), reply_markup=kb.sources_menu(sources, page, pages)
    )


@router.message(Command("addsource"))
async def cmd_add_source(message: Message, state: FSMContext) -> None:
    await state.set_state(AddSourceState.waiting_channel)
    await message.answer("➕ Пришли @username канала или ссылку t.me/…")
