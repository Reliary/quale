"""Property-based tests: random inputs on deterministic primitives."""
from __future__ import annotations

import unittest
import random
import string
import sys

from vocab.segmenter import segment
from vocab.index import _int_to_base36, _base36_to_int, encode_indices, decode_indices


def _random_text(max_len: int = 500) -> str:
    """Generate random text with varied delimiters."""
    chars = string.ascii_letters + string.digits + " \n\t,;|.:/!@#$%^&*()-_=+[]{}<>?"
    length = random.randint(0, max_len)
    return "".join(random.choice(chars) for _ in range(length))


def _random_vocabulary(size: int = 20) -> list[str]:
    """Generate random phrase-like strings."""
    vocab: list[str] = []
    for _ in range(size):
        phrase_len = random.randint(2, 30)
        chars = string.ascii_letters + string.digits + "_/-:.+"
        phrase = "".join(random.choice(chars) for _ in range(phrase_len))
        vocab.append(phrase)
    return list(dict.fromkeys(vocab))


class TestSegmenterDeterministic(unittest.TestCase):
    """Segmenter must produce identical output for identical input."""

    def test_deterministic_newline(self):
        text = "line1\nline2\nline3\n"
        self.assertEqual(segment(text), segment(text))

    def test_deterministic_code(self):
        text = "def foo():\n    return bar\n"
        self.assertEqual(segment(text), segment(text))

    def test_deterministic_random(self):
        for _ in range(50):
            text = _random_text(200)
            self.assertEqual(segment(text), segment(text))

    def test_deterministic_empty(self):
        self.assertEqual(segment(""), segment(""))

    def test_deterministic_unicode(self):
        text = "café résumé 日本語"
        self.assertEqual(segment(text), segment(text))


class TestSegmenterBoundaries(unittest.TestCase):
    """Segmenter must handle edge cases without crashing."""

    def test_empty_string(self):
        result = segment("")
        self.assertIsNotNone(result)
        self.assertIsInstance(result.phrases, list)

    def test_single_char(self):
        result = segment("x")
        self.assertIsNotNone(result)

    def test_only_delimiters(self):
        result = segment("\n\n\n")
        self.assertIsNotNone(result)

    def test_very_long_line(self):
        result = segment("x" * 10000)
        self.assertIsNotNone(result)

    def test_binary_like(self):
        result = segment("\x00\x01\x02\xff")
        self.assertIsNotNone(result)

    def test_mixed_newlines_and_commas(self):
        result = segment("a,b\nc,d\ne,f")
        self.assertEqual(result.strategy, "delimiter")
        self.assertGreaterEqual(len(result.phrases), 2)

    def test_no_delimiters(self):
        result = segment("abcdefghijklmnopqrstuvwxyz")
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result.phrases), 1)


class TestIndexRoundTrip(unittest.TestCase):
    """Index encoding must survive encode→decode round trip."""

    def test_base36_round_trip_small(self):
        for i in [0, 1, 5, 10, 100, 255, 1000, 65535]:
            self.assertEqual(_base36_to_int(_int_to_base36(i)), i)

    def test_base36_round_trip_large(self):
        for i in [10**6, 10**9, 2**31 - 1, 2**32, 10**12]:
            self.assertEqual(_base36_to_int(_int_to_base36(i)), i)

    def test_base36_round_trip_random(self):
        for _ in range(100):
            i = random.randint(0, 10**12)
            self.assertEqual(_base36_to_int(_int_to_base36(i)), i)

    def test_encode_decode_small_vocab(self):
        vocab = ["apple", "banana", "cherry"]
        indices = [0, 1, 2, 0, 1]
        encoded = encode_indices(indices)
        decoded = decode_indices(encoded)
        self.assertEqual(decoded, indices)

    def test_encode_decode_random(self):
        for _ in range(20):
            size = random.randint(2, 50)
            seq_len = random.randint(0, 100)
            indices = [random.randint(0, size - 1) for _ in range(seq_len)]
            encoded = encode_indices(indices)
            decoded = decode_indices(encoded)
            self.assertEqual(decoded, indices)

    def test_empty_sequence(self):
        encoded = encode_indices([])
        decoded = decode_indices(encoded)
        self.assertEqual(decoded, [])

    def test_single_element(self):
        encoded = encode_indices([5])
        decoded = decode_indices(encoded)
        self.assertEqual(decoded, [5])

    def test_repeated_indices(self):
        encoded = encode_indices([0, 0, 0, 0, 0])
        decoded = decode_indices(encoded)
        self.assertEqual(decoded, [0, 0, 0, 0, 0])


class TestVocabularyBuildStability(unittest.TestCase):
    """build_vocabulary must be deterministic."""

    def test_build_from_segment(self):
        from vocab.vocabulary import build_vocabulary
        from vocab.segmenter import segment

        text = "apple banana cherry apple banana date"
        seg = segment(text)
        v1 = build_vocabulary(seg.phrases, seg.strategy, seg.delimiter)
        seg2 = segment(text)
        v2 = build_vocabulary(seg2.phrases, seg2.strategy, seg2.delimiter)
        self.assertEqual(v1.entries, v2.entries)

    def test_build_random_deterministic(self):
        from vocab.vocabulary import build_vocabulary
        from vocab.segmenter import segment

        for _ in range(30):
            text = _random_text(300)
            seg = segment(text)
            v1 = build_vocabulary(seg.phrases, seg.strategy, seg.delimiter)
            seg2 = segment(text)
            v2 = build_vocabulary(seg2.phrases, seg2.strategy, seg2.delimiter)
            self.assertEqual(v1.entries, v2.entries)

    def test_empty_input(self):
        from vocab.vocabulary import build_vocabulary
        from vocab.segmenter import segment

        seg = segment("")
        v = build_vocabulary(seg.phrases, seg.strategy, seg.delimiter)
        self.assertEqual(len(v.entries), 0)


class TestTokenExtraction(unittest.TestCase):
    """Regex export token extraction must not crash on any input."""

    def test_extract_random_text(self):
        import re
        pattern = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')
        for _ in range(100):
            text = _random_text(500)
            matches = pattern.findall(text)
            for m in matches:
                self.assertGreaterEqual(len(m), 4)
                self.assertLessEqual(len(m), 40)

    def test_unicode_does_not_crash(self):
        import re
        pattern = re.compile(r'\b[A-Z][A-Za-z0-9_]{3,40}\b')
        texts = [
            "你好世界 abc",
            "café résumé",
            "\u0000\u0001\u0002",
            "a" * 10000,
            "\xff\xfe\x00",
        ]
        for text in texts:
            matches = pattern.findall(text)
            for m in matches:
                self.assertGreaterEqual(len(m), 4)
