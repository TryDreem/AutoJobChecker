"""Универсальный RSS/Atom-источник.

Нужен, чтобы подключать площадки без своего API одной строчкой в sources.yaml:
у большинства бирж и job-бордов лента есть. Разбор синхронный (feedparser),
поэтому выполняется в отдельном потоке, чтобы не блокировать цикл событий.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

from app.core.enums import SourceKind
from app.core.schemas import RawPost
from app.sources.base import BaseSource, SourceError
from app.sources.jobboards import TIMEOUT, USER_AGENT
from app.sources.registry import register
from app.pipeline.text import strip_html

log = logging.getLogger(__name__)


@register(str(SourceKind.RSS))
class RssSource(BaseSource):
    """Любая RSS/Atom-лента. identifier источника — это URL ленты."""

    kind = SourceKind.RSS
    display_name = "RSS-лента"

    async def _download(self) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, */*"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                response = await client.get(self.identifier, headers=headers)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise SourceError(f"RSS {self.identifier}: {exc}") from exc

    async def _parse(self):
        payload = await self._download()
        # feedparser блокирующий — уводим в поток.
        return await asyncio.to_thread(feedparser.parse, payload)

    async def verify(self) -> tuple[bool, str]:
        try:
            feed = await self._parse()
        except SourceError as exc:
            return False, str(exc)
        entries = feed.get("entries") or []
        if not entries:
            return False, "Лента пустая или не разобралась"
        title = feed.feed.get("title", "") if feed.get("feed") else ""
        if title:
            self.record.title = str(title)[:256]
        return True, f"OK — {title or self.identifier}, записей: {len(entries)}"

    @staticmethod
    def _entry_time(entry) -> datetime:
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            value = entry.get(key)
            if value:
                return datetime.fromtimestamp(mktime(value), tz=timezone.utc)
        return datetime.now(timezone.utc)

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        feed = await self._parse()

        for entry in feed.get("entries") or []:
            posted = self._entry_time(entry)
            if since and posted < since:
                continue

            body = ""
            if entry.get("content"):
                body = entry["content"][0].get("value", "")
            body = body or entry.get("summary", "") or entry.get("description", "")

            yield RawPost(
                external_id=str(entry.get("id") or entry.get("link") or entry.get("title")),
                title=str(entry.get("title") or ""),
                text=strip_html(str(body)),
                posted_at=posted,
                url=str(entry.get("link") or ""),
                author=str(entry.get("author") or ""),
                tags=[t.get("term", "") for t in entry.get("tags") or [] if t.get("term")],
            )
