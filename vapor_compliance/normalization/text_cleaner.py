from __future__ import annotations
import re
import unicodedata


_MULTI_SPACE = re.compile(r"\s+")
_PUNCTUATION_EXCEPT_SLASH_PERCENT = re.compile(r"[^\w\s/%.-]")


def clean(text: str) -> str:
    """Lowercase, unicode-normalize, remove noise punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _PUNCTUATION_EXCEPT_SLASH_PERCENT.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    return clean(text).split()


def remove_stopwords(tokens: list[str], stopwords: set[str] | None = None) -> list[str]:
    _default = {"the", "a", "an", "and", "or", "of", "with", "by", "for", "in", "at"}
    sw = stopwords if stopwords is not None else _default
    return [t for t in tokens if t not in sw]
