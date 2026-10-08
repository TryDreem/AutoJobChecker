"""Реестр источников: строка из БД -> живой объект-парсер."""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from app.core.models import Source
from app.sources.base import BaseSource

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseSource]] = {}

T = TypeVar("T", bound=BaseSource)


def register(key: str) -> Callable[[type[T]], type[T]]:
    """Декоратор регистрации. `key` совпадает с полем Source.identifier
    для бирж, либо с Source.kind для универсальных источников (telegram, rss).
    """

    def wrapper(cls: type[T]) -> type[T]:
        if key in _REGISTRY:
            raise ValueError(f"Источник '{key}' уже зарегистрирован")
        _REGISTRY[key] = cls
        return cls

    return wrapper


def build(record: Source) -> BaseSource | None:
    """Создаёт парсер для строки БД.

    Сначала ищем точный ключ (`remoteok`), затем — общий по типу (`telegram`).
    """
    cls = _REGISTRY.get(record.identifier) or _REGISTRY.get(str(record.kind))
    if cls is None:
        log.warning("Нет реализации для источника %s/%s", record.kind, record.identifier)
        return None
    return cls(record)


def known_keys() -> list[str]:
    return sorted(_REGISTRY)


def load_all() -> None:
    """Импортирует модули источников, чтобы сработали декораторы.

    Новый файл в app/sources/ достаточно добавить в этот список.
    """
    from app.sources import jobboards, rss, scrapy_websites, telegram, upwork  # noqa: F401

    log.debug("Зарегистрированы источники: %s", known_keys())
