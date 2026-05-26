"""Generic phrase segmentation — standalone, no content tuning, works on arbitrary text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


SegmentStrategy = Literal["delimiter", "ngram", "char"]


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    strategy: SegmentStrategy
    phrases: list[str]
    delimiter: str | None = None
    ngram_size: int = 0


_DELIMITERS = ["\n", "|", ";", ",", "\t"]


def _split_by_delimiter(text: str, delimiter: str) -> list[str]:
    parts = [p.strip() for p in text.split(delimiter)]
    return [p for p in parts if p]


def _split_by_ngram(text: str, n: int) -> list[str]:
    tokens = text.split()
    if len(tokens) <= n:
        return [text] if text else []
    phrases = []
    for i in range(0, len(tokens) - n + 1, n):
        phrases.append(" ".join(tokens[i:i + n]))
    remainder = len(tokens) % n
    if remainder > 0:
        phrases.append(" ".join(tokens[-remainder:]))
    return phrases


def _split_by_char(text: str, chunk_size: int = 20) -> list[str]:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def segment(text: str) -> SegmentationResult:
    if not text:
        return SegmentationResult(strategy="delimiter", phrases=[], delimiter=None)
    for delimiter in _DELIMITERS:
        if delimiter in text:
            phrases = _split_by_delimiter(text, delimiter)
            if phrases:
                return SegmentationResult(strategy="delimiter", phrases=phrases, delimiter=delimiter)
    ngram_phrases = _split_by_ngram(text, n=4)
    if ngram_phrases:
        return SegmentationResult(strategy="ngram", phrases=ngram_phrases, ngram_size=4)
    char_phrases = _split_by_char(text, chunk_size=20)
    return SegmentationResult(strategy="char", phrases=char_phrases)
