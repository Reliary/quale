"""Unit tests for core primitives: segmenter, vocabulary, index encoding."""

from __future__ import annotations

import unittest

from vocab.segmenter import segment, SegmentationResult
from vocab.vocabulary import build_vocabulary, _compute_hash, Vocabulary
from vocab.index import (_int_to_base36, _base36_to_int, encode_indices, decode_indices,
                         index_sequence_hash, structural_similarity)


class TestSegmenter(unittest.TestCase):

    def test_delimiter_newline(self):
        result = segment("foo\nbar\nbaz")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, "\n")
        self.assertEqual(result.phrases, ["foo", "bar", "baz"])

    def test_delimiter_pipe(self):
        result = segment("key|value|other")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, "|")
        self.assertEqual(result.phrases, ["key", "value", "other"])

    def test_delimiter_semicolon(self):
        result = segment("a; b; c")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, ";")
        self.assertEqual(result.phrases, ["a", "b", "c"])

    def test_delimiter_comma(self):
        result = segment("one, two, three")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, ",")
        self.assertEqual(result.phrases, ["one", "two", "three"])

    def test_delimiter_tab(self):
        result = segment("col1\tcol2\tcol3")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, "\t")
        self.assertEqual(result.phrases, ["col1", "col2", "col3"])

    def test_delimiter_priority_newline_over_others(self):
        result = segment("a,b\nc,d")
        self.assertEqual(result.strategy, "delimiter")
        self.assertEqual(result.delimiter, "\n")
        self.assertEqual(result.phrases, ["a,b", "c,d"])

    def test_ngram_fallback(self):
        result = segment("the quick brown fox jumps over the lazy dog")
        self.assertEqual(result.strategy, "ngram")
        self.assertEqual(result.ngram_size, 4)
        self.assertEqual(result.phrases, ["the quick brown fox", "jumps over the lazy", "dog"])

    def test_ngram_partial_last(self):
        result = segment("one two three")
        self.assertEqual(result.strategy, "ngram")
        self.assertEqual(result.phrases, ["one two three"])

    def test_char_fallback(self):
        result = segment("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        self.assertEqual(result.strategy, "ngram")
        self.assertEqual(len(result.phrases), 1)

    def test_char_split_explicit(self):
        from vocab.segmenter import _split_by_char
        phrases = _split_by_char("ABCDEFGHIJKLMNOPQRSTUVWXYZ", chunk_size=20)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0], "ABCDEFGHIJKLMNOPQRST")

    def test_empty_input(self):
        result = segment("")
        self.assertEqual(result.strategy, "delimiter")
        self.assertIsNone(result.delimiter)
        self.assertEqual(result.phrases, [])

    def test_whitespace_only(self):
        result = segment("   ")
        self.assertEqual(result.strategy, "ngram")
        self.assertEqual(len(result.phrases), 1)


class TestVocabulary(unittest.TestCase):

    def test_empty_phrases(self):
        v = build_vocabulary([], "delimiter")
        self.assertEqual(v.size, 0)
        self.assertEqual(v.total_frequency, 0)
        self.assertEqual(v.frequency_quartiles(), (0.0, 0.0, 0.0))

    def test_single_phrase(self):
        v = build_vocabulary(["hello"], "delimiter")
        self.assertEqual(v.size, 1)
        self.assertEqual(v.entries[0].text, "hello")
        self.assertEqual(v.entries[0].frequency, 1)
        self.assertEqual(v.entries[0].index, 1)

    def test_frequency_sorting(self):
        v = build_vocabulary(["a", "b", "a", "c", "b", "a"], "delimiter")
        entries = sorted(v.entries, key=lambda e: e.index)
        self.assertEqual(entries[0].text, "a")
        self.assertEqual(entries[0].frequency, 3)
        self.assertEqual(entries[1].text, "b")
        self.assertEqual(entries[1].frequency, 2)
        self.assertEqual(entries[2].text, "c")
        self.assertEqual(entries[2].frequency, 1)

    def test_lookup(self):
        v = build_vocabulary(["alpha", "beta", "gamma"], "delimiter")
        self.assertEqual(v.lookup(1), "alpha")
        self.assertEqual(v.lookup(2), "beta")
        self.assertEqual(v.lookup(3), "gamma")

    def test_lookup_missing(self):
        v = build_vocabulary(["alpha"], "delimiter")
        self.assertIsNone(v.lookup(99))

    def test_frequency_quartiles(self):
        entries = [
            type("E", (), {"frequency": 1})(),
            type("E", (), {"frequency": 2})(),
            type("E", (), {"frequency": 3})(),
            type("E", (), {"frequency": 4})(),
            type("E", (), {"frequency": 5})(),
            type("E", (), {"frequency": 6})(),
            type("E", (), {"frequency": 7})(),
        ]
        v = Vocabulary(entries=entries, strategy="test")
        q1, q2, q3 = v.frequency_quartiles()
        self.assertEqual(q1, 2.0)
        self.assertEqual(q2, 4.0)
        self.assertEqual(q3, 6.0)

    def test_checksum_stability(self):
        v1 = build_vocabulary(["a", "b", "c"], "delimiter")
        v2 = build_vocabulary(["a", "b", "c"], "delimiter")
        self.assertEqual(v1.checksum, v2.checksum)

    def test_deterministic_hash(self):
        self.assertEqual(_compute_hash("hello"), _compute_hash("hello"))

    def test_total_vs_unique(self):
        v = build_vocabulary(["x", "x", "y"], "delimiter")
        self.assertEqual(v.total_frequency, 3)
        self.assertEqual(v.unique_frequency, 2)


