"""Коннекторы к публичным job-бордам.

Все пять API отдают JSON без авторизации и без ключей — их можно включить
сразу. Каждый класс делает ровно одно: тянет список и раскладывает чужие
поля по RawPost. Вся логика оценки живёт в пайплайне.

Условия использования этих API требуют ссылаться на первоисточник —
бот всегда показывает ссылку на оригинал, так что требование выполняется.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.core.enums import SourceKind
from app.core.schemas import RawPost
from app.pipeline.text import strip_html
from app.sources.base import BaseSource, SourceError
from app.sources.registry import register

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


def _ts(value: object) -> datetime:
    """Приводит время в любом виде (epoch, ISO, пусто) к aware datetime UTC."""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return datetime.now(timezone.utc)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class HttpJobBoard(BaseSource):
    """Общая механика: сходить по URL, получить JSON, отдать элементы."""

    kind = SourceKind.JOBBOARD
    api_url: str = ""

    async def _get_json(self, url: str, params: dict | None = None) -> object:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"{self.display_name}: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.display_name}: сеть недоступна ({exc})") from exc
        except ValueError as exc:
            raise SourceError(f"{self.display_name}: ответ неJSON") from exc

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await self._get_json(self.api_url, self._verify_params())
        except SourceError as exc:
            return False, str(exc)
        count = len(data) if isinstance(data, list) else len(self._items(data))
        return True, f"OK — доступно, отдал {count} записей"

    def _verify_params(self) -> dict | None:
        return None

    def _items(self, data: object) -> list[dict]:
        return data if isinstance(data, list) else []

    def merge_cursor(self, previous: str | None, seen: list[str]) -> str | None:
        # У бордов курсор не нужен: повторы отсекает уникальный external_id,
        # а свежесть — фильтр по дате в пайплайне.
        return previous


@register("remoteok")
class RemoteOkSource(HttpJobBoard):
    """remoteok.com — крупный агрегатор удалённых вакансий."""

    display_name = "RemoteOK"
    api_url = "https://remoteok.com/api"

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        data = await self._get_json(self.api_url)
        if not isinstance(data, list):
            raise SourceError("RemoteOK: неожиданный формат ответа")

        for item in data:
            # Первый элемент ленты — юридическая пометка, не вакансия.
            if not isinstance(item, dict) or "position" not in item:
                continue
            posted = _ts(item.get("epoch") or item.get("date"))
            if since and posted < since:
                continue

            salary_min = float(item.get("salary_min") or 0) or None
            salary_max = float(item.get("salary_max") or 0) or None

            yield RawPost(
                external_id=str(item.get("id") or item.get("slug")),
                title=str(item.get("position") or ""),
                company=str(item.get("company") or ""),
                text=strip_html(str(item.get("description") or "")),
                posted_at=posted,
                url=str(item.get("url") or item.get("apply_url") or ""),
                tags=[str(t) for t in item.get("tags") or []],
                locations=[str(item.get("location") or "").strip()] if item.get("location") else [],
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency="USD" if salary_min or salary_max else "",
                # RemoteOK публикует «вилку в год».
                salary_period="year" if salary_min or salary_max else "",
                is_remote=True,
            )


@register("remotive")
class RemotiveSource(HttpJobBoard):
    """remotive.com — remote-вакансии с человеческой категоризацией."""

    display_name = "Remotive"
    api_url = "https://remotive.com/api/remote-jobs"

    def _verify_params(self) -> dict | None:
        return {"limit": 1}

    def _items(self, data: object) -> list[dict]:
        return data.get("jobs", []) if isinstance(data, dict) else []

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        # Несколько узких запросов дают больше релевантного, чем одна общая выдача.
        queries = self.options.get("queries") or ["python", "backend", "javascript", "fullstack"]
        limit = int(self.options.get("limit", 40))
        seen: set[str] = set()

        for query in queries:
            data = await self._get_json(self.api_url, {"search": query, "limit": limit})
            for item in self._items(data):
                job_id = str(item.get("id"))
                if job_id in seen:
                    continue
                seen.add(job_id)

                posted = _ts(item.get("publication_date"))
                if since and posted < since:
                    continue

                location = str(item.get("candidate_required_location") or "")
                yield RawPost(
                    external_id=job_id,
                    title=str(item.get("title") or ""),
                    company=str(item.get("company_name") or ""),
                    text=strip_html(str(item.get("description") or "")),
                    posted_at=posted,
                    url=str(item.get("url") or ""),
                    tags=[str(t) for t in item.get("tags") or []],
                    locations=[p.strip() for p in location.split(",") if p.strip()],
                    # Вилка приходит строкой («$18 - $22/hr») — разберёт экстрактор.
                    salary_currency="",
                    employment_hint=str(item.get("job_type") or ""),
                    is_remote=True,
                    extra={"salary_text": str(item.get("salary") or "")},
                )


@register("arbeitnow")
class ArbeitnowSource(HttpJobBoard):
    """arbeitnow.com — вакансии по Европе, включая Польшу. Ценен для Познани."""

    display_name = "Arbeitnow (Европа)"
    api_url = "https://www.arbeitnow.com/api/job-board-api"

    def _items(self, data: object) -> list[dict]:
        return data.get("data", []) if isinstance(data, dict) else []

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        pages = int(self.options.get("pages", 3))
        for page in range(1, pages + 1):
            data = await self._get_json(self.api_url, {"page": page})
            items = self._items(data)
            if not items:
                break

            for item in items:
                posted = _ts(item.get("created_at"))
                if since and posted < since:
                    continue

                location = str(item.get("location") or "")
                yield RawPost(
                    external_id=str(item.get("slug")),
                    title=str(item.get("title") or ""),
                    company=str(item.get("company_name") or ""),
                    text=strip_html(str(item.get("description") or "")),
                    posted_at=posted,
                    url=str(item.get("url") or ""),
                    tags=[str(t) for t in item.get("tags") or []],
                    locations=[location] if location else [],
                    is_remote=bool(item.get("remote")),
                    employment_hint=", ".join(str(t) for t in item.get("job_types") or []),
                )


@register("jobicy")
class JobicySource(HttpJobBoard):
    """jobicy.com — remote-вакансии с готовыми грейдами и регионами."""

    display_name = "Jobicy"
    api_url = "https://jobicy.com/api/v2/remote-jobs"

    def _verify_params(self) -> dict | None:
        return {"count": 1}

    def _items(self, data: object) -> list[dict]:
        return data.get("jobs", []) if isinstance(data, dict) else []

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        tags = self.options.get("tags") or ["python", "backend", "javascript", "full-stack"]
        count = int(self.options.get("count", 50))
        seen: set[str] = set()

        for tag in tags:
            data = await self._get_json(self.api_url, {"count": count, "tag": tag})
            for item in self._items(data):
                job_id = str(item.get("id"))
                if job_id in seen:
                    continue
                seen.add(job_id)

                posted = _ts(item.get("pubDate"))
                if since and posted < since:
                    continue

                salary_min = item.get("annualSalaryMin")
                salary_max = item.get("annualSalaryMax")
                geo = str(item.get("jobGeo") or "")

                yield RawPost(
                    external_id=job_id,
                    title=str(item.get("jobTitle") or ""),
                    company=str(item.get("companyName") or ""),
                    text=strip_html(str(item.get("jobDescription") or item.get("jobExcerpt") or "")),
                    posted_at=posted,
                    url=str(item.get("url") or ""),
                    tags=[str(t) for t in item.get("jobIndustry") or []],
                    locations=[geo] if geo else [],
                    salary_min=float(salary_min) if salary_min else None,
                    salary_max=float(salary_max) if salary_max else None,
                    salary_currency=str(item.get("salaryCurrency") or ""),
                    salary_period="year" if salary_min or salary_max else "",
                    seniority_hint=str(item.get("jobLevel") or ""),
                    employment_hint=", ".join(str(t) for t in item.get("jobType") or []),
                    is_remote=True,
                )


@register("himalayas")
class HimalayasSource(HttpJobBoard):
    """himalayas.app — большая база remote-вакансий с грейдами и вилками."""

    display_name = "Himalayas"
    api_url = "https://himalayas.app/jobs/api"

    def _verify_params(self) -> dict | None:
        return {"limit": 1}

    def _items(self, data: object) -> list[dict]:
        return data.get("jobs", []) if isinstance(data, dict) else []

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        limit = int(self.options.get("limit", 100))
        pages = int(self.options.get("pages", 2))

        for page in range(pages):
            data = await self._get_json(self.api_url, {"limit": limit, "offset": page * limit})
            items = self._items(data)
            if not items:
                break

            for item in items:
                posted = _ts(item.get("pubDate"))
                if since and posted < since:
                    continue

                seniority = item.get("seniority") or []
                locations = [str(x) for x in item.get("locationRestrictions") or []]

                yield RawPost(
                    external_id=str(item.get("guid") or item.get("applicationLink")),
                    title=str(item.get("title") or ""),
                    company=str(item.get("companyName") or ""),
                    text=strip_html(str(item.get("description") or item.get("excerpt") or "")),
                    posted_at=posted,
                    url=str(item.get("applicationLink") or item.get("guid") or ""),
                    tags=[str(c) for c in item.get("categories") or []],
                    locations=locations,
                    salary_min=float(item["minSalary"]) if item.get("minSalary") else None,
                    salary_max=float(item["maxSalary"]) if item.get("maxSalary") else None,
                    salary_currency=str(item.get("currency") or ""),
                    salary_period=str(item.get("salaryPeriod") or ""),
                    seniority_hint=", ".join(str(s) for s in seniority),
                    employment_hint=str(item.get("employmentType") or ""),
                    # Пустой список ограничений = работать можно откуда угодно.
                    is_remote=True,
                )
