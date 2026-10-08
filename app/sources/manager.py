"""Управление каталогом источников.

Отвечает за три вещи:
  * первичный залив списка из config/sources.yaml в базу;
  * проверку, что канал жив и читается (мёртвые отключаются сами);
  * поиск новых каналов по ключевым словам через Telegram.

Список в YAML — это стартовая точка, а не догма: любой канал можно добавить
или выключить прямо из бота, и правки переживают перезапуск.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.enums import SourceKind
from app.core.models import Source
from app.sources import registry

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^@?([a-zA-Z][a-zA-Z0-9_]{3,31})$")
TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,31})")


def normalize_channel(value: str) -> str | None:
    """Приводит «@name», «t.me/name», «name» к единому «@name»."""
    value = (value or "").strip()
    if not value:
        return None
    link = TG_LINK_RE.search(value)
    if link:
        return f"@{link.group(1)}"
    match = USERNAME_RE.match(value)
    return f"@{match.group(1)}" if match else None


async def load_catalog(session: AsyncSession) -> tuple[int, int]:
    """Заливает config/sources.yaml в базу. Существующие записи не трогает,
    чтобы не затирать ручные правки. Возвращает (добавлено, всего в файле)."""
    path = get_settings().sources_file
    if not path.exists():
        log.warning("Каталог источников не найден: %s", path)
        return 0, 0

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    entries: list[dict] = []

    for item in data.get("telegram") or []:
        identifier = normalize_channel(item.get("channel", ""))
        if not identifier:
            log.warning("Пропущен некорректный канал: %r", item)
            continue
        entries.append({
            "kind": SourceKind.TELEGRAM,
            "identifier": identifier,
            "title": item.get("title", ""),
            "tags": item.get("tags", []),
            "priority": int(item.get("priority", 5)),
            "enabled": bool(item.get("enabled", True)),
            "options": item.get("options", {}) or {},
        })

    for item in data.get("jobboards") or []:
        key = item.get("provider", "")
        if key not in registry.known_keys():
            log.warning("Неизвестный провайдер биржи: %r", key)
            continue
        entries.append({
            "kind": SourceKind.JOBBOARD,
            "identifier": key,
            "title": item.get("title", key),
            "tags": item.get("tags", []),
            "priority": int(item.get("priority", 5)),
            "enabled": bool(item.get("enabled", True)),
            "options": item.get("options", {}) or {},
        })

    for item in data.get("rss") or []:
        url = item.get("url", "")
        if not url.startswith("http"):
            continue
        entries.append({
            "kind": SourceKind.RSS,
            "identifier": url,
            "title": item.get("title", ""),
            "tags": item.get("tags", []),
            "priority": int(item.get("priority", 5)),
            "enabled": bool(item.get("enabled", True)),
            "options": item.get("options", {}) or {},
        })

    added = 0
    for entry in entries:
        exists = await session.scalar(
            select(Source).where(
                Source.kind == entry["kind"], Source.identifier == entry["identifier"]
            )
        )
        if exists is not None:
            continue
        session.add(Source(**entry))
        added += 1

    if added:
        log.info("Из каталога добавлено источников: %s", added)
    return added, len(entries)


async def add_channel(
    session: AsyncSession, raw: str, *, title: str = "", priority: int = 5
) -> tuple[Source | None, str]:
    """Добавляет Telegram-канал. Возвращает (источник, пояснение для пользователя)."""
    identifier = normalize_channel(raw)
    if identifier is None:
        return None, "Не похоже на канал. Нужен @username или ссылка вида t.me/username."

    exists = await session.scalar(
        select(Source).where(
            Source.kind == SourceKind.TELEGRAM, Source.identifier == identifier
        )
    )
    if exists is not None:
        if not exists.enabled:
            exists.enabled = True
            return exists, f"Канал {identifier} уже был в списке — снова включил."
        return exists, f"Канал {identifier} уже отслеживается."

    source = Source(
        kind=SourceKind.TELEGRAM,
        identifier=identifier,
        title=title,
        priority=priority,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    return source, f"Канал {identifier} добавлен."


@dataclass(slots=True)
class CheckResult:
    source_id: int
    name: str
    ok: bool
    detail: str


async def verify_source(session: AsyncSession, record: Source) -> CheckResult:
    """Проверяет один источник и обновляет его метаданные."""
    parser = registry.build(record)
    if parser is None:
        record.enabled = False
        record.last_error = "нет реализации"
        return CheckResult(record.id, record.display_name, False, "нет реализации")

    try:
        ok, detail = await parser.verify()
    except Exception as exc:  # noqa: BLE001 - в отчёт нужна любая ошибка
        ok, detail = False, f"{type(exc).__name__}: {exc}"

    record.verified = ok
    if ok:
        record.error_count = 0
        record.last_error = None
    else:
        record.error_count += 1
        record.last_error = detail[:500]
        # Три неудачи подряд — почти наверняка канал удалён или закрыт.
        if record.error_count >= 3:
            record.enabled = False

    return CheckResult(record.id, record.display_name, ok, detail)


async def verify_all(session: AsyncSession, *, only_enabled: bool = True) -> list[CheckResult]:
    query = select(Source)
    if only_enabled:
        query = query.where(Source.enabled.is_(True))
    rows = await session.execute(query.order_by(Source.id))

    results: list[CheckResult] = []
    for record in rows.scalars():
        results.append(await verify_source(session, record))
    return results


# --- Автопоиск новых каналов ------------------------------------------------
# Запросы сгруппированы по направлениям: искать всё разом — значит утонуть в
# общих «работа/вакансии» каналах, поэтому в боте можно выбрать конкретную
# группу. Много узких запросов работает лучше нескольких общих: Telegram
# отдаёт на каждый максимум ~20 совпадений и сильно завязан на формулировку —
# «python вакансии» и «python jobs remote» дают почти непересекающиеся выдачи.
DISCOVERY_GROUPS: dict[str, list[str]] = {
    "python": [
        "python вакансии", "python jobs", "python developer", "django вакансии",
        "fastapi vacancy", "python backend вакансии", "питон разработчик вакансии",
        "python remote jobs", "django jobs", "python работа удаленно",
    ],
    "js": [
        "javascript вакансии", "node.js jobs", "js developer remote",
        "fullstack developer jobs", "react developer vacancy", "typescript jobs",
        "frontend вакансии удаленно", "nodejs вакансии", "react вакансии",
    ],
    "it": [
        "backend вакансии", "удаленная работа it", "remote developer jobs",
        "it jobs remote", "hiring developer remote", "vacancy software engineer",
        "junior разработчик", "junior developer jobs", "стажировка it",
        "internship developer", "trainee developer", "it вакансии удаленно",
    ],
    "freelance": [
        "фриланс заказы", "фриланс разработка", "заказы программирование",
        "freelance developer", "freelance python jobs", "заказы веб-разработка",
        "разовые заказы it", "подработка программист", "заказ сайт разработка",
        "upwork заказы", "заказы боты разработка", "фриланс биржа заказы",
        "заказы верстка сайтов", "freelance projects", "заказы автоматизация",
        "kwork заказы", "фриланс it заказы", "заказы разработка приложений",
    ],
    "poland": [
        "it jobs poland", "poland it jobs remote", "praca programista",
        "it relocation poland", "poznan it jobs", "warsaw developer jobs",
        "praca zdalna it", "praca it polska", "работа в польше it",
    ],
}

# Порядок групп в интерфейсе и русские подписи для кнопок.
DISCOVERY_GROUP_LABELS: dict[str, str] = {
    "freelance": "🧩 Фриланс-заказы",
    "python": "🐍 Python",
    "js": "🟨 JS / Fullstack",
    "it": "💼 IT-вакансии",
    "poland": "🇵🇱 Польша",
}

# Плоский список — когда ищем сразу по всем направлениям.
DISCOVERY_QUERIES = [q for group in DISCOVERY_GROUPS.values() for q in group]

# Слова, по которым видно, что канал вообще про работу — первый грубый фильтр
# по названию/юзернейму (дёшево, применяется до сетевых запросов).
RELEVANT_HINTS = re.compile(
    r"ваканс|работ|job|hiring|career|карьер|фриланс|freelance|заказ|стаж|intern"
    r"|resume|hh\b|remote|developer|developers|programmist|программист|it[\s_-]?jobs",
    re.IGNORECASE,
)

# Тот же смысл, но для описания канала (about) — второй, более надёжный шанс
# для каналов, у которых название нейтральное («Мой канал»), а по делу — в описании.
DESCRIPTION_HINTS = re.compile(
    r"ваканс|работ\w*|job|hiring|career|карьер|фриланс|freelance|заказ|стаж|intern"
    r"|remote|developer|programm|recruit|talent|резюме|\bcv\b|подработк",
    re.IGNORECASE,
)

# Явный мусор, который стабильно лезет в выдачу Telegram по этим запросам.
# Проверено на живом поиске: инфобизнес, реферальные бот-схемы, маркетплейсы,
# неайтишная подработка и каналы, целиком завязанные на Россию.
BAD_CHANNEL_HINTS = re.compile(
    r"научу|с нуля|эксперт по заработк|заработок|пассивн\w+ доход|инвестиц"
    r"|наставник|менторств|обучени[ея]|марафон|интенсив|вебинар"
    r"|получить доступ|бот с множеством|реферальн"
    r"|крипт|казино|ставки на спорт|трейдинг|форекс"
    r"|курьер|грузчик|склад|вахта|такси|уборщи|сиделк"
    r"|wildberries|маркетплейс|озон|ozon\b"
    r"|эскорт|18\+|адалт",
    re.IGNORECASE,
)

# Каналы, целиком посвящённые российскому рынку, — работать оттуда всё равно
# нельзя, поэтому в предложениях они не нужны.
RUSSIA_CHANNEL_HINTS = re.compile(
    r"москв|мск\b|санкт-?петербург|спб\b|россии|россия|\bрф\b|краснодар|новосибирск"
    r"|екатеринбург|казан[ьи]|нижн\w+ новгород|челябинск|самар[аеы]|ростов|уф[аеы]"
    r"|красноярск|воронеж|перм[ьи]|волгоград|тюмен|минск|беларус",
    re.IGNORECASE,
)

# Сильные признаки профиля — по ним кандидат поднимается в списке.
PROFILE_HINTS = re.compile(
    r"python|питон|django|fastapi|backend|бэкенд|бекенд|javascript|typescript"
    r"|node|react|fullstack|фулстек|разработ|программир|developer|engineer|it\b",
    re.IGNORECASE,
)

# Каналов меньше этого числа подписчиков либо только что созданы, либо мертвы —
# не показываем, чтобы не засорять список автопоиска. 0 — участники неизвестны
# (сетевой запрос за описанием не удался), таких не отбрасываем.
MIN_PARTICIPANTS = 15


@dataclass(slots=True)
class Candidate:
    username: str
    title: str
    participants: int
    score: int = 0
    about: str = ""


def _score_candidate(title: str, handle: str, about: str, participants: int) -> int:
    """Насколько канал похож на полезный. Отрицательный результат = не показывать.

    Размер канала здесь лишь один из факторов: крупный «канал про заработок»
    должен проигрывать небольшому каналу с заказами на Python.
    """
    haystack = f"{title} {handle} {about}"

    if BAD_CHANNEL_HINTS.search(haystack):
        return -1
    if RUSSIA_CHANNEL_HINTS.search(f"{title} {handle}"):
        return -1

    # Канал вообще про работу? Без этого дальше считать нечего.
    if not (RELEVANT_HINTS.search(f"{title} {handle}") or DESCRIPTION_HINTS.search(about)):
        return -1

    score = 10
    if PROFILE_HINTS.search(f"{title} {handle}"):
        score += 40          # профиль прямо в названии — лучший сигнал
    elif PROFILE_HINTS.search(about):
        score += 20
    if re.search(r"фриланс|freelance|заказ", haystack, re.IGNORECASE):
        score += 15
    if re.search(r"remote|удал[её]н|zdaln", haystack, re.IGNORECASE):
        score += 10
    if re.search(r"poland|polska|польш|poznan|warsaw|praca", haystack, re.IGNORECASE):
        score += 15

    # Размер — вспомогательный признак, с насыщением: разница между 10k и 30k
    # подписчиков куда менее важна, чем между 50 и 5000.
    if participants >= 10_000:
        score += 20
    elif participants >= 2_000:
        score += 15
    elif participants >= 500:
        score += 10
    elif participants >= 100:
        score += 5

    return score


async def _channel_about(client, chat) -> tuple[str, int]:
    """Описание и точное число подписчиков — contacts.search их не отдаёт,
    только полноценный GetFullChannelRequest. Без этого сортировка «крупные
    каналы первыми» не работала: participants_count в результатах поиска
    почти всегда пустой."""
    from telethon.tl.functions.channels import GetFullChannelRequest

    from app.sources.tg_client import safe_call

    try:
        full = await safe_call(
            lambda: client(GetFullChannelRequest(channel=chat)),
            what=f"описание @{getattr(chat, 'username', '?')}",
            max_wait=20,
        )
    except Exception as exc:  # noqa: BLE001 - одна неудача не должна рушить весь поиск
        log.debug("Не удалось получить описание канала: %s", exc)
        return "", getattr(chat, "participants_count", 0) or 0

    about = getattr(full.full_chat, "about", "") or ""
    participants = getattr(full.full_chat, "participants_count", 0) or 0
    return about, participants


async def discover_channels(
    session: AsyncSession, limit: int = 15, group: str | None = None
) -> list[Candidate]:
    """Ищет в Telegram каналы по ключевым словам и отбрасывает уже добавленные.

    group — ключ из DISCOVERY_GROUPS («freelance», «python», …). Без него
    ищем по всем направлениям сразу, но тогда выдача заметно шумнее.

    Двухпроходная схема: сначала широкий сбор по всем запросам (дёшево),
    затем уточнение описания и подписчиков только для реальных кандидатов
    (дороже, поэтому ограничено сверху) — так канал с нейтральным названием,
    но профильным описанием, тоже не теряется.
    """
    from telethon.tl.functions.contacts import SearchRequest

    from app.sources.tg_client import get_client, safe_call

    client = await get_client()

    rows = await session.execute(
        select(Source.identifier).where(Source.kind == SourceKind.TELEGRAM)
    )
    known = {row[0].lower() for row in rows}

    queries = DISCOVERY_GROUPS.get(group, DISCOVERY_QUERIES) if group else DISCOVERY_QUERIES

    # --- Проход 1: собрать сырой пул уникальных публичных каналов -----------
    raw: dict[str, tuple[object, str]] = {}
    for query in queries:
        if len(raw) >= 100:
            break
        try:
            result = await safe_call(
                lambda q=query: client(SearchRequest(q=q, limit=20)),
                what=f"поиск «{query}»",
                max_wait=60,
            )
        except Exception as exc:  # noqa: BLE001 - поиск не должен ронять бота
            log.warning("Поиск каналов «%s» не удался: %s", query, exc)
            continue

        for chat in getattr(result, "chats", []):
            username = getattr(chat, "username", None)
            title = getattr(chat, "title", "") or ""
            if not username or username.lower().endswith("bot"):
                continue
            handle = f"@{username}"
            if handle.lower() in known or handle in raw:
                continue
            raw[handle] = (chat, title)

        # Пауза между запросами поиска — их у нас теперь много подряд.
        await asyncio.sleep(0.4)

    # --- Проход 2: описание + точные подписчики для реальных кандидатов -----
    candidates: list[Candidate] = []
    for handle, (chat, title) in list(raw.items())[:70]:
        about, participants = await _channel_about(client, chat)

        if 0 < participants < MIN_PARTICIPANTS:
            continue

        score = _score_candidate(title, handle, about, participants)
        if score < 0:
            continue

        candidates.append(
            Candidate(
                username=handle,
                title=title,
                participants=participants,
                score=score,
                about=about[:200],
            )
        )
        await asyncio.sleep(0.2)

    # Сначала по осмысленности, и только при равном счёте — по размеру.
    ranked = sorted(candidates, key=lambda c: (c.score, c.participants), reverse=True)
    return ranked[:limit]