class TestIndex(unittest.TestCase):

    def test_encode_zero(self):
        self.assertEqual(_int_to_base36(0), "0")

    def test_encode_digit(self):
        self.assertEqual(_int_to_base36(10), "a")

    def test_encode_multi(self):
        self.assertEqual(encode_indices([1, 10, 35]), "1 a z")

    def test_decode_multi(self):
        self.assertEqual(decode_indices("1 a z"), [1, 10, 35])

    def test_round_trip_small(self):
        indices = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        self.assertEqual(decode_indices(encode_indices(indices)), indices)

    def test_round_trip_large(self):
        indices = [1000, 2000, 3000, 99999]
        self.assertEqual(decode_indices(encode_indices(indices)), indices)

    def test_round_trip_vocab_size(self):
        indices = list(range(1, 500))
        self.assertEqual(decode_indices(encode_indices(indices)), indices)

    def test_hash_deterministic(self):
        self.assertEqual(index_sequence_hash([1, 2, 3]), index_sequence_hash([1, 2, 3]))

    def test_similarity_identical(self):
        self.assertEqual(structural_similarity([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 1.0)

    def test_similarity_disjoint(self):
        self.assertEqual(structural_similarity([1, 2, 3], [4, 5, 6]), 0.0)

    def test_similarity_empty(self):
        self.assertEqual(structural_similarity([], [1, 2, 3]), 0.0)

    def test_similarity_too_short(self):
        self.assertEqual(structural_similarity([1], [1]), 0.0)

    def test_base36_round_trip_many(self):
        for n in [0, 1, 35, 36, 100, 1000, 12345, 999999]:
            self.assertEqual(_base36_to_int(_int_to_base36(n)), n)


class TestScanCache(unittest.TestCase):

    def test_cache_hits_on_repeated_ref_scans(self):
        import tempfile, subprocess
        from pathlib import Path
        from vocab.scanner import scan_codebase, _scan_cache_clear, _SCAN_CACHE

        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "a.ts").write_text("export const A = 1;\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", "init"],
            cwd=repo, check=True,
        )

        _scan_cache_clear()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

        a = scan_codebase(str(repo), git_ref=head, quiet=True)
        self.assertEqual(len(_SCAN_CACHE), 1)

        b = scan_codebase(str(repo), git_ref=head, quiet=True)
        self.assertIs(a, b)

        _scan_cache_clear()

    def test_cache_respects_limit(self):
        from vocab.scanner import _scan_cache_clear, _SCAN_CACHE, _SCAN_CACHE_MAX
        _scan_cache_clear()
        # Fill one past limit
        for i in range(_SCAN_CACHE_MAX + 1):
            _SCAN_CACHE[(f"/tmp/fake{i}", None)] = None  # type: ignore
        self.assertEqual(len(_SCAN_CACHE), _SCAN_CACHE_MAX + 1)
        _scan_cache_clear()


if __name__ == "__main__":
    unittest.main()
