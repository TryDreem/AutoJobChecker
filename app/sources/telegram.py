"""Парсер Telegram-каналов и чатов через MTProto (Telethon).

Читает публичные каналы от имени твоего аккаунта. Подписываться не обязательно:
для публичных каналов история доступна и так.

Защита от лимитов держится на трёх вещах:
  * entity резолвится один раз и кладётся в кэш (главный источник FloodWait);
  * каналы обходятся последовательно, с паузой между ними;
  * при FloodWait длиннее пяти минут канал пропускается до следующего прохода.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    InviteHashExpiredError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import Channel, Chat, User

from app.config import get_settings
from app.core.enums import SourceKind
from app.core.schemas import RawPost
from app.sources.base import BaseSource, SourceError
from app.sources.registry import register
from app.sources.tg_client import get_client, safe_call, throttle

log = logging.getLogger(__name__)

# Резолв @username -> entity стоит дорого и лимитируется жёстче всего,
# поэтому держим результат в памяти процесса.
_entity_cache: dict[str, object] = {}


@register(str(SourceKind.TELEGRAM))
class TelegramSource(BaseSource):
    """Один Telegram-канал или чат."""

    kind = SourceKind.TELEGRAM
    display_name = "Telegram-канал"
    needs_telegram = True

    async def _resolve(self):
        """Достаёт entity канала: из памяти, из сессии Telethon или по сети."""
        key = self.identifier
        if key in _entity_cache:
            return _entity_cache[key]

        client = await get_client()
        # Резолвим строго по @username. Голый числовой id сюда передавать нельзя:
        # Telethon по положительному int считает peer пользователем, а не каналом,
        # и падает с «Could not find the input entity for PeerUser». Кэш
        # ResolveUsername Telethon и так держит в файле сессии, так что
        # дополнительной экономии от собственного peer_id не было.
        try:
            entity = await safe_call(
                lambda: client.get_entity(key), what=f"get_entity({key})"
            )
        except (UsernameNotOccupiedError, UsernameInvalidError) as exc:
            raise SourceError(f"Канал {key} не существует") from exc
        except (ChannelPrivateError, ChatAdminRequiredError, InviteHashExpiredError) as exc:
            raise SourceError(f"Нет доступа к {key} (приватный или забанен)") from exc
        except ValueError as exc:
            raise SourceError(f"Не удалось разобрать {key}: {exc}") from exc

        _entity_cache[key] = entity
        return entity

    async def verify(self) -> tuple[bool, str]:
        """Проверяет, что канал существует и читается. Заодно чинит метаданные."""
        try:
            entity = await self._resolve()
        except SourceError as exc:
            return False, str(exc)
        except FloodWaitError as exc:
            return False, f"FloodWait {exc.seconds} сек — проверь позже"
        except Exception as exc:  # noqa: BLE001 - в UI нужен любой текст ошибки
            return False, f"Ошибка: {exc}"

        title = getattr(entity, "title", None) or getattr(entity, "username", "") or self.identifier
        self.record.title = str(title)[:256]
        # peer_id храним только для справки: резолв идёт по username (см. _resolve).
        self.options["peer_id"] = entity.id
        if getattr(entity, "username", None):
            self.options["username"] = entity.username
        self.record.options = self.options

        subs = getattr(entity, "participants_count", None)
        suffix = f", подписчиков: {subs}" if subs else ""
        return True, f"OK — {title}{suffix}"

    def _post_url(self, entity, message_id: int) -> str:
        username = getattr(entity, "username", None) or self.options.get("username")
        if username:
            return f"https://t.me/{username}/{message_id}"
        # У приватных каналов ссылка строится от id без префикса -100.
        raw_id = str(getattr(entity, "id", "")).removeprefix("-100")
        return f"https://t.me/c/{raw_id}/{message_id}"

    @staticmethod
    def _author_of(message) -> str:
        """Ник автора, если Telethon уже знает отправителя.

        Специально не вызываем get_sender(): это лишний сетевой запрос на
        каждое сообщение. Если ника нет — контакты вытащит экстрактор из текста.
        """
        sender = message.sender
        if isinstance(sender, User):
            if sender.username:
                return f"@{sender.username}"
            name = " ".join(filter(None, [sender.first_name, sender.last_name]))
            return name.strip()
        if isinstance(sender, (Channel, Chat)):
            return getattr(sender, "title", "") or ""
        return ""

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:  # type: ignore[override]
        settings = get_settings()
        client = await get_client()
        entity = await self._resolve()

        cursor = self.cursor()
        min_id = int(cursor) if cursor and cursor.isdigit() else 0

        # Первый проход по новому каналу: не тянем всю историю, только окно.
        floor_date: datetime | None = None
        if not min_id:
            days = self.options.get("backfill_days", settings.backfill_days)
            floor_date = datetime.now(timezone.utc) - timedelta(days=int(days))
        if since is not None:
            floor_date = max(floor_date, since) if floor_date else since

        limit = int(self.options.get("limit", settings.max_messages_per_channel))

        async with throttle():
            try:
                async for message in client.iter_messages(entity, limit=limit, min_id=min_id):
                    text = (message.message or "").strip()
                    if not text:
                        continue  # медиа без подписи, служебные события

                    posted = message.date
                    if posted.tzinfo is None:
                        posted = posted.replace(tzinfo=timezone.utc)
                    if floor_date and posted < floor_date:
                        break  # идём от новых к старым — дальше только старее

                    yield RawPost(
                        external_id=str(message.id),
                        text=text,
                        posted_at=posted,
                        url=self._post_url(entity, message.id),
                        author=self._author_of(message),
                        extra={
                            "views": message.views,
                            "forwarded": message.forward is not None,
                            "channel": self.record.title or self.identifier,
                        },
                    )
            except FloodWaitError as exc:
                # Не блокируем весь сбор из-за одного канала.
                raise SourceError(f"FloodWait {exc.seconds} сек на {self.identifier}") from exc
            except (ChannelPrivateError, ChatAdminRequiredError) as exc:
                raise SourceError(f"Потерян доступ к {self.identifier}") from exc

        # Пауза между каналами — самая дешёвая страховка от лимитов.
        await asyncio.sleep(float(self.options.get("cooldown", 1.5)))

    def merge_cursor(self, previous: str | None, seen: list[str]) -> str | None:
        """id сообщений числовые, и приходят от новых к старым — берём максимум."""
        numbers = [int(x) for x in seen if x.isdigit()]
        if previous and previous.isdigit():
            numbers.append(int(previous))
        return str(max(numbers)) if numbers else previous
