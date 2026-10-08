# AutoJobChecker

**AutoJobChecker is a personal job-hunting radar that monitors Telegram channels
and job websites, removes noise, ranks opportunities, and sends the best matches
straight to your Telegram bot.**

Instead of checking dozens of channels, boards, feeds, and freelance platforms by
hand, AutoJobChecker turns them into one clean, searchable stream. It extracts
salary, tech stack, seniority, work format, location, contacts, source links, and
relevance signals, then shows only the opportunities worth your attention.

## Why It Stands Out

- **Telegram plus websites in one place.** Track Telegram job channels, public
  job-board APIs, RSS feeds, and optional API-backed platforms such as Upwork.
- **Signal over keyword spam.** The pipeline understands roles, seniority, tech
  stack, work format, geography, salary, contacts, and post type.
- **Noise is filtered before it reaches you.** Courses, resumes, crypto spam,
  irrelevant roles, weak posts, and duplicates are filtered out early.
- **Every match is explainable.** Each card has a score and a breakdown of why
  the opportunity was accepted or downgraded.
- **Built for real job search workflows.** Save favorites, hide bad matches,
  search the full database, review daily summaries, and track responses.
- **Works without paid services.** LLM classification via Groq is optional. The
  rule-based engine works on its own.
- **Easy to extend.** Add another website, RSS feed, Telegram channel, or custom
  source without rewriting the pipeline.

## What It Monitors

AutoJobChecker is not limited to Telegram.

| Source type | Examples | Notes |
|---|---|---|
| Telegram channels | public job and freelance channels | Uses Telethon with your account session |
| Job websites with APIs | RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas | Works without scraping keys |
| Scrapy website parsers | the:protocol, Pracuj.pl | Parses SSR job pages when available |
| RSS/Atom feeds | We Work Remotely and any compatible feed | Add URLs in `config/sources.yaml` |
| Freelance platforms | Upwork | Optional, uses official API credentials |
| Custom websites | your own parser/source class | Implement `BaseSource` and yield `RawPost` |

## Core Features

- Aggregates jobs, gigs, internships, and freelance tasks from many sources.
- Includes Scrapy-based parsers for Polish job websites such as the:protocol
  and Pracuj.pl.
- Extracts salary ranges and normalizes monthly compensation when possible.
- Detects Python, backend, frontend, full-stack, DevOps, AI, automation, and
  other stack signals.
- Scores posts from 0 to 100 using profile fit, source quality, freshness,
  salary, work format, seniority, keywords, and penalties.
- Deduplicates exact reposts and near-duplicates across channels and websites.
- Uses an optional LLM only for borderline cases, keeping obvious decisions fast
  and cheap.
- Sends Telegram cards with original links, full text, favorites, hide actions,
  and response tracking.
- Provides source management, health checks, search, stats, filters, and daily
  summaries directly in the bot UI.

## Ideal For

- developers monitoring multiple Telegram channels and job websites;
- junior and middle engineers who need to react fast to relevant openings;
- freelancers looking for fresh paid tasks before they disappear in the stream;
- anyone building a private, self-hosted job radar with transparent filters and
  local data ownership.

## Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill BOT_TOKEN, ADMIN_IDS, and optional source credentials.

python -m app.tools.login      # one-time setup for Telegram channel parsing
python -m app.tools.selfcheck  # validates configuration
python main.py
```

Without `TG_API_ID` and `TG_API_HASH`, Telegram channel parsing is disabled, but
website/API/RSS sources still work. Without `GROQ_API_KEY`, the bot runs on the
local rule-based classifier.

## Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `BOT_TOKEN` | Telegram bot token from `@BotFather` | yes |
| `ADMIN_IDS` | Telegram user ids allowed to use the bot | yes |
| `TG_API_ID`, `TG_API_HASH` | Telegram API credentials from `my.telegram.org` | for Telegram channels |
| `TG_PHONE` | phone number for the first Telethon login | for file-based sessions |
| `TG_SESSION_STRING` | Telethon StringSession | alternative to file-based sessions |
| `GROQ_API_KEY` | optional LLM classifier for borderline posts | no |
| `UPWORK_CLIENT_ID`, `UPWORK_CLIENT_SECRET`, `UPWORK_REFRESH_TOKEN` | Upwork API access | for Upwork |

## How It Works

1. **Collect.** The scheduler polls Telegram channels, job-board APIs, RSS
   feeds, and optional platform APIs.
2. **Parse.** The extraction layer pulls out salary, stack, role, seniority,
   work format, geography, contacts, and source metadata.
3. **Score.** A transparent rule-based scoring engine ranks each post from 0 to
   100 and records the reasons.
4. **Deduplicate.** Exact hashes catch copy-paste reposts, while simhash catches
   near-duplicates with small edits.
5. **Classify edge cases.** Optional Groq-powered LLM checks only borderline
   posts, so the system stays fast and cost-aware.
6. **Notify.** High-quality matches are sent as Telegram cards with useful
   actions and full context.

## Telegram Bot UI

| Section | What it does |
|---|---|
| Feed | fresh relevant jobs and gigs |
| Favorites | saved opportunities |
| Search | full database search, including lower-scored posts |
| Filters | stack, seniority, work format, salary, keywords, stop words |
| Sources | enable, disable, add, and check sources |
| Stats | source quality and collection metrics |
| Daily Digest | compact daily summary |

Each card can be opened at the original source, saved, hidden, expanded to full
text, or marked as responded.

## Architecture

```text
main.py                     bot and scheduler entry point
config/sources.yaml         starter source catalog
app/
|-- config.py               .env-based settings
|-- scheduler.py            background collection and maintenance jobs
|-- core/                   database models, schemas, shared entities
|-- sources/                Telegram, job websites, RSS, Upwork, registry
|-- pipeline/               extraction, scoring, LLM, deduplication
|-- bot/                    aiogram 3 handlers, keyboards, cards
`-- tools/                  login, selfcheck, reclassify, upwork_login
```

## Adding A Website Or Source

If the website exposes RSS or Atom, add the feed URL to `config/sources.yaml`.
For a custom website, implement a source that yields `RawPost` objects:

```python
from app.sources.base import BaseSource, RawPost
from app.sources.registry import register


@register("mysite")
class MySiteSource(BaseSource):
    display_name = "My Site"

    async def fetch(self, since):
        for item in await self._load():
            yield RawPost(
                external_id=item.id,
                text=item.text,
                posted_at=item.posted_at,
            )
```

Once registered and added to `config/sources.yaml`, the new source automatically
uses the same extraction, scoring, deduplication, storage, search, and
notification pipeline.

## Security

The repository is prepared for public use without private data:

- real credentials belong only in `.env`;
- `.env`, `.env.*`, `data/`, databases, logs, `.session`, `.venv`, and IDE files
  are ignored by Git;
- `.env.example` contains empty placeholders and safe defaults;
- Telegram session files and `TG_SESSION_STRING` must be treated as secrets,
  because they can grant access to the parser account.

Before publishing your own fork, verify tracked files:

```bash
git status --ignored --short
git ls-files
```

Tracked files should never include `.env`, `data/`, `.session`, databases, logs,
local virtual environments, or IDE settings.

## Tech Stack

Python 3.11, aiogram 3, Telethon, Scrapy, SQLAlchemy 2, APScheduler, httpx,
feedparser, Pydantic Settings, SQLite, and Groq SDK.

## Status

AutoJobChecker is a practical self-hosted project for building a private job
search command center. The core pipeline is modular, source-driven, and ready
for more job websites, custom parsers, and personalized ranking profiles.
