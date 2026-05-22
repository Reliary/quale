"""Generic base-36 index encoding — standalone."""

from __future__ import annotations

import string

_BASE36_ALPHABET = string.digits + string.ascii_lowercase


def _int_to_base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = []
    while n > 0:
        n, remainder = divmod(n, 36)
        digits.append(_BASE36_ALPHABET[remainder])
    return "".join(reversed(digits))


def _base36_to_int(s: str) -> int:
    result = 0
    for char in s:
        result = result * 36 + _BASE36_ALPHABET.index(char)
    return result


def encode_indices(indices: list[int]) -> str:
    return " ".join(_int_to_base36(i) for i in indices)


def decode_indices(encoded: str) -> list[int]:
    return [_base36_to_int(s) for s in encoded.split()]


def index_sequence_hash(indices: list[int]) -> str:
    """Produce a stable structural hash from an index sequence."""
    import hashlib
    return hashlib.sha256(encode_indices(indices).encode()).hexdigest()[:16]


def structural_similarity(indices_a: list[int], indices_b: list[int]) -> float:
    """Jaccard-like similarity between two index sequences using n-gram overlap."""
    def _ngrams(seq, n=3):
        return set(tuple(seq[i:i+n]) for i in range(len(seq)-n+1))
    if not indices_a or not indices_b:
        return 0.0
    a_ngrams = _ngrams(indices_a)
    b_ngrams = _ngrams(indices_b)
    if not a_ngrams or not b_ngrams:
        return 0.0
    intersection = a_ngrams & b_ngrams
    union = a_ngrams | b_ngrams
    return len(intersection) / len(union)
