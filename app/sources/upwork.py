"""Upwork — заказы через официальный GraphQL API.

Ключ выдаётся бесплатно любому фрилансеру для личного использования: заявка
подаётся в API Center на upwork.com, рассмотрение около недели, лимит
40 000 запросов в сутки. Публичные RSS-ленты Upwork отключил в августе 2024,
поэтому официальный API — единственный законный путь; скрейпинг нарушает
условия использования и грозит блокировкой аккаунта.

Ценность этого источника не в самих вакансиях, а в метриках, которых нет
больше нигде: сколько уже подано откликов, подтверждена ли у клиента оплата,
какой у него рейтинг и сколько он потратил. По ним и отбирается «то самое»
предложение, пока его не завалили сотней заявок.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.core.enums import SourceKind
from app.core.schemas import RawPost
from app.sources.base import BaseSource, SourceError
from app.sources.jobboards import TIMEOUT, USER_AGENT
from app.sources.registry import register

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"

# Поисковый запрос к marketplace. Помимо описания вакансии тянем всё, что
# характеризует клиента и конкуренцию, — ради этого источник и нужен.
SEARCH_QUERY = """
query SearchJobs($query: String!, $limit: Int!) {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: { searchExpression_eq: $query }
    searchType: USER_JOBS_SEARCH
    sortAttributes: [{ field: RECENCY }]
    pagination: { first: $limit }
  ) {
    edges {
      node {
        id
        title
        description
        ciphertext
        createdDateTime
        experienceLevel
        totalApplicants
        amount { rawValue currency }
        hourlyBudgetMin { rawValue }
        hourlyBudgetMax { rawValue }
        skills { name }
        job { contractTerms { contractType } }
        client {
          totalSpent { rawValue }
          totalHires
          totalReviews
          totalFeedback
          verificationStatus
          location { country }
        }
      }
    }
  }
}
"""

# Upwork отдаёт уровень как ENTRY_LEVEL / INTERMEDIATE / EXPERT.
_LEVEL_MAP = {
    "ENTRY_LEVEL": "entry",
    "INTERMEDIATE": "intermediate",
    "EXPERT": "expert",
}

# Ориентировочный опыт, который стоит за уровнем Upwork. Нужен, чтобы фильтр
# «не больше N лет опыта» работал и там, где в тексте лет не указано.
_LEVEL_YEARS = {"entry": 1, "intermediate": 3, "expert": 5}


def _num(value) -> float | None:
    """rawValue приходит строкой или числом, а иногда отсутствует вовсе."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@register("upwork")
