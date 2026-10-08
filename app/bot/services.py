"""Запросы к базе, которыми пользуются хендлеры бота."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.enums import PostKind, Seniority, SourceKind, UserStatus, WorkMode
from app.core.models import Source, UserProfile, UserVacancy, Vacancy

log = logging.getLogger(__name__)

PAGE_SIZE = 5


async def get_or_create_profile(session: AsyncSession, tg_id: int, username: str = "") -> UserProfile:
    """Профиль получателя. Создаётся при первом обращении с разумными настройками."""
    profile = await session.scalar(select(UserProfile).where(UserProfile.tg_id == tg_id))
    if profile is None:
        settings = get_settings()
        profile = UserProfile(
            tg_id=tg_id,
            username=username,
            min_score=settings.min_score_notify,
        )
        session.add(profile)
        await session.flush()
        log.info("Создан профиль для %s (%s)", tg_id, username or "без ника")
    elif username and profile.username != username:
        profile.username = username
    return profile


def _only_enabled_sources(query: Select) -> Select:
    """Оставляет посты только включённых источников.

    Выключение источника — это «я больше не хочу видеть его посты», поэтому
    правило действует и на то, что уже лежит в базе: иначе выключенный канал
    ещё двумя неделями отдавал бы вакансии в ленту. Посты не удаляются, и
    после повторного включения источника они вернутся.
    """
    return query.where(
        Vacancy.source_id.in_(select(Source.id).where(Source.enabled.is_(True)))
    )


def _apply_profile_filters(query: Select, profile: UserProfile, *, skip_kind: bool = False) -> Select:
    """Накладывает пользовательские фильтры на выборку вакансий.

    skip_kind=True — для вкладок «Вакансии»/«Заказы»: тип поста там уже задан
    явно кнопкой, и персональный фильтр «Тип поста» из ⚙️ не должен с ним
    спорить (иначе выбор в фильтрах мог бы съесть всё содержимое вкладки).
    """
    query = _only_enabled_sources(query)
    query = query.where(Vacancy.score >= profile.min_score)

    # «Не указано» — не то же самое, что «не подходит»: грейд и формат работы
    # авторы пишут далеко не всегда, и такие посты составляют большинство.
    # Фильтр должен убирать заведомо чужое (senior, офис), а не всё, что не
    # размечено, иначе выбор пары галочек опустошает ленту.
    if profile.seniorities:
        query = query.where(
            or_(
                Vacancy.seniority.in_(profile.seniorities),
                Vacancy.seniority == Seniority.UNKNOWN,
            )
        )
    if profile.work_modes:
        query = query.where(
            or_(
                Vacancy.work_mode.in_(profile.work_modes),
                Vacancy.work_mode == WorkMode.UNKNOWN,
            )
        )
    if profile.kinds and not skip_kind:
        query = query.where(Vacancy.kind.in_(profile.kinds))

    # Направления заказов ограничивают только сами заказы: вакансия с категорией
    # «other» (у неё направление не размечается) не должна из-за этого пропасть.
    if profile.gig_categories:
        query = query.where(
            or_(
                Vacancy.kind != PostKind.GIG,
                Vacancy.gig_category.in_(profile.gig_categories),
            )
        )

    # Потолок требуемого опыта. Посты, где опыт не назван, проходят: таких
    # большинство, и отсекать их значило бы выкинуть почти всю ленту.
    if profile.max_required_years:
        query = query.where(
            or_(
                Vacancy.required_years <= profile.max_required_years,
                Vacancy.required_years.is_(None),
            )
        )

    # Фильтры качества заказа. Каждый пропускает посты без соответствующих
    # данных — их не знает никто, кроме Upwork, и требовать их ото всех нельзя.
    quality = profile.quality_filters or {}
    if quality.get("payment_verified"):
        query = query.where(
            or_(
                Vacancy.client_payment_verified.is_(True),
                Vacancy.client_payment_verified.is_(None),
            )
        )
    if quality.get("max_proposals"):
        query = query.where(
            or_(
                Vacancy.proposals_max <= int(quality["max_proposals"]),
                Vacancy.proposals_max.is_(None),
            )
        )
    if quality.get("min_client_rating"):
        query = query.where(
            or_(
                Vacancy.client_rating >= float(quality["min_client_rating"]),
                Vacancy.client_rating.is_(None),
            )
        )
    if quality.get("experience_levels"):
        query = query.where(
            or_(
                Vacancy.experience_level.in_(quality["experience_levels"]),
                Vacancy.experience_level == "",
            )
        )

    if profile.min_salary_usd:
        threshold = Vacancy.salary_usd_month >= profile.min_salary_usd
        # Посты без вилки чаще всего нормальные — по умолчанию не выбрасываем.
        query = query.where(
            or_(threshold, Vacancy.salary_usd_month.is_(None))
            if profile.allow_no_salary
            else threshold
        )

    # Роли лежат в JSON-массиве: точного оператора нет, поэтому ищем подстроку.
    if profile.roles:
        conditions = [Vacancy.roles.cast(String).like(f'%"{role}"%') for role in profile.roles]
        query = query.where(or_(*conditions))

    return query


async def _hidden_ids(session: AsyncSession, profile: UserProfile) -> list[int]:
    rows = await session.execute(
        select(UserVacancy.vacancy_id).where(
            UserVacancy.user_id == profile.id,
            UserVacancy.status.in_([UserStatus.HIDDEN, UserStatus.APPLIED]),
        )
    )
    return [row[0] for row in rows]


async def feed_page(
    session: AsyncSession,
    profile: UserProfile,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    days: int = 14,
    kinds: list[str] | None = None,
) -> tuple[list[tuple[Vacancy, Source | None]], int]:
    """Лента подходящих вакансий, новые сверху. Возвращает страницу и общий счёт.

    kinds — явное ограничение по типу поста для вкладок «Вакансии»/«Заказы»;
    если задано, персональный фильтр «Тип поста» из ⚙️ на эту выборку не влияет.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    hidden = await _hidden_ids(session, profile)

    base = (
        select(Vacancy)
        .where(Vacancy.posted_at >= since, Vacancy.duplicate_of.is_(None))
    )
    base = _apply_profile_filters(base, profile, skip_kind=bool(kinds))
    if kinds:
        base = base.where(Vacancy.kind.in_(kinds))
    if hidden:
        base = base.where(Vacancy.id.notin_(hidden))

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = await session.execute(
        base.order_by(Vacancy.posted_at.desc()).offset(offset).limit(limit)
    )
    vacancies = list(rows.scalars())
    return await _attach_sources(session, vacancies), total


