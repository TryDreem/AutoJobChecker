"""Клавиатуры и коллбэки.

Управление кнопками, а не командами: нижнее меню всегда под рукой, а действия
над конкретной вакансией живут прямо под её карточкой.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.core.enums import GigCategory, PostKind, Role, Seniority, WorkMode

# --- Подписи нижнего меню (используются и как фильтры сообщений) -----------
BTN_FEED = "📥 Лента"
BTN_JOBS = "💼 Вакансии"
BTN_GIGS = "🧩 Заказы"
BTN_UPWORK = "💼 Upwork"
BTN_FAVORITES = "⭐️ Избранное"
BTN_SEARCH = "🔍 Поиск"
BTN_FILTERS = "⚙️ Фильтры"
BTN_SOURCES = "📡 Источники"
BTN_STATS = "📊 Статистика"
BTN_DIGEST = "📬 Сводка за день"
BTN_HELP = "❓ Помощь"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FEED), KeyboardButton(text=BTN_FAVORITES)],
            [KeyboardButton(text=BTN_JOBS), KeyboardButton(text=BTN_GIGS)],
            [KeyboardButton(text=BTN_UPWORK), KeyboardButton(text=BTN_SEARCH)],
            [KeyboardButton(text=BTN_FILTERS), KeyboardButton(text=BTN_SOURCES)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_DIGEST)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел или введи запрос для поиска",
    )


# --- Коллбэки ---------------------------------------------------------------
class VacancyCB(CallbackData, prefix="vac"):
    action: str          # fav | hide | full | apply
    vacancy_id: int


class FeedCB(CallbackData, prefix="feed"):
    action: str          # page | refresh | close
    mode: str            # new | job | gig | fav | search
    offset: int = 0


class FilterCB(CallbackData, prefix="flt"):
    action: str          # menu | toggle | set | reset
    field: str = ""
    value: str = ""


class SourceCB(CallbackData, prefix="src"):
    action: str          # list | toggle | check | delete | add | discover | dgroup
    source_id: int = 0
    page: int = 0
    group: str = ""      # направление поиска для discover


def vacancy_actions(vacancy_id: int, url: str = "", *, is_favorite: bool = False) -> InlineKeyboardMarkup:
    """Кнопки под карточкой вакансии."""
    first_row: list[InlineKeyboardButton] = []
    if url:
        first_row.append(InlineKeyboardButton(text="🔗 Открыть оригинал", url=url))

    star = "💛 В избранном" if is_favorite else "⭐️ В избранное"
    second_row = [
        InlineKeyboardButton(
            text=star, callback_data=VacancyCB(action="fav", vacancy_id=vacancy_id).pack()
        ),
        InlineKeyboardButton(
            text="🚫 Не то", callback_data=VacancyCB(action="hide", vacancy_id=vacancy_id).pack()
        ),
    ]
    third_row = [
        InlineKeyboardButton(
            text="📄 Полный текст",
            callback_data=VacancyCB(action="full", vacancy_id=vacancy_id).pack(),
        ),
        InlineKeyboardButton(
            text="✅ Откликнулся",
            callback_data=VacancyCB(action="apply", vacancy_id=vacancy_id).pack(),
        ),
    ]

    rows = [row for row in (first_row, second_row, third_row) if row]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def feed_navigation(mode: str, offset: int, total: int, page_size: int) -> InlineKeyboardMarkup:
    """Пагинация ленты. Показывается, только если есть куда листать."""
    buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=FeedCB(
                    action="page", mode=mode, offset=max(0, offset - page_size)
                ).pack(),
            )
        )

    current = offset // page_size + 1
    pages = max(1, (total + page_size - 1) // page_size)
    buttons.append(
        InlineKeyboardButton(
            text=f"{current}/{pages}",
            callback_data=FeedCB(action="refresh", mode=mode, offset=offset).pack(),
        )
    )

    if offset + page_size < total:
        buttons.append(
            InlineKeyboardButton(
                text="Дальше ➡️",
                callback_data=FeedCB(action="page", mode=mode, offset=offset + page_size).pack(),
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def filters_menu(profile) -> InlineKeyboardMarkup:
    """Главное меню фильтров с текущими значениями прямо на кнопках."""
    roles = f"{len(profile.roles)} выбрано" if profile.roles else "все"
    grades = f"{len(profile.seniorities)} выбрано" if profile.seniorities else "все"
    modes = f"{len(profile.work_modes)} выбрано" if profile.work_modes else "все"
    kinds = f"{len(profile.kinds)} выбрано" if profile.kinds else "все"
    gigs = f"{len(profile.gig_categories)} выбрано" if profile.gig_categories else "все"
    years = f"до {profile.max_required_years} лет" if profile.max_required_years else "любой"
    quality = f"{len(profile.quality_filters or {})} условий" if profile.quality_filters else "не важно"
    alerts_state = "включены" if profile.alert_enabled else "выключены"
    salary = f"от ${profile.min_salary_usd}" if profile.min_salary_usd else "без ограничений"
    notify = "включены" if profile.notify_enabled else "выключены"

    rows = [
        [InlineKeyboardButton(
            text=f"🎯 Порог релевантности: {profile.min_score}%",
            callback_data=FilterCB(action="menu", field="score").pack())],
        [InlineKeyboardButton(
            text=f"💻 Специализация: {roles}",
            callback_data=FilterCB(action="menu", field="roles").pack())],
        [InlineKeyboardButton(
            text=f"🎓 Уровень: {grades}",
            callback_data=FilterCB(action="menu", field="seniorities").pack())],
        [InlineKeyboardButton(
            text=f"🌍 Формат работы: {modes}",
            callback_data=FilterCB(action="menu", field="work_modes").pack())],
        [InlineKeyboardButton(
            text=f"📋 Тип поста: {kinds}",
            callback_data=FilterCB(action="menu", field="kinds").pack())],
        [InlineKeyboardButton(
            text=f"🧩 Направления заказов: {gigs}",
            callback_data=FilterCB(action="menu", field="gig_categories").pack())],
        [InlineKeyboardButton(
            text=f"📅 Требуемый опыт: {years}",
            callback_data=FilterCB(action="menu", field="years").pack())],
        [InlineKeyboardButton(
            text=f"🎯 Качество заказа: {quality}",
            callback_data=FilterCB(action="menu", field="quality").pack())],
        [InlineKeyboardButton(
            text=f"🔥 Срочные алерты: {alerts_state}",
            callback_data=FilterCB(action="menu", field="alerts").pack())],
        [InlineKeyboardButton(
            text=f"💰 Минимальная оплата: {salary}",
            callback_data=FilterCB(action="menu", field="salary").pack())],
        [InlineKeyboardButton(
            text=f"🔑 Мои ключевые слова: {len(profile.keywords)}",
            callback_data=FilterCB(action="menu", field="keywords").pack())],
        [InlineKeyboardButton(
            text=f"🚷 Стоп-слова: {len(profile.stopwords)}",
            callback_data=FilterCB(action="menu", field="stopwords").pack())],
        [InlineKeyboardButton(
            text=f"🔔 Уведомления: {notify}",
            callback_data=FilterCB(action="toggle", field="notify").pack())],
        [InlineKeyboardButton(
            text="♻️ Сбросить всё",
            callback_data=FilterCB(action="reset").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _toggle_rows(field: str, options: list[tuple[str, str]], selected: list[str]) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for value, label in options:
        mark = "✅" if value in selected else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=FilterCB(action="toggle", field=field, value=value).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack()
        )
    ])
    return rows


def roles_menu(selected: list[str]) -> InlineKeyboardMarkup:
    options = [(str(r), f"{r.emoji} {r.label}") for r in Role if r is not Role.OTHER_ROLE]
    return InlineKeyboardMarkup(inline_keyboard=_toggle_rows("roles", options, selected))


def seniority_menu(selected: list[str]) -> InlineKeyboardMarkup:
    options = [(str(s), s.label) for s in Seniority if s is not Seniority.UNKNOWN]
    return InlineKeyboardMarkup(inline_keyboard=_toggle_rows("seniorities", options, selected))


def work_mode_menu(selected: list[str]) -> InlineKeyboardMarkup:
    options = [(str(m), f"{m.emoji} {m.label}") for m in WorkMode if m is not WorkMode.UNKNOWN]
    return InlineKeyboardMarkup(inline_keyboard=_toggle_rows("work_modes", options, selected))


def kinds_menu(selected: list[str]) -> InlineKeyboardMarkup:
    options = [
        (str(k), f"{k.emoji} {k.label}")
        for k in (PostKind.JOB, PostKind.GIG, PostKind.INTERNSHIP)
    ]
    return InlineKeyboardMarkup(inline_keyboard=_toggle_rows("kinds", options, selected))


def gig_categories_menu(selected: list[str]) -> InlineKeyboardMarkup:
    """Направления заказов. Отдельная кнопка «только код» — самый частый выбор
    для разработчика, вручную отмечать восемь пунктов неудобно."""
    options = [(str(c), f"{c.emoji} {c.label}") for c in GigCategory]
    rows = _toggle_rows("gig_categories", options, selected)
    rows.insert(0, [
        InlineKeyboardButton(
            text="⚡️ Только про код",
            callback_data=FilterCB(action="set", field="gig_categories", value="__coding__").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def score_menu(current: int) -> InlineKeyboardMarkup:
    rows = []
    for value in (30, 45, 55, 65, 75, 85):
        mark = "🔘" if value == current else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} от {value}%",
                callback_data=FilterCB(action="set", field="score", value=str(value)).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def salary_menu(current: int) -> InlineKeyboardMarkup:
    rows = []
    for value in (0, 500, 1000, 1500, 2000, 3000):
        mark = "🔘" if value == current else "▫️"
        label = "не важно" if value == 0 else f"от ${value}/мес"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=FilterCB(action="set", field="salary", value=str(value)).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def words_menu(field: str, words: list[str]) -> InlineKeyboardMarkup:
    """Список слов с кнопками удаления и предложением добавить."""
    rows: list[list[InlineKeyboardButton]] = []
    for word in words[:20]:
        rows.append([
            InlineKeyboardButton(
                text=f"❌ {word}",
                callback_data=FilterCB(action="toggle", field=field, value=word).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="➕ Добавить слово",
            callback_data=FilterCB(action="set", field=field, value="__add__").pack(),
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sources_menu(sources, page: int, pages: int) -> InlineKeyboardMarkup:
    """Список источников: тап по строке включает и выключает."""
    rows: list[list[InlineKeyboardButton]] = []
    for source in sources:
        mark = "🟢" if source.enabled else "⚪️"
        warn = " ⚠️" if source.error_count > 2 else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {source.display_name[:32]}{warn}",
                callback_data=SourceCB(action="toggle", source_id=source.id, page=page).pack(),
            )
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️", callback_data=SourceCB(action="list", page=page - 1).pack()))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{max(1, pages)}", callback_data=SourceCB(action="list", page=page).pack()))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(
            text="➡️", callback_data=SourceCB(action="list", page=page + 1).pack()))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton(text="➕ Добавить канал",
                            callback_data=SourceCB(action="add").pack()),
        InlineKeyboardButton(text="🔎 Найти новые",
                            callback_data=SourceCB(action="dgroup").pack()),
    ])
    rows.append([
        InlineKeyboardButton(text="🩺 Проверить все",
                            callback_data=SourceCB(action="check").pack()),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def discovery_groups_menu() -> InlineKeyboardMarkup:
    """Выбор направления поиска. Узкий поиск даёт заметно меньше шума,
    чем один общий проход по всем запросам сразу."""
    from app.sources.manager import DISCOVERY_GROUP_LABELS

    rows = [
        [InlineKeyboardButton(
            text=label,
            callback_data=SourceCB(action="discover", group=key).pack(),
        )]
        for key, label in DISCOVERY_GROUP_LABELS.items()
    ]
    rows.append([
        InlineKeyboardButton(
            text="🌐 Искать везде (дольше, шумнее)",
            callback_data=SourceCB(action="discover").pack(),
        )
    ])
    rows.append([
        InlineKeyboardButton(text="⬅️ К источникам",
                            callback_data=SourceCB(action="list").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_add_sources(candidates: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Кнопки для найденных при автопоиске каналов."""
    rows = [
        [InlineKeyboardButton(
            text=f"➕ {title[:40]}",
            callback_data=SourceCB(action="add", source_id=index).pack(),
        )]
        for index, (_, title) in enumerate(candidates[:15])
    ]
    rows.append([
        InlineKeyboardButton(text="⬅️ К источникам",
                            callback_data=SourceCB(action="list").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def years_menu(current: int) -> InlineKeyboardMarkup:
    """Потолок требуемого опыта. Посты без указания опыта проходят всегда."""
    rows = []
    for value, label in [(0, "не важно"), (1, "до 1 года"), (2, "до 2 лет"),
                         (3, "до 3 лет"), (5, "до 5 лет")]:
        mark = "🔘" if value == current else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {label}",
                callback_data=FilterCB(action="set", field="years", value=str(value)).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quality_menu(quality: dict) -> InlineKeyboardMarkup:
    """Фильтры качества заказа — те же, что в самом Upwork."""
    verified = quality.get("payment_verified")
    max_prop = quality.get("max_proposals")
    rating = quality.get("min_client_rating")

    rows = [
        [InlineKeyboardButton(
            text=f"{'✅' if verified else '▫️'} Только с подтверждённой оплатой",
            callback_data=FilterCB(action="toggle", field="q_verified").pack())],
    ]
    for value in (5, 10, 20, 50):
        mark = "🔘" if max_prop == value else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} Откликов не больше {value}",
                callback_data=FilterCB(action="set", field="q_proposals", value=str(value)).pack())
        ])
    for value, label in ((4.5, "рейтинг клиента от 4.5"), (4.0, "рейтинг клиента от 4.0")):
        mark = "🔘" if rating == value else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {label.capitalize()}",
                callback_data=FilterCB(action="set", field="q_rating", value=str(value)).pack())
        ])
    rows.append([
        InlineKeyboardButton(
            text="♻️ Сбросить условия качества",
            callback_data=FilterCB(action="set", field="q_reset", value="1").pack())
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alerts_menu(profile) -> InlineKeyboardMarkup:
    """Настройка срочных уведомлений «звёзды сошлись»."""
    from app.pipeline.alerts import rules_for

    rules = rules_for(profile)
    rows = [
        [InlineKeyboardButton(
            text=f"{'🔥 Алерты включены' if profile.alert_enabled else '💤 Алерты выключены'}",
            callback_data=FilterCB(action="toggle", field="alert_on").pack())],
    ]
    for value in (70, 80, 90):
        mark = "🔘" if rules.get("min_score") == value else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} Только от {value}% релевантности",
                callback_data=FilterCB(action="set", field="a_score", value=str(value)).pack())
        ])
    for value in (5, 15, 30):
        mark = "🔘" if rules.get("max_proposals") == value else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} Откликов меньше {value}",
                callback_data=FilterCB(action="set", field="a_proposals", value=str(value)).pack())
        ])
    for value in (3, 5, 10):
        mark = "🔘" if rules.get("daily_limit") == value else "▫️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} Не больше {value} в сутки",
                callback_data=FilterCB(action="set", field="a_limit", value=str(value)).pack())
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К фильтрам", callback_data=FilterCB(action="menu", field="root").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
