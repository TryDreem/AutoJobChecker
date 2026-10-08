"""Приведение текста к виду, пригодному для анализа и показа."""

from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*(br|/p|/div|/li)\s*/?\s*>", re.IGNORECASE)
_LI_RE = re.compile(r"<\s*li[^>]*>", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t ]+")
_NL_RE = re.compile(r"\n{3,}")
_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f1e6-\U0001f1ff" "←-⇿"
    "️" "]+",
    flags=re.UNICODE,
)
_URL_RE = re.compile(r"https?://\S+|t\.me/\S+|www\.\S+", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Декоративные линейки, которыми в каналах разделяют посты.
_DECOR_RE = re.compile(r"[─━═▬▪️•·➖\-_=~*#]{4,}")


def strip_html(text: str) -> str:
    """Убирает разметку из описаний бирж.

    Некоторые API (Arbeitnow) отдают HTML, дважды прогнанный через escape,
    поэтому раскодируем сущности в два прохода.
    """
    if not text:
        return ""
    out = html.unescape(html.unescape(text))
    out = _LI_RE.sub("\n• ", out)
    out = _BR_RE.sub("\n", out)
    out = _TAG_RE.sub(" ", out)
    return normalize_ws(out)


def normalize_ws(text: str) -> str:
    """Схлопывает пробелы и лишние переводы строк."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = _WS_RE.sub(" ", out)
    out = "\n".join(line.strip() for line in out.split("\n"))
    return _NL_RE.sub("\n\n", out).strip()


def clean_for_display(text: str) -> str:
    """Текст для показа в боте: без декоративных линеек и висячих пробелов."""
    return normalize_ws(_DECOR_RE.sub("", text))


def normalize_for_match(text: str) -> str:
    """Единый вид для поиска ключевых слов: нижний регистр, без эмодзи и ссылок.

    Ссылки выкидываем намеренно: домен вроде `python-jobs.com` в подписи канала
    иначе засчитывался бы как упоминание стека в каждом посте.
    """
    if not text:
        return ""
    out = text.lower().replace("ё", "е")
    out = _URL_RE.sub(" ", out)
    out = _EMOJI_RE.sub(" ", out)
    return _WS_RE.sub(" ", out)


def normalize_for_hash(text: str) -> str:
    """Максимально «голый» вид для дедупликации.

    Пересланный пост обычно отличается от оригинала только эмодзи, ссылками
    и подписью канала — всё это здесь стирается.
    """
    out = normalize_for_match(text)
    out = _PUNCT_RE.sub(" ", out)
    return " ".join(out.split())


def detect_lang(text: str) -> str:
    """Грубое определение языка. Полноценный детектор здесь не окупается."""
    if not text:
        return ""
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    polish = sum(1 for ch in text.lower() if ch in "ąćęłńóśźż")
    if cyrillic > latin:
        return "ru"
    if polish > 2:
        return "pl"
    return "en" if latin else ""


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """Обрезает по границе слова, чтобы не рвать текст посередине."""
    if len(text) <= limit:
        return text
    cut = text[: limit - len(suffix)]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + suffix


def first_line(text: str, limit: int = 120) -> str:
    """Первая содержательная строка — из неё получается заголовок поста."""
    for line in clean_for_display(text).split("\n"):
        candidate = line.strip(" .:-—#*")
        if len(candidate) >= 8:
            return truncate(candidate, limit)
    return truncate(clean_for_display(text).replace("\n", " "), limit)