async def upwork_page(
    session: AsyncSession,
    profile: UserProfile,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    days: int = 14,
) -> tuple[list[tuple[Vacancy, Source | None]], int]:
    """Заказы только с Upwork, самые «горячие» сверху.

    Сортировка здесь другая, чем в ленте: не по свежести, а по числу откликов.
    На бирже решает не то, что заказ новый, а то, что на него ещё мало кто
    откликнулся — именно туда есть смысл писать.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    hidden = await _hidden_ids(session, profile)

    base = (
        select(Vacancy)
        .join(Source, Source.id == Vacancy.source_id)
        .where(
            Source.kind == SourceKind.UPWORK,
            Source.enabled.is_(True),
            Vacancy.posted_at >= since,
            Vacancy.duplicate_of.is_(None),
            Vacancy.score >= profile.min_score,
        )
    )
    if hidden:
        base = base.where(Vacancy.id.notin_(hidden))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.execute(
        base.order_by(
            # NULLS LAST вручную: у части заказов число откликов неизвестно.
            (Vacancy.proposals_max.is_(None)).asc(),
            Vacancy.proposals_max.asc(),
            Vacancy.posted_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return await _attach_sources(session, list(rows.scalars())), total


async def favorites_page(
    session: AsyncSession, profile: UserProfile, *, offset: int = 0, limit: int = PAGE_SIZE
) -> tuple[list[tuple[Vacancy, Source | None]], int]:
    base = (
        select(Vacancy)
        .join(UserVacancy, UserVacancy.vacancy_id == Vacancy.id)
        .where(UserVacancy.user_id == profile.id, UserVacancy.status == UserStatus.FAVORITE)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.execute(
        base.order_by(UserVacancy.created_at.desc()).offset(offset).limit(limit)
    )
    return await _attach_sources(session, list(rows.scalars())), total


async def search_page(
    session: AsyncSession,
    profile: UserProfile,
    query_text: str,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> tuple[list[tuple[Vacancy, Source | None]], int]:
    """Поиск по сохранённым постам. Порог релевантности здесь снижен:
    если человек ищет явно, показываем и то, что не прошло бы в ленту.
    Выключенные источники не показываем и здесь — иначе поиск возвращал бы
    то, от чего пользователь только что отписался."""
    pattern = f"%{query_text.strip().lower()}%"
    base = _only_enabled_sources(
        select(Vacancy).where(
            Vacancy.duplicate_of.is_(None),
            or_(
                func.lower(Vacancy.title).like(pattern),
                func.lower(Vacancy.clean_text).like(pattern),
                func.lower(Vacancy.company).like(pattern),
            ),
        )
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.execute(
        base.order_by(Vacancy.score.desc(), Vacancy.posted_at.desc()).offset(offset).limit(limit)
    )
    return await _attach_sources(session, list(rows.scalars())), total


async def _attach_sources(
    session: AsyncSession, vacancies: list[Vacancy]
) -> list[tuple[Vacancy, Source | None]]:
    """Подтягивает источники одним запросом, чтобы не дёргать базу в цикле."""
    if not vacancies:
        return []
    ids = {v.source_id for v in vacancies}
    rows = await session.execute(select(Source).where(Source.id.in_(ids)))
    by_id = {s.id: s for s in rows.scalars()}
    return [(v, by_id.get(v.source_id)) for v in vacancies]


async def get_vacancy(session: AsyncSession, vacancy_id: int) -> tuple[Vacancy | None, Source | None]:
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        return None, None
    source = await session.get(Source, vacancy.source_id)
    return vacancy, source


async def set_status(
    session: AsyncSession, profile: UserProfile, vacancy_id: int, status: UserStatus
) -> UserStatus:
    """Ставит отметку. Повторное нажатие на «в избранное» снимает её."""
    link = await session.scalar(
        select(UserVacancy).where(
            UserVacancy.user_id == profile.id, UserVacancy.vacancy_id == vacancy_id
        )
    )
    if link is None:
        link = UserVacancy(user_id=profile.id, vacancy_id=vacancy_id, status=status)
        session.add(link)
        return status

    if link.status == status == UserStatus.FAVORITE:
        link.status = UserStatus.SEEN
        return UserStatus.SEEN

    link.status = status
    return status


async def is_favorite(session: AsyncSession, profile: UserProfile, vacancy_id: int) -> bool:
    link = await session.scalar(
        select(UserVacancy).where(
            UserVacancy.user_id == profile.id, UserVacancy.vacancy_id == vacancy_id
        )
    )
    return bool(link and link.status == UserStatus.FAVORITE)


async def mark_sent(
    session: AsyncSession,
    profile: UserProfile,
    vacancy_ids: list[int],
    *,
    note: str = "",
) -> None:
    """Отмечает вакансии как отправленные, чтобы не прислать их повторно.

    note помечает способ доставки («alert» для срочных уведомлений) — по нему
    считается суточный лимит, чтобы бот не будил десять раз за вечер.
    """
    if not vacancy_ids:
        return
    rows = await session.execute(
        select(UserVacancy.vacancy_id).where(
            UserVacancy.user_id == profile.id, UserVacancy.vacancy_id.in_(vacancy_ids)
        )
    )
    existing = {row[0] for row in rows}
    now = datetime.now(timezone.utc)
    for vacancy_id in vacancy_ids:
        if vacancy_id not in existing:
            session.add(
                UserVacancy(
                    user_id=profile.id,
                    vacancy_id=vacancy_id,
                    status=UserStatus.SEEN,
                    sent_at=now,
                    note=note,
                )
            )


async def unsent_for_user(
    session: AsyncSession, profile: UserProfile, vacancy_ids: list[int]
) -> list[tuple[Vacancy, Source | None]]:
    """Из свежесохранённых оставляет то, что подходит под фильтры и ещё не отправлено."""
    if not vacancy_ids:
        return []

    seen_rows = await session.execute(
        select(UserVacancy.vacancy_id).where(
            UserVacancy.user_id == profile.id, UserVacancy.vacancy_id.in_(vacancy_ids)
        )
    )
    seen = {row[0] for row in seen_rows}
    fresh = [vid for vid in vacancy_ids if vid not in seen]
    if not fresh:
        return []

    base = select(Vacancy).where(Vacancy.id.in_(fresh), Vacancy.duplicate_of.is_(None))
    base = _apply_profile_filters(base, profile)
    rows = await session.execute(base.order_by(Vacancy.score.desc()))
    return await _attach_sources(session, list(rows.scalars()))


async def unhide_all(session: AsyncSession, profile: UserProfile) -> int:
    """Возвращает в ленту всё, что было скрыто кнопкой «Не то».

    Скрытие накапливается незаметно: пара десятков нажатий — и лента пустеет,
    хотя подходящие посты в базе есть. Отметку «Откликнулся» не трогаем — это
    осмысленная история, а не отказ.
    """
    rows = await session.execute(
        select(UserVacancy).where(
            UserVacancy.user_id == profile.id,
            UserVacancy.status == UserStatus.HIDDEN,
        )
    )
    hidden = list(rows.scalars())
    for link in hidden:
        link.status = UserStatus.SEEN
    return len(hidden)


async def collect_stats(session: AsyncSession, profile: UserProfile) -> dict:
    """Цифры для раздела «Статистика»."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    total = await session.scalar(select(func.count(Vacancy.id))) or 0
    today = await session.scalar(
        select(func.count(Vacancy.id)).where(Vacancy.fetched_at >= day_ago)
    ) or 0
    week = await session.scalar(
        select(func.count(Vacancy.id)).where(Vacancy.fetched_at >= week_ago)
    ) or 0
    duplicates = await session.scalar(
        select(func.count(Vacancy.id)).where(Vacancy.duplicate_of.isnot(None))
    ) or 0
    relevant = await session.scalar(
        select(func.count(Vacancy.id)).where(
            Vacancy.score >= profile.min_score, Vacancy.duplicate_of.is_(None)
        )
    ) or 0
    favorites = await session.scalar(
        select(func.count(UserVacancy.id)).where(
            UserVacancy.user_id == profile.id, UserVacancy.status == UserStatus.FAVORITE
        )
    ) or 0
    hidden = await session.scalar(
        select(func.count(UserVacancy.id)).where(
            UserVacancy.user_id == profile.id, UserVacancy.status == UserStatus.HIDDEN
        )
    ) or 0
    applied = await session.scalar(
        select(func.count(UserVacancy.id)).where(
            UserVacancy.user_id == profile.id, UserVacancy.status == UserStatus.APPLIED
        )
    ) or 0

    sources_total = await session.scalar(select(func.count(Source.id))) or 0
    sources_on = await session.scalar(
        select(func.count(Source.id)).where(Source.enabled.is_(True))
    ) or 0

    top_rows = await session.execute(
        select(Source.title, Source.identifier, Source.posts_kept, Source.posts_seen)
        .where(Source.posts_kept > 0)
        .order_by(Source.posts_kept.desc())
        .limit(5)
    )

    return {
        "total": total,
        "today": today,
        "week": week,
        "duplicates": duplicates,
        "relevant": relevant,
        "favorites": favorites,
        "hidden": hidden,
        "applied": applied,
        "sources_total": sources_total,
        "sources_on": sources_on,
        "top_sources": [
            {"name": title or identifier, "kept": kept, "seen": seen}
            for title, identifier, kept, seen in top_rows
        ],
    }
