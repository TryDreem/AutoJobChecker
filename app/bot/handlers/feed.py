"""Лента, избранное, поиск и действия над карточками."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatters import esc, format_digest, format_vacancy
from app.bot.keyboards import FeedCB, VacancyCB
from app.bot.services import (
    PAGE_SIZE,
    upwork_page,
    favorites_page,
    feed_page,
    get_vacancy,
    is_favorite,
    search_page,
    set_status,
)
from app.core.enums import PostKind, UserStatus
from app.core.models import UserProfile

log = logging.getLogger(__name__)
router = Router(name="feed")


class SearchState(StatesGroup):
    waiting_query = State()


EMPTY_FEED = (
    "📭 <b>Пока ничего подходящего.</b>\n\n"
    "Это нормально в первые часы после запуска — я ещё собираю посты.\n\n"
    "Что можно сделать:\n"
    "• запустить обход прямо сейчас — /scan\n"
    "• снизить порог в «⚙️ Фильтры»\n"
    "• добавить каналы в «📡 Источники»"
)

EMPTY_FAVORITES = (
    "⭐️ <b>В избранном пусто.</b>\n\n"
    "Нажимай «⭐️ В избранное» под карточками — так они не потеряются в ленте."
)

EMPTY_JOBS = (
    "📭 <b>Подходящих вакансий пока нет.</b>\n\n"
    "Заказы могут быть в «🧩 Заказы» — это разные вкладки.\n\n"
    "• запустить обход прямо сейчас — /scan\n"
    "• снизить порог в «⚙️ Фильтры»\n"
    "• добавить каналы в «📡 Источники»"
)

EMPTY_GIGS = (
    "📭 <b>Подходящих заказов пока нет.</b>\n\n"
    "Вакансии могут быть в «💼 Вакансии» — это разные вкладки.\n\n"
    "• запустить обход прямо сейчас — /scan\n"
    "• снизить порог в «⚙️ Фильтры»\n"
    "• добавить каналы в «📡 Источники»"
)

# «Вакансии» = постоянная занятость (включая стажировки — это тоже штатная
# позиция, просто короткая). «Заказы» = разовые проекты и фриланс-задачи.
EMPTY_UPWORK = (
    "💼 <b>Заказов с Upwork пока нет.</b>\n\n"
    "Upwork отключил публичные RSS в 2024 году, поэтому нужен официальный "
    "API-ключ — он бесплатный для личного использования.\n\n"
    "<b>Как получить:</b>\n"
    "1. Зайди на upwork.com → Settings → API Access\n"
    "2. Создай приложение (Key Type: <b>OAuth 2.0</b>)\n"
    "3. В описании укажи, что это личный агрегатор вакансий для себя\n"
    "4. Дождись ответа — обычно около недели, статус ключа станет Enabled\n"
    "5. Впиши <code>UPWORK_CLIENT_ID</code> и <code>UPWORK_CLIENT_SECRET</code> "
    "в <code>.env</code>\n"
    "6. Запусти <code>python -m app.tools.upwork_login</code> — он проведёт "
    "через одноразовую авторизацию в браузере и выдаст "
    "<code>UPWORK_REFRESH_TOKEN</code>\n\n"
    "<i>В профиле должен быть полный адрес и подтверждённая личность — "
    "без этого заявку отклоняют.</i>"
)

JOB_KINDS = [str(PostKind.JOB), str(PostKind.INTERNSHIP)]
GIG_KINDS = [str(PostKind.GIG)]


async def _safe_edit_markup(message: Message, markup) -> None:
    """Перерисовывает кнопки, не роняя обработчик.

    Telegram отвечает ошибкой, если разметка не изменилась или сообщение
    слишком старое. Для нас это не повод считать действие неудавшимся —
    отметка в базе к этому моменту уже сохранена.
    """
    try:
        await message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as exc:
        log.debug("Не удалось обновить кнопки карточки: %s", exc)


async def _render_page(
    target: Message | CallbackQuery,
    session: AsyncSession,
    profile: UserProfile,
    mode: str,
    offset: int,
    query_text: str = "",
) -> None:
    """Отправляет страницу: заголовок, карточки, кнопки навигации."""
    if mode == "fav":
        items, total = await favorites_page(session, profile, offset=offset)
        empty = EMPTY_FAVORITES
        title = "⭐️ <b>Избранное</b>"
    elif mode == "search":
        items, total = await search_page(session, profile, query_text, offset=offset)
        empty = f"🔍 По запросу «{query_text}» ничего не нашлось."
        title = f"🔍 <b>Результаты: «{query_text}»</b>"
    elif mode == "job":
        items, total = await feed_page(session, profile, offset=offset, kinds=JOB_KINDS)
        empty = EMPTY_JOBS
        title = "💼 <b>Вакансии</b>"
    elif mode == "upwork":
        items, total = await upwork_page(session, profile, offset=offset)
        empty = EMPTY_UPWORK
        title = "💼 <b>Upwork — сначала те, где мало откликов</b>"
    elif mode == "gig":
        items, total = await feed_page(session, profile, offset=offset, kinds=GIG_KINDS)
        empty = EMPTY_GIGS
        title = "🧩 <b>Заказы и разовые проекты</b>"
    else:
        items, total = await feed_page(session, profile, offset=offset)
        empty = EMPTY_FEED
        title = "📥 <b>Свежие вакансии и заказы</b>"

    message = target if isinstance(target, Message) else target.message
    if message is None:
        return

    if not items:
        await message.answer(empty, reply_markup=kb.main_menu())
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    shown_to = min(offset + len(items), total)
    await message.answer(f"{title}\nПоказаны {offset + 1}–{shown_to} из {total}")

    for number, (vacancy, source) in enumerate(items, start=offset + 1):
        favorite = await is_favorite(session, profile, vacancy.id)
        await message.answer(
            format_vacancy(vacancy, source, index=f"<b>{number}.</b>"),
            reply_markup=kb.vacancy_actions(vacancy.id, vacancy.url, is_favorite=favorite),
            disable_web_page_preview=True,
        )

    if total > PAGE_SIZE:
        await message.answer(
            "Листай страницы:",
            reply_markup=kb.feed_navigation(mode, offset, total, PAGE_SIZE),
        )

    if isinstance(target, CallbackQuery):
        await target.answer()


# --- Разделы ---------------------------------------------------------------
@router.message(F.text == kb.BTN_FEED)
@router.message(Command("feed"))
async def show_feed(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    await _render_page(message, session, profile, "new", 0)


@router.message(F.text == kb.BTN_JOBS)
@router.message(Command("jobs"))
async def show_jobs(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    await _render_page(message, session, profile, "job", 0)


@router.message(F.text == kb.BTN_GIGS)
@router.message(Command("gigs"))
async def show_gigs(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    await _render_page(message, session, profile, "gig", 0)


@router.message(F.text == kb.BTN_UPWORK)
@router.message(Command("upwork"))
async def show_upwork(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    await _render_page(message, session, profile, "upwork", 0)


@router.message(F.text == kb.BTN_FAVORITES)
@router.message(Command("favorites"))
async def show_favorites(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    await _render_page(message, session, profile, "fav", 0)


@router.message(F.text == kb.BTN_DIGEST)
@router.message(Command("digest"))
async def show_digest(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    items, _ = await feed_page(session, profile, offset=0, limit=25, days=1)
    await message.answer(format_digest(items), disable_web_page_preview=True)


@router.message(F.text == kb.BTN_SEARCH)
@router.message(Command("search"))
async def ask_query(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchState.waiting_query)
    await message.answer(
        "🔍 Что искать? Напиши слово или фразу — например <code>fastapi</code>, "
        "<code>стажировка</code> или <code>Poznań</code>.\n\n"
        "Ищу по всей базе, включая посты ниже твоего порога."
    )


@router.message(SearchState.waiting_query)
async def run_search(
    message: Message, state: FSMContext, session: AsyncSession, profile: UserProfile
) -> None:
    query_text = (message.text or "").strip()
    await state.clear()
    if len(query_text) < 2:
        await message.answer("Слишком короткий запрос — нужно хотя бы два символа.")
        return
    await state.update_data(last_query=query_text)
    await _render_page(message, session, profile, "search", 0, query_text)


@router.callback_query(FeedCB.filter())
async def paginate(
    callback: CallbackQuery,
    callback_data: FeedCB,
    state: FSMContext,
    session: AsyncSession,
    profile: UserProfile,
) -> None:
    data = await state.get_data()
    await _render_page(
        callback,
        session,
        profile,
        callback_data.mode,
        callback_data.offset,
        data.get("last_query", ""),
    )


# --- Действия над карточкой ------------------------------------------------
@router.callback_query(VacancyCB.filter())
async def vacancy_action(
    callback: CallbackQuery,
    callback_data: VacancyCB,
    session: AsyncSession,
    profile: UserProfile,
) -> None:
    vacancy, source = await get_vacancy(session, callback_data.vacancy_id)
    if vacancy is None:
        await callback.answer("Эта вакансия пропала из базы", show_alert=True)
        return

    action = callback_data.action

    if action == "fav":
        status = await set_status(session, profile, vacancy.id, UserStatus.FAVORITE)
        added = status == UserStatus.FAVORITE
        # Фиксируем сразу: правка разметки ниже может не пройти (сообщение
        # старое, разметка не изменилась), а её ошибка откатила бы транзакцию —
        # и отметка терялась бы, хотя пользователю уже написали «добавлено».
        await session.commit()
        await callback.answer("⭐️ Добавлено в избранное" if added else "Убрано из избранного")
        if callback.message:
            await _safe_edit_markup(
                callback.message,
                kb.vacancy_actions(vacancy.id, vacancy.url, is_favorite=added),
            )
        return

    if action == "hide":
        await set_status(session, profile, vacancy.id, UserStatus.HIDDEN)
        await session.commit()
        await callback.answer("🚫 Скрыто — в ленте больше не появится")
        if callback.message:
            try:
                await callback.message.edit_text(
                    f"🚫 <s>{esc(vacancy.title)}</s>\n<i>Скрыто</i>", reply_markup=None
                )
            except TelegramBadRequest as exc:
                log.debug("Не удалось перерисовать карточку: %s", exc)
        return

    if action == "apply":
        await set_status(session, profile, vacancy.id, UserStatus.APPLIED)
        await session.commit()
        await callback.answer("✅ Отмечено: ты откликнулся")
        if callback.message:
            await _safe_edit_markup(callback.message, None)
        return

    if action == "full":
        if callback.message:
            await callback.message.answer(
                format_vacancy(vacancy, source, full=True),
                reply_markup=kb.vacancy_actions(
                    vacancy.id, vacancy.url,
                    is_favorite=await is_favorite(session, profile, vacancy.id),
                ),
                disable_web_page_preview=True,
            )
        await callback.answer()
        return

    await callback.answer()


# --- Свободный текст = поиск ------------------------------------------------
@router.message(F.text & ~F.text.startswith("/"))
async def freeform_search(
    message: Message, state: FSMContext, session: AsyncSession, profile: UserProfile
) -> None:
    """Любой текст вне меню считаем поисковым запросом — так быстрее всего."""
    query_text = (message.text or "").strip()
    if len(query_text) < 2:
        return
    await state.update_data(last_query=query_text)
    await _render_page(message, session, profile, "search", 0, query_text)
