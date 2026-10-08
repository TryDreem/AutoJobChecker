"""Scrapy-backed website sources.

Scrapy runs in a short-lived subprocess. That keeps Twisted's reactor isolated:
the bot can scan these sources many times without hitting ReactorNotRestartable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.config import BASE_DIR
from app.core.enums import SourceKind
from app.core.schemas import RawPost
from app.sources.base import BaseSource, SourceError
from app.sources.registry import register

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120


def _posted_at(value: object) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class ScrapyWebsiteSource(BaseSource):
    """Base class for websites crawled through app.tools.scrapy_fetch."""

    kind = SourceKind.JOBBOARD
    provider_key = ""

    async def _run_spider(self, *, verify: bool = False) -> list[dict]:
        options = dict(self.options)
        timeout = int(options.pop("timeout", DEFAULT_TIMEOUT_SECONDS))
        if verify:
            options["limit"] = 1
            options["pages"] = 1

        command = [
            sys.executable,
            "-m",
            "app.tools.scrapy_fetch",
            self.provider_key,
            json.dumps(options, ensure_ascii=False),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(BASE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SourceError(f"{self.display_name}: cannot start Scrapy ({exc})") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise SourceError(f"{self.display_name}: Scrapy timeout after {timeout}s") from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            detail = detail.splitlines()[-1] if detail else "unknown Scrapy error"
            raise SourceError(f"{self.display_name}: {detail[:400]}")

        items: list[dict] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s: skipped non-JSON Scrapy output: %r", self.display_name, line[:120])
                continue
            if isinstance(item, dict):
                items.append(item)
        return items

    async def verify(self) -> tuple[bool, str]:
        try:
            items = await self._run_spider(verify=True)
        except SourceError as exc:
            return False, str(exc)
        if not items:
            return False, "Scrapy finished, but no offers were found"
        return True, f"OK - Scrapy fetched {len(items)} offer(s)"

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        items = await self._run_spider()
        for item in items:
            external_id = str(item.get("external_id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not external_id or not text:
                continue

            posted_at = _posted_at(item.get("posted_at"))
            if since and posted_at < since:
                continue

            yield RawPost(
                external_id=external_id,
                title=str(item.get("title") or "").strip(),
                company=str(item.get("company") or "").strip(),
                text=text,
                posted_at=posted_at,
                url=str(item.get("url") or "").strip(),
                tags=[str(tag) for tag in item.get("tags") or [] if str(tag).strip()],
                locations=[
                    str(location)
                    for location in item.get("locations") or []
                    if str(location).strip()
                ],
                is_remote=item.get("is_remote") if isinstance(item.get("is_remote"), bool) else None,
                employment_hint=str(item.get("employment_hint") or ""),
                seniority_hint=str(item.get("seniority_hint") or ""),
                extra={"provider": self.provider_key, **dict(item.get("extra") or {})},
            )

    def merge_cursor(self, previous: str | None, seen: list[str]) -> str | None:
        # Website search pages are not cursor-based. Duplicate external_id values
        # and the normal since window are enough for incremental scans.
        return previous


@register("theprotocol")
class TheProtocolSource(ScrapyWebsiteSource):
    display_name = "the:protocol"
    provider_key = "theprotocol"


@register("pracuj")
class PracujSource(ScrapyWebsiteSource):
    display_name = "Pracuj.pl"
    provider_key = "pracuj"
