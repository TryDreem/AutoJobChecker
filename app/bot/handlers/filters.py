"""Настройка фильтров: что именно присылать."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.keyboards import FilterCB
from app.core.models import UserProfile

log = logging.getLogger(__name__)
router = Router(name="filters")

TOGGLE_FIELDS = {"roles", "seniorities", "work_modes", "kinds", "gig_categories"}
WORD_FIELDS = {"keywords", "stopwords"}


class WordState(StatesGroup):
    waiting_word = State()


def _summary(profile: UserProfile) -> str:
    def listed(values: list[str], empty: str = "любые") -> str:
        return ", ".join(values) if values else empty

    return (
        "⚙️ <b>Фильтры</b>\n\n"
        f"🎯 Показывать от <b>{profile.min_score}%</b> релевантности\n"
        f"💻 Специализация: <b>{listed(profile.roles)}</b>\n"
        f"🎓 Уровень: <b>{listed(profile.seniorities)}</b>\n"
        f"🌍 Формат: <b>{listed(profile.work_modes)}</b>\n"
        f"📋 Тип: <b>{listed(profile.kinds)}</b>\n"
        f"🧩 Направления заказов: <b>{listed(profile.gig_categories)}</b>\n"
        f"📅 Требуемый опыт: <b>"
        f"{'до ' + str(profile.max_required_years) + ' лет' if profile.max_required_years else 'любой'}</b>\n"
        f"🔥 Срочные алерты: <b>{'включены' if profile.alert_enabled else 'выключены'}</b>\n"
        f"💰 Оплата: <b>"
        f"{'от $' + str(profile.min_salary_usd) + '/мес' if profile.min_salary_usd else 'не важно'}</b>\n"
        f"🔑 Ключевые слова: <b>{listed(profile.keywords, 'нет')}</b>\n"
        f"🚷 Стоп-слова: <b>{listed(profile.stopwords, 'нет')}</b>\n\n"
        "<i>Нажми на строку, чтобы поменять.</i>"
    )


@router.message(F.text == kb.BTN_FILTERS)
@router.message(Command("filters"))
async def show_filters(message: Message, profile: UserProfile) -> None:
    await message.answer(_summary(profile), reply_markup=kb.filters_menu(profile))


async def _back_to_root(callback: CallbackQuery, profile: UserProfile) -> None:
    if callback.message:
        await callback.message.edit_text(
            _summary(profile), reply_markup=kb.filters_menu(profile)
        )
    await callback.answer()


@router.callback_query(FilterCB.filter(F.action == "menu"))
async def open_submenu(
    callback: CallbackQuery, callback_data: FilterCB, profile: UserProfile
) -> None:
    field = callback_data.field
    message = callback.message
    if message is None:
        await callback.answer()
        return

    if field == "root":
        await _back_to_root(callback, profile)
        return

    if field == "roles":
        await message.edit_text(
            "💻 <b>Какие направления присылать?</b>\n\n"
            "<i>Если ничего не выбрано — присылаю все подходящие.</i>",
            reply_markup=kb.roles_menu(profile.roles),
        )
    elif field == "seniorities":
        await message.edit_text(
            "🎓 <b>Какой уровень позиций?</b>\n\n"
            "<i>Ничего не выбрано — беру любой, но junior и стажировки всё равно "
            "получают больше баллов.</i>",
            reply_markup=kb.seniority_menu(profile.seniorities),
        )
    elif field == "work_modes":
        await message.edit_text(
            "🌍 <b>Формат работы</b>\n\n"
            "<i>Офис и гибрид в Познани проходят в любом случае — они оцениваются "
            "отдельно.</i>",
            reply_markup=kb.work_mode_menu(profile.work_modes),
        )
    elif field == "kinds":
        await message.edit_text(
            "📋 <b>Что присылать?</b>\n\n"
            "<i>Резюме и реклама отсеиваются всегда, их тут нет.</i>",
            reply_markup=kb.kinds_menu(profile.kinds),
        )
    elif field == "gig_categories":
        await message.edit_text(
            "🧩 <b>Какие заказы присылать?</b>\n\n"
            "Фриланс-каналы мешают в кучу код, монтаж, дизайн и тексты — "
            "здесь можно оставить только нужное.\n"
            "<i>Ничего не выбрано — присылаю все направления.</i>",
            reply_markup=kb.gig_categories_menu(profile.gig_categories),
        )
    elif field == "years":
        await message.edit_text(
            "📅 <b>Требуемый опыт</b>\n\n"
            "Отсекает вакансии, где просят больше лет, чем указано.\n"
            "<i>Посты, где опыт вообще не назван, проходят всегда — их "
            "большинство, и отсекать их значило бы выкинуть почти всю ленту.</i>",
            reply_markup=kb.years_menu(profile.max_required_years),
        )
    elif field == "quality":
        await message.edit_text(
            "🎯 <b>Качество заказа</b>\n\n"
            "Те же условия, что и в фильтрах Upwork: подтверждённая оплата, "
            "число откликов, рейтинг клиента.\n"
            "<i>Посты без этих данных (Telegram, обычные job-борды) проходят "
            "в любом случае — метрики есть только у бирж.</i>",
            reply_markup=kb.quality_menu(profile.quality_filters or {}),
        )
    elif field == "alerts":
        from app.pipeline.alerts import describe_rules

        await message.edit_text(
            "🔥 <b>Срочные алерты</b>\n\n"
            "Отдельное уведомление, когда совпало всё сразу. В отличие от "
            "обычной ленты приходит немедленно и <b>игнорирует тихие часы</b>: "
            "на биржах смысл в том, чтобы успеть, пока откликов мало.\n\n"
            "<b>Сейчас условия такие:</b>\n"
            f"{describe_rules(profile)}",
            reply_markup=kb.alerts_menu(profile),
        )
    elif field == "score":
        await message.edit_text(
            "🎯 <b>Минимальная релевантность</b>\n\n"
            "Чем выше порог, тем меньше и точнее поток.\n"
            "<i>55% — разумная середина, 75% — только явные попадания.</i>",
            reply_markup=kb.score_menu(profile.min_score),
        )
    elif field == "salary":
        await message.edit_text(
            "💰 <b>Минимальная оплата</b>\n\n"
            "Сравниваю по пересчёту в доллары за месяц.\n"
            "<i>Посты без указанной вилки продолжу показывать — их большинство.</i>",
            reply_markup=kb.salary_menu(profile.min_salary_usd),
        )
    elif field == "keywords":
        await message.edit_text(
            "🔑 <b>Мои ключевые слова</b>\n\n"
            "Посты с этими словами получают прибавку к оценке.\n"
            "<i>Например: fastapi, стажировка, poznań.</i>",
            reply_markup=kb.words_menu("keywords", profile.keywords),
        )
    elif field == "stopwords":
        await message.edit_text(
            "🚷 <b>Стоп-слова</b>\n\n"
            "Посты с этими словами отбрасываются полностью.\n"
            "<i>Например: битрикс, продажи, casino.</i>",
            reply_markup=kb.words_menu("stopwords", profile.stopwords),
        )

    await callback.answer()


@router.callback_query(FilterCB.filter(F.action == "toggle"))
async def toggle_value(
    callback: CallbackQuery,
    callback_data: FilterCB,
    session: AsyncSession,
    profile: UserProfile,
) -> None:
    field, value = callback_data.field, callback_data.value

    if field == "q_verified":
        quality = dict(profile.quality_filters or {})
        quality["payment_verified"] = not quality.get("payment_verified")
        if not quality["payment_verified"]:
            quality.pop("payment_verified")
        profile.quality_filters = quality
        await session.flush()
        await callback.answer("Условие обновлено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=kb.quality_menu(quality))
        return

    if field == "alert_on":
        profile.alert_enabled = not profile.alert_enabled
        await session.flush()
        await callback.answer(
            "🔥 Срочные алерты включены" if profile.alert_enabled else "💤 Алерты выключены"
        )
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=kb.alerts_menu(profile))
        return

    if field == "notify":
        profile.notify_enabled = not profile.notify_enabled
        await session.flush()
        await callback.answer(
            "🔔 Уведомления включены" if profile.notify_enabled else "🔕 Уведомления выключены"
        )
        await _back_to_root(callback, profile)
        return

    if field in TOGGLE_FIELDS:
        current = list(getattr(profile, field) or [])
        if value in current:
            current.remove(value)
        else:
            current.append(value)
        setattr(profile, field, current)
        await session.flush()

        message = callback.message
        if message is not None:
            builders = {
                "roles": kb.roles_menu,
                "seniorities": kb.seniority_menu,
                "work_modes": kb.work_mode_menu,
                "kinds": kb.kinds_menu,
                "gig_categories": kb.gig_categories_menu,
            }
            await message.edit_reply_markup(reply_markup=builders[field](current))
        await callback.answer()
        return

    if field in WORD_FIELDS:
        current = list(getattr(profile, field) or [])
        if value in current:
            current.remove(value)
            setattr(profile, field, current)
            await session.flush()
            if callback.message:
                await callback.message.edit_reply_markup(
                    reply_markup=kb.words_menu(field, current)
                )
            await callback.answer(f"Удалено: {value}")
        return

    await callback.answer()


@router.callback_query(FilterCB.filter(F.action == "set"))
async def set_value(
    callback: CallbackQuery,
    callback_data: FilterCB,
    state: FSMContext,
    session: AsyncSession,
    profile: UserProfile,
) -> None:
    field, value = callback_data.field, callback_data.value

    if field in WORD_FIELDS and value == "__add__":
        await state.set_state(WordState.waiting_word)
        await state.update_data(word_field=field)
        title = "ключевое слово" if field == "keywords" else "стоп-слово"
        await callback.message.answer(  # type: ignore[union-attr]
            f"✍️ Напиши {title} одним сообщением.\n"
            "<i>Можно несколько через запятую.</i>"
        )
        await callback.answer()
        return

    if field == "gig_categories" and value == "__coding__":
        # Быстрый пресет: всё, что про код, одной кнопкой.
        from app.core.enums import GigCategory

        profile.gig_categories = [str(c) for c in GigCategory.coding()]
        await session.flush()
        await callback.answer("⚡️ Оставил только направления про код")
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=kb.gig_categories_menu(profile.gig_categories)
            )
        return

    if field == "years":
        profile.max_required_years = int(value)
        await session.flush()
        await callback.answer("Порог опыта обновлён")
        await _back_to_root(callback, profile)
        return

    if field in ("q_proposals", "q_rating", "q_reset"):
        quality = dict(profile.quality_filters or {})
        if field == "q_reset":
            quality = {}
            answer = "Условия качества сброшены"
        elif field == "q_proposals":
            # Повторное нажатие снимает ограничение.
            new_value = int(value)
            if quality.get("max_proposals") == new_value:
                quality.pop("max_proposals", None)
                answer = "Ограничение по откликам снято"
            else:
                quality["max_proposals"] = new_value
                answer = f"Откликов не больше {new_value}"
        else:
            new_value = float(value)
            if quality.get("min_client_rating") == new_value:
                quality.pop("min_client_rating", None)
                answer = "Ограничение по рейтингу снято"
            else:
                quality["min_client_rating"] = new_value
                answer = f"Рейтинг клиента от {new_value}"
        profile.quality_filters = quality
        await session.flush()
        await callback.answer(answer)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=kb.quality_menu(quality))
        return

    if field in ("a_score", "a_proposals", "a_limit"):
        rules = dict(profile.alert_rules or {})
        key = {"a_score": "min_score", "a_proposals": "max_proposals",
               "a_limit": "daily_limit"}[field]
        rules[key] = int(value)
        profile.alert_rules = rules
        await session.flush()
        await callback.answer("Правило алертов обновлено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=kb.alerts_menu(profile))
        return

    if field == "score":
        profile.min_score = int(value)
        await session.flush()
        await callback.answer(f"Порог: {value}%")
        await _back_to_root(callback, profile)
        return

    if field == "salary":
        profile.min_salary_usd = int(value)
        await session.flush()
        await callback.answer("Порог оплаты обновлён")
        await _back_to_root(callback, profile)
        return

    await callback.answer()


@router.message(WordState.waiting_word)
async def add_word(
    message: Message, state: FSMContext, session: AsyncSession, profile: UserProfile
) -> None:
    data = await state.get_data()
    field = data.get("word_field", "keywords")
    await state.clear()

    words = [w.strip().lower() for w in (message.text or "").split(",") if w.strip()]
    if not words:
        await message.answer("Ничего не понял — попробуй ещё раз.")
        return

    current = list(getattr(profile, field) or [])
    added = [w for w in words if w not in current][:20]
    current.extend(added)
    setattr(profile, field, current[:50])
    await session.flush()

    title = "Ключевые слова" if field == "keywords" else "Стоп-слова"
    await message.answer(
        f"✅ {title} обновлены: <b>{', '.join(added) or '—'}</b>",
        reply_markup=kb.main_menu(),
    )
    await message.answer(_summary(profile), reply_markup=kb.filters_menu(profile))


@router.callback_query(FilterCB.filter(F.action == "reset"))
async def reset_filters(
    callback: CallbackQuery, session: AsyncSession, profile: UserProfile
) -> None:
    from app.config import get_settings

    profile.min_score = get_settings().min_score_notify
    profile.roles = []
    profile.seniorities = []
    profile.work_modes = []
    profile.kinds = []
    profile.gig_categories = []
    profile.max_required_years = 0
    profile.quality_filters = {}
    profile.alert_rules = {}
    profile.min_salary_usd = 0
    profile.keywords = []
    profile.stopwords = []
    profile.notify_enabled = True
    await session.flush()

    await callback.answer("♻️ Фильтры сброшены")
    await _back_to_root(callback, profile)
