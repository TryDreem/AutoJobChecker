"""Run Scrapy website spiders and print JSON Lines items to stdout."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urljoin

try:
    import scrapy
    from scrapy import signals
    from scrapy.crawler import CrawlerProcess
except Exception as exc:  # pragma: no cover - exercised only without dependency
    print(f"Scrapy is not installed: {exc}", file=sys.stderr)
    raise SystemExit(3) from exc


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

DEFAULT_QUERIES = {
    "theprotocol": ["python", "django", "fastapi", "backend", "javascript", "fullstack"],
    "pracuj": ["python developer", "django", "fastapi", "backend python", "javascript developer"],
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "wrzesnia": 9,
    "września": 9,
    "pazdziernika": 10,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}

CITY_NAMES = [
    "Warszawa",
    "Krakow",
    "Kraków",
    "Wroclaw",
    "Wrocław",
    "Poznan",
    "Poznań",
    "Gdansk",
    "Gdańsk",
    "Gdynia",
    "Sopot",
    "Katowice",
    "Lodz",
    "Łódź",
    "Lublin",
    "Bialystok",
    "Białystok",
    "Szczecin",
    "Bydgoszcz",
    "Rzeszow",
    "Rzeszów",
    "Remote",
    "Zdalna",
    "Cala Polska",
    "Cała Polska",
]

TECH_HINTS = [
    "Python",
    "Django",
    "FastAPI",
    "Flask",
    "JavaScript",
    "TypeScript",
    "React",
    "React.js",
    "Node.js",
    "SQL",
    "PostgreSQL",
    "Docker",
    "Kubernetes",
    "AWS",
    "Azure",
    "GCP",
    "Git",
    "CI/CD",
    "Pandas",
    "PyTorch",
    "TensorFlow",
]

BLOCKED_MARKERS = (
    "cf-chl",
    "Just a moment...",
    "Enable JavaScript and cookies to continue",
)


def _clean(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9.+#]+", "-", value)
    return value.strip("-")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = _clean(raw)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _extract_id(provider: str, url: str) -> str:
    decoded = unquote(url)
    patterns = {
        "theprotocol": [r",oferta,([0-9a-fA-F-]{24,})"],
        "pracuj": [r",oferta,(\d+)"],
    }
    for pattern in patterns.get(provider, []):
        match = re.search(pattern, decoded)
        if match:
            return match.group(1)
    return re.sub(r"\W+", "-", decoded).strip("-")[-120:]


def _build_urls(provider: str, options: dict[str, Any]) -> list[str]:
    urls = [str(url) for url in options.get("urls") or [] if str(url).startswith("http")]
    if urls:
        return urls

    queries = [str(query) for query in options.get("queries") or DEFAULT_QUERIES[provider]]
    pages = max(1, int(options.get("pages") or 1))
    out: list[str] = []

    for query in queries:
        if provider == "theprotocol":
            # the:protocol uses filter slugs for technologies, e.g. /filtry/python;t.
            out.append(f"https://theprotocol.it/filtry/{quote(_slug(query))};t")
        elif provider == "pracuj":
            encoded = quote(query)
            for page in range(1, pages + 1):
                suffix = f"?pn={page}" if page > 1 else ""
                out.append(f"https://www.pracuj.pl/praca/{encoded};kw{suffix}")
    return out


def _parse_date_from_text(text: str) -> datetime:
    now = datetime.now(timezone.utc)

    iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)

    month_names = "|".join(re.escape(month) for month in MONTHS)
    match = re.search(
        rf"\b(\d{{1,2}})\s+({month_names})\s+(20\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        day = int(match.group(1))
        month = MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        return datetime(year, month, day, tzinfo=timezone.utc)

    return now


def _json_ld_values(response: scrapy.http.Response, key: str) -> list[str]:
    values: list[str] = []
    for raw in response.css('script[type="application/ld+json"]::text').getall():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                value = item.get(key)
                if isinstance(value, str):
                    values.append(value)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return values


def _first_text(response: scrapy.http.Response, selectors: list[str]) -> str:
    for selector in selectors:
        value = response.css(selector).get()
        if value:
            return _clean(value)
    return ""


def _visible_lines(response: scrapy.http.Response) -> list[str]:
    skip = {
        "cenimy twoja prywatnosc",
        "cenimy twoją prywatność",
        "akceptuje wszystkie",
        "akceptuję wszystkie",
        "dostosuje chce dostosowac",
        "dostosuję chcę dostosować",
        "zapisz",
        "save",
        "share",
        "copy",
        "kopiuj",
    }
    lines: list[str] = []
    for raw in response.css("body ::text").getall():
        line = _clean(raw)
        if not line:
            continue
        if line.casefold() in skip:
            continue
        lines.append(line)
    return _unique(lines)


def _extract_company(provider: str, response: scrapy.http.Response, lines: list[str]) -> str:
    selectors = [
        '[data-test*="company"]::text',
        '[data-test*="employer"]::text',
        'a[href*="/firma/"]::text',
        'a[href*="/pracodawcy/"]::text',
        'a[href*="/pracodawca/"]::text',
    ]
    company = _first_text(response, selectors)
    if company:
        return re.sub(r"^(?:Company|Firma):\s*", "", company, flags=re.IGNORECASE).strip()

    joined = "\n".join(lines[:80])
    pattern = r"(?:Company|Firma):\s*(.+)"
    match = re.search(pattern, joined, flags=re.IGNORECASE)
    if match:
        return _clean(match.group(1))[:180]

    if provider == "pracuj":
        for index, line in enumerate(lines[:40]):
            if line.startswith("## ") and index + 1 < len(lines):
                return lines[index + 1].replace("O firmie", "").strip()[:180]
    return ""


def _extract_title(response: scrapy.http.Response, lines: list[str]) -> str:
    title = _first_text(
        response,
        [
            "h1::text",
            '[data-test*="offer-title"]::text',
            'meta[property="og:title"]::attr(content)',
            "title::text",
        ],
    )
    if title:
        return re.sub(r"\s+-\s+.*$", "", title).strip()[:220]
    for line in lines:
        if 8 <= len(line) <= 220 and not line.lower().startswith(("praca ", "oferty ")):
            return line
    return ""


def _extract_locations(text: str) -> list[str]:
    found = [city for city in CITY_NAMES if re.search(rf"\b{re.escape(city)}\b", text)]
    return _unique(found)[:8]


def _extract_tags(text: str) -> list[str]:
    tags = [tag for tag in TECH_HINTS if re.search(rf"\b{re.escape(tag)}\b", text, re.IGNORECASE)]
    return _unique(tags)[:20]


def _extract_employment(text: str) -> str:
    hints: list[str] = []
    lowered = text.casefold()
    for label in ("b2b", "contract", "kontrakt", "umowa o prace", "umowa o pracę", "full-time", "part-time", "pelny etat", "pełny etat"):
        if label in lowered:
            hints.append(label)
    return ", ".join(_unique(hints))


def _extract_seniority(text: str) -> str:
    lowered = text.casefold()
    for label in ("trainee", "intern", "junior", "mid", "regular", "senior", "lead", "expert"):
        if re.search(rf"\b{re.escape(label)}\b", lowered):
            return label
    return ""


class JobWebsiteSpider(scrapy.Spider):
    name = "job_websites"
    handle_httpstatus_list = [401, 403, 429]

    custom_settings = {
        "LOG_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "USER_AGENT": USER_AGENT,
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_TIMES": 1,
        "COOKIES_ENABLED": True,
        "AUTOTHROTTLE_ENABLED": True,
        "DOWNLOAD_DELAY": 1.0,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.9,en;q=0.8",
        },
    }

    def __init__(self, provider: str, options_json: str = "{}", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if provider not in DEFAULT_QUERIES:
            raise ValueError(f"unknown provider: {provider}")
        self.provider = provider
        self.options = json.loads(options_json or "{}")
        self.limit = max(1, int(self.options.get("limit") or 40))
        self.seen: set[str] = set()
        self.blocked = False
        self.start_urls = _build_urls(provider, self.options)

    def parse(self, response: scrapy.http.Response):
        body_head = response.text[:8000]
        if response.status in (401, 403, 429) or any(marker in body_head for marker in BLOCKED_MARKERS):
            self.blocked = True
            return

        links = []
        for href in response.css("a::attr(href)").getall():
            absolute = urljoin(response.url, href)
            decoded = unquote(absolute)
            if self.provider == "theprotocol" and "/szczegoly/praca/" not in decoded:
                continue
            if self.provider == "pracuj" and not re.search(r"/praca/.+?,oferta,\d+", decoded):
                continue
            external_id = _extract_id(self.provider, absolute)
            if not external_id or external_id in self.seen:
                continue
            self.seen.add(external_id)
            links.append((absolute, external_id))
            if len(self.seen) >= self.limit:
                break

        if not links and self._looks_like_detail(response.url):
            yield self._build_item(response, _extract_id(self.provider, response.url))
            return

        for url, external_id in links:
            yield response.follow(url, callback=self.parse_offer, cb_kwargs={"external_id": external_id})

    def parse_offer(self, response: scrapy.http.Response, external_id: str):
        body_head = response.text[:8000]
        if response.status in (401, 403, 429) or any(marker in body_head for marker in BLOCKED_MARKERS):
            self.blocked = True
            return
        yield self._build_item(response, external_id)

    def _looks_like_detail(self, url: str) -> bool:
        decoded = unquote(url)
        if self.provider == "theprotocol":
            return "/szczegoly/praca/" in decoded
        return bool(re.search(r"/praca/.+?,oferta,\d+", decoded))

    def _build_item(self, response: scrapy.http.Response, external_id: str) -> dict[str, Any]:
        lines = _visible_lines(response)
        text = _clean("\n".join(lines))[:20000]
        title = _extract_title(response, lines)
        company = _extract_company(self.provider, response, lines)

        date_values = (
            _json_ld_values(response, "datePosted")
            + response.css('meta[itemprop="datePosted"]::attr(content)').getall()
            + response.css('time::attr(datetime)').getall()
        )
        posted_at = None
        for value in date_values:
            try:
                posted_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if posted_at.tzinfo is None:
                    posted_at = posted_at.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if posted_at is None:
            posted_at = _parse_date_from_text(text)

        canonical = response.css('link[rel="canonical"]::attr(href)').get()
        url = urljoin(response.url, canonical) if canonical else response.url
        lowered = text.casefold()

        return {
            "external_id": external_id,
            "title": title,
            "company": company,
            "text": text,
            "posted_at": posted_at.isoformat(),
            "url": url,
            "locations": _extract_locations(text),
            "tags": _extract_tags(text),
            "is_remote": True if any(value in lowered for value in ("remote", "zdalna", "praca zdalna")) else None,
            "employment_hint": _extract_employment(text),
            "seniority_hint": _extract_seniority(text),
            "extra": {"source_url": response.url},
        }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m app.tools.scrapy_fetch PROVIDER [OPTIONS_JSON]", file=sys.stderr)
        return 2

    provider = sys.argv[1]
    options_json = sys.argv[2] if len(sys.argv) > 2 else "{}"
    items: list[dict[str, Any]] = []
    state: dict[str, Any] = {"blocked": False}

    process = CrawlerProcess(settings={"LOG_ENABLED": False})

    def on_item_scraped(item: dict[str, Any], response, spider):
        items.append(dict(item))

    def on_spider_closed(spider, reason):
        state["blocked"] = bool(getattr(spider, "blocked", False))

    crawler = process.create_crawler(JobWebsiteSpider)
    crawler.signals.connect(on_item_scraped, signal=signals.item_scraped)
    crawler.signals.connect(on_spider_closed, signal=signals.spider_closed)
    process.crawl(crawler, provider=provider, options_json=options_json)
    process.start()

    if not items and state.get("blocked"):
        print("site returned an anti-bot challenge or HTTP 403/429", file=sys.stderr)
        return 4

    for item in items:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
