"""Оркестратор: сырой пост -> запись в базе.

Порядок шагов важен и выбран так, чтобы дорогие операции стояли последними:
    забрать -> отсеять уже виденное -> разобрать -> оценить правилами ->
    отбросить мусор -> дедуп -> LLM для спорных -> сохранить.

Мусор отсекается до дедупа и до LLM, поэтому и база, и счёт за модель
остаются небольшими.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.core.enums import PostKind, SalaryPeriod, Seniority, SourceKind, WorkMode
from app.core.models import Source, Vacancy
from app.core.schemas import RawPost
from app.pipeline import dedup
from app.pipeline.extractors import Extracted, extract_all
from app.pipeline.llm import LlmRequest, get_classifier
from app.pipeline.scoring import ScoreResult, score_post
from app.pipeline.text import (
    clean_for_display,
    detect_lang,
    first_line,
    normalize_for_match,
)
from app.sources import registry
from app.sources.base import BaseSource, SourceError

log = logging.getLogger(__name__)

# Окно, в котором ищем дубликаты. Вакансия старше двух недель всё равно неактуальна.
DEDUP_WINDOW_DAYS = 14

# Биржи называют период по-разному: «yearly», «annual», «per year».
# Без этой таблицы годовая вилка показывалась бы как месячная.
_PERIOD_ALIASES: dict[str, SalaryPeriod] = {
    "hour": SalaryPeriod.HOUR, "hourly": SalaryPeriod.HOUR, "per hour": SalaryPeriod.HOUR,
    "day": SalaryPeriod.DAY, "daily": SalaryPeriod.DAY,
    "month": SalaryPeriod.MONTH, "monthly": SalaryPeriod.MONTH,
    "year": SalaryPeriod.YEAR, "yearly": SalaryPeriod.YEAR, "annual": SalaryPeriod.YEAR,
    "annually": SalaryPeriod.YEAR, "per year": SalaryPeriod.YEAR,
    "project": SalaryPeriod.PROJECT, "fixed": SalaryPeriod.PROJECT,
}


@dataclass(slots=True)
class ProcessStats:
    """Итог обхода одного источника — из него собирается статистика в боте."""

    source: str = ""
    fetched: int = 0
    skipped_known: int = 0
    rejected: int = 0
    duplicates: int = 0
    llm_checked: int = 0
    saved: int = 0
    error: str = ""
    new_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(slots=True)
class _Candidate:
    """Пост, доживший до сохранения."""

    post: RawPost
    data: Extracted
    score: ScoreResult
    clean_text: str
    normalized: str
    title: str


async def _known_external_ids(session, source_id: int, ids: list[str]) -> set[str]:
    """Какие из этих постов уже лежат в базе."""
    if not ids:
        return set()
    rows = await session.execute(
        select(Vacancy.external_id).where(
            Vacancy.source_id == source_id, Vacancy.external_id.in_(ids)
        )
    )
    return {row[0] for row in rows}


async def _recent_fingerprints(session) -> list[tuple[int, str, int]]:
    """Отпечатки недавних записей для поиска перепечаток."""
    since = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    rows = await session.execute(
        select(Vacancy.id, Vacancy.content_hash, Vacancy.simhash)
        .where(Vacancy.posted_at >= since, Vacancy.duplicate_of.is_(None))
        .order_by(Vacancy.posted_at.desc())
        .limit(4000)
    )
    return [(r[0], r[1] or "", r[2] or 0) for r in rows]


def _build_title(post: RawPost, data: Extracted) -> str:
    """Заголовок карточки: у бирж он есть, у Telegram — берём первую строку."""
    if post.title.strip():
        return post.title.strip()[:200]

    line = first_line(post.text, 110)
    if line:
        return line
    role = data.roles[0] if data.roles else ""
    return f"{data.kind.label}{f' · {role}' if role else ''}"


async def process_source(session, record: Source) -> ProcessStats:
    """Обходит один источник и сохраняет всё, что прошло фильтры."""
    settings = get_settings()
    stats = ProcessStats(source=record.display_name)

    source: BaseSource | None = registry.build(record)
    if source is None:
        stats.error = "нет реализации для этого типа источника"
        return stats

    # Биржи фильтруем по времени последнего обхода, Telegram — по id сообщения.
    since = None
    if record.kind is not SourceKind.TELEGRAM and record.last_fetch_at:
        last_fetch_at = record.last_fetch_at
        # SQLite не хранит offset: значение возвращается наивным, хотя колонка
        # объявлена как timezone-aware. Без этого datetime ниже по цепочке
        # падает при сравнении с aware-временем из источников.
        if last_fetch_at.tzinfo is None:
            last_fetch_at = last_fetch_at.replace(tzinfo=timezone.utc)
        since = last_fetch_at - timedelta(hours=1)  # нахлёст на случай сбоя

    posts: list[RawPost] = []
    seen_ids: list[str] = []
    try:
        async for post in source.fetch(since):
            seen_ids.append(post.external_id)
            posts.append(post)
    except SourceError as exc:
        stats.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - источник не должен ронять весь обход
        stats.error = f"{type(exc).__name__}: {exc}"
        log.exception("Ошибка при обходе %s", record.identifier)

    stats.fetched = len(posts)
    if not posts:
        record.last_fetch_at = datetime.now(timezone.utc)
        if stats.error:
            record.error_count += 1
            record.last_error = stats.error[:500]
        return stats

    # --- Уже виденное ------------------------------------------------------
    # Ленты иногда отдают один и тот же элемент дважды в одной выдаче, поэтому
    # внутри пачки тоже нужен отсев — иначе ловим нарушение уникальности.
    known = await _known_external_ids(session, record.id, seen_ids)
    fresh: list[RawPost] = []
    batch_seen: set[str] = set()
    for post in posts:
        if post.external_id in known or post.external_id in batch_seen:
            continue
        batch_seen.add(post.external_id)
        fresh.append(post)
    stats.skipped_known = len(posts) - len(fresh)

    # --- Разбор и оценка по правилам ---------------------------------------
    candidates: list[_Candidate] = []
    for post in fresh:
        raw = post.searchable()
        normalized = normalize_for_match(raw)

        data = extract_all(
            post.text,
            normalized,
            title=post.title,
            author=post.author,
            seniority_hint=post.seniority_hint,
            employment_hint=post.employment_hint,
            known_locations=post.locations,
            is_remote_hint=post.is_remote,
        )
        # Метрики качества площадка знает точно — просто переносим их.
        data.proposals_min = post.proposals_min
        data.proposals_max = post.proposals_max
        data.client_payment_verified = post.client_payment_verified
        data.client_rating = post.client_rating
        data.client_reviews = post.client_reviews
        data.client_spent = post.client_spent
        data.client_hires = post.client_hires
        data.client_country = post.client_country
        data.experience_level = post.experience_level

        # Структурированную вилку от биржи не переоцениваем регулярками.
        if post.salary_min or post.salary_max:
            data.salary.min = post.salary_min or data.salary.min
            data.salary.max = post.salary_max or data.salary.max
            if post.salary_currency:
                data.salary.currency = post.salary_currency
            raw_period = (post.salary_period or "").strip().lower()
            if raw_period in ("week", "weekly"):
                # Недельной ставки в enum нет — приводим сумму к месяцу.
                for field in ("min", "max"):
                    value = getattr(data.salary, field)
                    if value:
                        setattr(data.salary, field, round(value * 4.33, 2))
                data.salary.period = SalaryPeriod.MONTH
            elif raw_period:
                period = _PERIOD_ALIASES.get(raw_period)
                if period is not None:
                    data.salary.period = period
            if not data.salary.raw:
                # Вилку показываем только когда границы действительно разные.
                low, high = post.salary_min, post.salary_max
                parts = [f"{v:,.0f}".replace(",", " ") for v in (low, high) if v]
                unique = list(dict.fromkeys(parts))
                data.salary.raw = " – ".join(unique)

        score = score_post(
            data,
            posted_at=post.posted_at,
            text_length=len(post.text),
            normalized_text=normalized,
            source_priority=record.priority,
        )

        if score.rejected or score.score < settings.min_score_store:
            stats.rejected += 1
            continue

        candidates.append(
            _Candidate(
                post=post,
                data=data,
                score=score,
                clean_text=clean_for_display(post.text),
                normalized=normalized,
                title=_build_title(post, data),
            )
        )

    if not candidates:
        record.last_fetch_at = datetime.now(timezone.utc)
        record.posts_seen += len(posts)
        return stats

    # --- LLM для спорных ---------------------------------------------------
    classifier = get_classifier()
    if classifier.enabled:
        borderline = [
            c
            for c in candidates
            if settings.llm_score_low <= c.score.score <= settings.llm_score_high
        ]
        if borderline:
            requests = [
                LlmRequest(
                    id=f"{record.id}:{c.post.external_id}",
                    title=c.title,
                    text=c.clean_text,
                    source=record.display_name,
                    rule_score=c.score.score,
                )
                for c in borderline
            ]
            verdicts = await classifier.classify(requests)
            stats.llm_checked = len(verdicts)

            for candidate in borderline:
                verdict = verdicts.get(f"{record.id}:{candidate.post.external_id}")
                if verdict is None:
                    continue
                _apply_verdict(candidate, verdict)

    # Модель могла понизить оценку ниже порога хранения.
    survivors = [c for c in candidates if c.score.score >= settings.min_score_store]
    stats.rejected += len(candidates) - len(survivors)

    # --- Дедупликация ------------------------------------------------------
    fingerprints = await _recent_fingerprints(session)

    for candidate in survivors:
        content_hash = dedup.content_hash(candidate.post.text)
        simhash = dedup.simhash(candidate.post.text)
        duplicate_of = dedup.find_duplicate(content_hash, simhash, fingerprints)

        vacancy = _build_vacancy(record, candidate, content_hash, simhash, duplicate_of)
        session.add(vacancy)
        await session.flush()  # нужен id для дальнейшей рассылки

        if duplicate_of is not None:
            stats.duplicates += 1
        else:
            fingerprints.append((vacancy.id, content_hash, simhash))
            stats.saved += 1
            stats.new_ids.append(vacancy.id)

    # --- Состояние источника -----------------------------------------------
    record.last_external_id = source.merge_cursor(record.last_external_id, seen_ids)
    record.last_fetch_at = datetime.now(timezone.utc)
    record.posts_seen += len(posts)
    record.posts_kept += stats.saved
    if stats.ok:
        record.error_count = 0
        record.last_error = None
    else:
        record.error_count += 1
        record.last_error = stats.error[:500]

    return stats


def _apply_verdict(candidate: _Candidate, verdict) -> None:
    """Смешивает вердикт модели с оценкой по правилам.

    Правилам оставлен вес: модель видит только текст, а правила знают ещё и
    про свежесть, источник и персональные ключевые слова. Поэтому берётся
    взвешенное среднее, а не просто оценка модели.
    """
    candidate.score.reasons.append(f"LLM: {verdict.reason}")

    try:
        kind = PostKind(verdict.kind)
    except ValueError:
        kind = candidate.data.kind

    # Отсев мусора — единственный случай, где модель решает единолично.
    if kind in (PostKind.RESUME, PostKind.OTHER):
        candidate.data.kind = kind
        candidate.score.score = 0
        candidate.score.rejected = True
        candidate.score.reject_reason = verdict.reason
        return

    candidate.data.kind = kind
    if verdict.seniority != "unknown":
        try:
            candidate.data.seniority = Seniority(verdict.seniority)
        except ValueError:
            pass
    if verdict.remote and candidate.data.work_mode is WorkMode.UNKNOWN:
        candidate.data.work_mode = WorkMode.REMOTE

    blended = round(candidate.score.score * 0.4 + verdict.score * 0.6)
    candidate.score.score = max(0, min(100, blended))


def _build_vacancy(
    record: Source,
    candidate: _Candidate,
    content_hash: str,
    simhash: int,
    duplicate_of: int | None,
) -> Vacancy:
    post, data = candidate.post, candidate.data
    return Vacancy(
        source_id=record.id,
        external_id=post.external_id,
        url=post.url,
        title=candidate.title,
        raw_text=post.text,
        clean_text=candidate.clean_text,
        lang=detect_lang(post.text),
        company=post.company,
        author=post.author,
        contacts=data.contacts,
        kind=data.kind,
        gig_category=str(data.gig_category),
        required_years=data.required_years,
        proposals_min=data.proposals_min,
        proposals_max=data.proposals_max,
        client_payment_verified=data.client_payment_verified,
        client_rating=data.client_rating,
        client_reviews=data.client_reviews,
        client_spent=data.client_spent,
        client_hires=data.client_hires,
        client_country=data.client_country,
        experience_level=data.experience_level,
        roles=data.roles,
        stack=data.stack,
        seniority=data.seniority,
        employment=data.employment,
        work_mode=data.work_mode,
        locations=data.locations,
        salary_min=data.salary.min,
        salary_max=data.salary.max,
        salary_currency=data.salary.currency,
        salary_period=data.salary.period,
        salary_raw=data.salary.raw,
        salary_usd_month=data.salary.usd_month,
        score=candidate.score.score,
        score_reasons=candidate.score.reasons,
        llm_checked=any(r.startswith("LLM:") for r in candidate.score.reasons),
        content_hash=content_hash,
        simhash=simhash,
        duplicate_of=duplicate_of,
        posted_at=post.posted_at,
    )
