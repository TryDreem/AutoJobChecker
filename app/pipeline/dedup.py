"""Отсев повторов.

Одна и та же вакансия расходится по десятку каналов, обрастая эмодзи и
подписями. Поэтому два уровня:

  * точный хеш «голого» текста — ловит копипасту один в один;
  * simhash по 3-граммам слов — ловит перепечатки с правками.

Simhash выбран вместо MinHash из-за компактности: одно 64-битное число
в строке БД, сравнение — XOR и подсчёт битов.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from app.pipeline.text import normalize_for_hash

BITS = 64
# Порог похожести. 3 бита из 64 — это «отличия в паре слов»;
# выше 6 начинают склеиваться разные вакансии одной компании.
DEFAULT_THRESHOLD = 4

# Короткие посты дают неустойчивый simhash — их сравниваем только точным хешем.
MIN_TOKENS_FOR_SIMHASH = 12


def content_hash(text: str) -> str:
    """Хеш нормализованного текста: копипаста один в один."""
    return hashlib.blake2b(normalize_for_hash(text).encode("utf-8"), digest_size=16).hexdigest()


def _shingles(text: str, size: int = 3) -> list[str]:
    words = normalize_for_hash(text).split()
    if len(words) < size:
        return words
    return [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]


def simhash(text: str) -> int:
    """64-битный отпечаток текста. Похожие тексты дают близкие отпечатки."""
    tokens = _shingles(text)
    if len(tokens) < MIN_TOKENS_FOR_SIMHASH:
        return 0

    vector = [0] * BITS
    for token, weight in Counter(tokens).items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(BITS):
            vector[bit] += weight if (value >> bit) & 1 else -weight

    result = 0
    for bit in range(BITS):
        if vector[bit] > 0:
            result |= 1 << bit
    # SQLite хранит целые со знаком, а старший бит ломает сравнение —
    # приводим к диапазону int64.
    return result - (1 << BITS) if result >= (1 << (BITS - 1)) else result


def hamming(left: int, right: int) -> int:
    return bin((left ^ right) & ((1 << BITS) - 1)).count("1")


def is_similar(left: int, right: int, threshold: int = DEFAULT_THRESHOLD) -> bool:
    if not left or not right:
        return False
    return hamming(left, right) <= threshold


def find_duplicate(
    candidate_hash: str,
    candidate_simhash: int,
    known: list[tuple[int, str, int]],
    threshold: int = DEFAULT_THRESHOLD,
) -> int | None:
    """Ищет оригинал среди уже сохранённых.

    `known` — список (id, content_hash, simhash) недавних записей.
    Возвращает id первого совпадения либо None.
    """
    for vacancy_id, other_hash, other_simhash in known:
        if candidate_hash and candidate_hash == other_hash:
            return vacancy_id
        if is_similar(candidate_simhash, other_simhash, threshold):
            return vacancy_id
    return None
