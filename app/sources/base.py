"""Контракт источника.

Чтобы подключить новую площадку (Upwork, Kwork, FL.ru, justjoin.it), нужно
ровно две вещи:

    1. Наследник BaseSource с реализованным fetch().
    2. Декоратор @register("ключ") над классом.

Всё остальное — планировщик, дедуп, скоринг, отправка — уже работает.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from app.core.enums import SourceKind
from app.core.models import Source
from app.core.schemas import RawPost

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Ошибка получения данных. Планировщик поймает её и пометит источник."""


class BaseSource(ABC):
    """Базовый источник постов.

    Экземпляр создаётся на каждый проход и живёт недолго, поэтому тяжёлые
    ресурсы (клиент Telethon, http-сессия) передаются снаружи, а не создаются
    в конструкторе.
    """

    #: Тип источника — определяет, какой планировщик его обходит.
    kind: SourceKind = SourceKind.JOBBOARD
    #: Человекочитаемое имя для UI.
    display_name: str = "Источник"
    #: Нужен ли этому источнику клиент Telethon.
    needs_telegram: bool = False

    def __init__(self, record: Source) -> None:
        self.record = record
        self.options: dict = dict(record.options or {})

    @property
    def identifier(self) -> str:
        return self.record.identifier

    @abstractmethod
    def fetch(self, since: datetime | None) -> AsyncIterator[RawPost]:
        """Отдаёт посты новее `since` (или новее сохранённого курсора).

        Реализация обязана быть async-генератором и не должна падать на
        отдельном «кривом» посте — такие лучше пропускать с логом.
        """
        raise NotImplementedError

    async def verify(self) -> tuple[bool, str]:
        """Проверка доступности. Возвращает (живой?, пояснение для UI)."""
        return True, "OK"

    def cursor(self) -> str | None:
        """Текущий курсор обхода (id последнего обработанного элемента)."""
        return self.record.last_external_id

    def merge_cursor(self, previous: str | None, seen: list[str]) -> str | None:
        """Куда сдвинуть курсор после прохода.

        База берёт последний увиденный id. Источники с числовыми id (Telegram)
        переопределяют метод, чтобы сравнивать числа, а не строки.
        """
        return seen[-1] if seen else previous

    def build_url(self, post: RawPost) -> str:
        """Ссылка на оригинал. Источник переопределяет, если умеет лучше."""
        return post.url

    def __repr__(self) -> str:  # pragma: no cover - отладочное
        return f"<{type(self).__name__} {self.identifier}>"
