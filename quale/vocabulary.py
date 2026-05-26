"""Generic vocabulary builder — frequency-weighted, deduplicated, standalone."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    index: int
    text: str
    frequency: int
    text_hash: str


@dataclass
class Vocabulary:
    entries: list[VocabularyEntry]
    strategy: str
    delimiter: str | None = None
    checksum: str = ""

    def lookup(self, index: int) -> str | None:
        for entry in self.entries:
            if entry.index == index:
                return entry.text
        return None

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def total_frequency(self) -> int:
        return sum(e.frequency for e in self.entries)

    @property
    def unique_frequency(self) -> int:
        return self.size

    def frequency_quartiles(self) -> tuple[float, float, float]:
        """Return Q1, Q2 (median), Q3 frequencies."""
        if not self.entries:
            return (0.0, 0.0, 0.0)
        freqs = sorted([e.frequency for e in self.entries])
        n = len(freqs)
        return (float(freqs[n // 4]), float(freqs[n // 2]), float(freqs[3 * n // 4]))


def _compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_vocabulary(phrases: list[str], strategy: str, delimiter: str | None = None) -> Vocabulary:
    if not phrases:
        return Vocabulary(entries=[], strategy=strategy, delimiter=delimiter)
    frequency_map: dict[str, int] = {}
    hash_map: dict[str, str] = {}
    for phrase in phrases:
        h = _compute_hash(phrase)
        if h not in hash_map:
            hash_map[h] = phrase
        frequency_map[h] = frequency_map.get(h, 0) + 1
    sorted_hashes = sorted(frequency_map.keys(), key=lambda h: frequency_map[h], reverse=True)
    entries = []
    for idx, h in enumerate(sorted_hashes, start=1):
        entries.append(VocabularyEntry(index=idx, text=hash_map[h], frequency=frequency_map[h], text_hash=h))
    vocab_text = "\n".join(f"{e.index}: {e.text}" for e in entries)
    checksum = hashlib.sha256(vocab_text.encode("utf-8")).hexdigest()
    return Vocabulary(entries=entries, strategy=strategy, delimiter=delimiter, checksum=checksum)
