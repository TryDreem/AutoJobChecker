"""Старт, помощь и общие ответы."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.core.models import UserProfile
from app.pipeline.llm import get_classifier

router = Router(name="common")

WELCOME = """\
👋 <b>Привет! Я слежу за вакансиями и заказами вместо тебя.</b>

Я читаю Telegram-каналы и биржи, отбрасываю резюме, рекламу курсов и вакансии \
не по профилю, а подходящее присылаю карточкой — с зарплатой, стеком, датой \
и контактом, куда писать.

<b>Что я ищу по умолчанию</b>
🐍 Python backend в первую очередь, JS и fullstack во вторую
🎓 стажировки, junior и начальный middle
🌍 удалённо из любой точки мира либо офис/гибрид в Познани
🆕 только свежее — старое опускается в оценке само

<b>С чего начать</b>
1. Загляни в «📡 Источники» и нажми «🩺 Проверить все» — я проверю каналы и отключу мёртвые.
2. Настрой «⚙️ Фильтры» под себя, если дефолт не подходит.
3. Дальше просто жди — новое буду присылать сам.

Кнопки внизу всегда под рукой. Чтобы найти что-то в накопленной базе, \
просто напиши слово в чат.
"""

HELP = """\
<b>📖 Как это работает</b>

<b>Разделы</b>
📥 <b>Лента</b> — всё подходящее за две недели, вакансии и заказы вместе.
💼 <b>Вакансии</b> — только штатная работа и стажировки.
🧩 <b>Заказы</b> — только разовые проекты и фриланс-задачи.
💼 <b>Upwork</b> — заказы с биржи, сверху те, где меньше откликов.
⭐️ <b>Избранное</b> — то, что ты отметил звёздочкой.
🔍 <b>Поиск</b> — по всей базе, включая посты ниже твоего порога.
⚙️ <b>Фильтры</b> — что именно присылать.
📡 <b>Источники</b> — каналы и биржи: включить, выключить, добавить, проверить.
📊 <b>Статистика</b> — сколько собрано и какие каналы реально полезны.
📬 <b>Сводка за день</b> — короткий список за последние сутки.

<b>Кнопки под карточкой</b>
🔗 Открыть оригинал — перейти к посту в канале или на бирже.
⭐️ В избранное — сохранить, повторное нажатие снимает.
🚫 Не то — скрыть; больше в ленте не появится.
📄 Полный текст — весь пост целиком и разбор оценки.
✅ Откликнулся — пометить, что уже написал.

<b>🔥 Срочные алерты</b>
Когда совпало всё сразу — стек, уровень, мало откликов, подтверждённая \
оплата — бот присылает отдельное уведомление немедленно, минуя тихие часы. \
Условия настраиваются в «⚙️ Фильтры» → «🔥 Срочные алерты».

<b>Оценка релевантности</b>
Считается по стеку, уровню, формату работы, географии, свежести и наличию \
вилки. Спорные посты дополнительно проверяет языковая модель. \
Разбор конкретной оценки виден по кнопке «📄 Полный текст».

<b>Команды</b>
/start — главное меню
/help — эта справка
/jobs — только вакансии и стажировки
/gigs — только заказы и разовые проекты
/upwork — заказы с Upwork
/unhide — вернуть в ленту всё скрытое кнопкой «Не то»
/scan — запустить обход источников прямо сейчас
/status — что с ботом, парсером и лимитами
"""


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=kb.main_menu())


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("status"))
async def cmd_status(message: Message, session: AsyncSession, profile: UserProfile) -> None:
    """Короткая диагностика — чтобы понять, живо ли всё."""
    from sqlalchemy import func, select

    from app.core.models import Source, Vacancy
    from app.sources.tg_client import get_client

    sources_on = await session.scalar(
        select(func.count(Source.id)).where(Source.enabled.is_(True))
    ) or 0
    broken = await session.scalar(
        select(func.count(Source.id)).where(Source.error_count > 2)
    ) or 0
    total = await session.scalar(select(func.count(Vacancy.id))) or 0
    last = await session.scalar(select(func.max(Source.last_fetch_at)))

    try:
        client = await get_client()
        me = await client.get_me()
        tg_status = f"✅ подключён как @{me.username}" if me.username else "✅ подключён"
    except Exception as exc:  # noqa: BLE001 - показываем причину как есть
        tg_status = f"❌ {exc}"

    from app.bot.formatters import format_when

    lines = [
        "<b>⚙️ Состояние</b>",
        "",
        f"📡 Парсер Telegram: {tg_status}",
        f"🧠 LLM-классификатор: {get_classifier().status}",
        "",
        f"🔌 Источников включено: <b>{sources_on}</b>" + (f" (проблемных: {broken})" if broken else ""),
        f"🗂 Постов в базе: <b>{total}</b>",
        f"🕐 Последний обход: {format_when(last) if last else 'ещё не было'}",
        "",
        f"🎯 Твой порог: <b>{profile.min_score}%</b>",
        f"🔔 Уведомления: <b>{'включены' if profile.notify_enabled else 'выключены'}</b>",
        f"🌙 Тихие часы: <b>{profile.quiet_from}:00 – {profile.quiet_to}:00</b>",
    ]
    await message.answer("\n".join(lines))