class UpworkSource(BaseSource):
    """Заказы с Upwork. Требует OAuth2-ключей в .env."""

    kind = SourceKind.UPWORK
    display_name = "Upwork"

    def _credentials(self) -> tuple[str, str, str]:
        # Через get_settings(), а не os.getenv(): pydantic-settings читает .env
        # в свой объект напрямую и не пробрасывает значения в os.environ,
        # так что os.getenv() здесь всегда возвращал бы пустую строку, даже
        # если ключи честно вписаны в файл.
        settings = get_settings()
        return (
            settings.upwork_client_id,
            settings.upwork_client_secret,
            settings.upwork_refresh_token,
        )

    async def _access_token(self) -> str:
        client_id, client_secret, refresh_token = self._credentials()
        if not all((client_id, client_secret, refresh_token)):
            raise SourceError(
                "Upwork не настроен: нужны UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET "
                "и UPWORK_REFRESH_TOKEN в .env. Ключ запрашивается бесплатно "
                "в API Center на upwork.com."
            )
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(TOKEN_URL, data=payload)
            if response.status_code != 200:
                raise SourceError(
                    f"Upwork OAuth: HTTP {response.status_code}. "
                    "Скорее всего истёк refresh token — получи новый в API Center."
                )
            token = str(response.json().get("access_token", ""))
            if not token:
                raise SourceError("Upwork OAuth: ответ без access_token")
            return token

    async def verify(self) -> tuple[bool, str]:
        try:
            await self._access_token()
        except SourceError as exc:
            return False, str(exc)
        return True, "OK — ключи Upwork приняты"

    @staticmethod
    def _parse_applicants(node: dict) -> tuple[int | None, int | None]:
        """Число откликов. Upwork отдаёт то точное значение, то диапазон строкой."""
        raw = node.get("totalApplicants")
        if isinstance(raw, (int, float)):
            value = int(raw)
            return value, value
        if isinstance(raw, str):
            numbers = [int(x) for x in re.findall(r"\d+", raw)]
            if len(numbers) >= 2:
                return numbers[0], numbers[1]
            if numbers:
                return numbers[0], numbers[0]
        return None, None

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        token = await self._access_token()
        queries = self.options.get("queries") or [
            "python", "django", "fastapi", "python automation", "telegram bot",
            "web scraping python", "backend api", "javascript nodejs",
        ]
        limit = int(self.options.get("limit", 40))

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for query in queries:
                response = await client.post(
                    GRAPHQL_URL,
                    headers=headers,
                    json={"query": SEARCH_QUERY, "variables": {"query": query, "limit": limit}},
                )
                if response.status_code == 429:
                    raise SourceError("Upwork: превышен лимит запросов, попробую позже")
                if response.status_code != 200:
                    raise SourceError(f"Upwork GraphQL: HTTP {response.status_code}")

                payload = response.json()
                if payload.get("errors"):
                    raise SourceError(f"Upwork GraphQL: {str(payload['errors'])[:200]}")

                search = (payload.get("data") or {}).get("marketplaceJobPostingsSearch") or {}
                for edge in search.get("edges") or []:
                    node = edge.get("node") or {}
                    job_id = str(node.get("id") or "")
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    posted = node.get("createdDateTime") or ""
                    try:
                        posted_at = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
                    except ValueError:
                        posted_at = datetime.now(timezone.utc)
                    if posted_at.tzinfo is None:
                        posted_at = posted_at.replace(tzinfo=timezone.utc)
                    if since and posted_at < since:
                        continue

                    amount = node.get("amount") or {}
                    hourly_min = _num((node.get("hourlyBudgetMin") or {}).get("rawValue"))
                    hourly_max = _num((node.get("hourlyBudgetMax") or {}).get("rawValue"))
                    fixed = _num(amount.get("rawValue"))

                    client_info = node.get("client") or {}
                    location = client_info.get("location") or {}
                    reviews = client_info.get("totalReviews")
                    # Upwork называет статус по-разному в разных версиях схемы;
                    # проверяем по подстроке, чтобы не ломаться на переименовании.
                    verification = str(client_info.get("verificationStatus") or "").upper()
                    payment_verified = "VERIFIED" in verification if verification else None

                    level = _LEVEL_MAP.get(str(node.get("experienceLevel") or "").upper(), "")
                    proposals_min, proposals_max = self._parse_applicants(node)

                    # Ссылку строим через ciphertext — обычный id в веб-адресе
                    # Upwork не открывается.
                    cipher = str(node.get("ciphertext") or "").lstrip("~")
                    url = f"https://www.upwork.com/jobs/~{cipher}" if cipher else "https://www.upwork.com/nx/search/jobs/"

                    yield RawPost(
                        external_id=job_id,
                        title=str(node.get("title") or ""),
                        text=str(node.get("description") or ""),
                        posted_at=posted_at,
                        url=url,
                        tags=[s.get("name", "") for s in node.get("skills") or [] if s.get("name")],
                        salary_min=hourly_min or fixed,
                        salary_max=hourly_max or fixed,
                        salary_currency=str(amount.get("currency") or "USD"),
                        salary_period="hour" if hourly_min or hourly_max else "project",
                        employment_hint="freelance",
                        seniority_hint=level,
                        is_remote=True,
                        proposals_min=proposals_min,
                        proposals_max=proposals_max,
                        client_payment_verified=payment_verified,
                        client_rating=_num(client_info.get("totalFeedback")),
                        client_reviews=int(reviews) if isinstance(reviews, (int, float)) else None,
                        client_spent=_num((client_info.get("totalSpent") or {}).get("rawValue")),
                        client_hires=client_info.get("totalHires"),
                        client_country=str(location.get("country") or ""),
                        experience_level=level,
                        extra={"level_years": _LEVEL_YEARS.get(level)},
                    )
