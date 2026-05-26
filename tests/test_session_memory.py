"""Tests for session_memory: ingest, query, eviction, persistence, daemon."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest

from vocab.session_memory import SessionMemory, SessionEvent, RecallResult, run_daemon


class TestExtractTokens(unittest.TestCase):

    def test_tool_class(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(tool="read_file", file_path="src/auth.ts")
        self.assertIn("read", tokens)
        self.assertIn("auth", tokens)
        self.assertIn("src", tokens)

    def test_error_code(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(tool="tool_error", error_code="TS2322", file_path="src/spool.ts")
        self.assertIn("error", tokens)
        self.assertIn("TS2322", tokens)
        self.assertIn("spool", tokens)

    def test_raw_text_identifiers(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(raw="SpoolManager is not assignable to SpoolConfig")
        self.assertIn("SpoolManager", tokens)
        self.assertIn("SpoolConfig", tokens)

    def test_explicit_identifiers(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(identifiers=["TokenValidator", "AuthHandler"])
        self.assertIn("TokenValidator", tokens)
        self.assertIn("AuthHandler", tokens)

    def test_short_tokens_filtered(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(file_path="a/b.ts")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertNotIn("ts", tokens)
        self.assertNotIn(".ts", tokens)

    def test_unknown_tool_passes_through(self):
        from vocab.session_memory import extract_tokens
        tokens = extract_tokens(tool="custom_debug_tool")
        self.assertIn("custom_debug_tool", tokens)


class TestSessionMemory(unittest.TestCase):

    def setUp(self):
        self.mem = SessionMemory(max_events=10)

    def test_ingest_and_query(self):
        self.mem.ingest(tool="read_file", file_path="src/auth.ts")
        self.mem.ingest(tool="file_edit", file_path="src/auth.ts")
        r = self.mem.query("auth")
        self.assertIsNotNone(r)
        self.assertEqual(r.concept, "auth")
        self.assertIn("read", r.associations)
        self.assertIn("edit", r.associations)
        self.assertEqual(r.total_events, 2)

    def test_query_missing_concept(self):
        r = self.mem.query("nonexistent")
        self.assertIsNone(r)

    def test_error_recall(self):
        self.mem.ingest(tool="read_file", file_path="src/spool.ts")
        self.mem.ingest(tool="tool_error", file_path="src/spool.ts", error_code="TS2322")
        r = self.mem.query("TS2322")
        self.assertIsNotNone(r)
        self.assertIn("spool", r.associations)
        self.assertIn("error", r.associations)

    def test_recency(self):
        import time as tmod
        now = tmod.time()
        self.mem.ingest(tool="read", file_path="src/alpha.ts", timestamp=now)
        tmod.sleep(0.001)
        self.mem.ingest(tool="read", file_path="src/beta.ts", timestamp=now + 2)
        tmod.sleep(0.001)
        self.mem.ingest(tool="read", file_path="src/alpha.ts", timestamp=now + 4)
        r = self.mem.query("alpha")
        self.assertIsNotNone(r)
        self.assertGreater(r.recency, 0.5)

    def test_status(self):
        self.mem.ingest(tool="read", file_path="src/alpha.ts")
        self.mem.ingest(tool="edit", file_path="src/beta.ts")
        s = self.mem.status()
        self.assertEqual(s["events"], 2)
        self.assertGreater(s["unique_tokens"], 0)

    def test_fifo_eviction(self):
        for i in range(15):
            self.mem.ingest(tool="read", file_path=f"src/file_{i}.ts")
        s = self.mem.status()
        self.assertEqual(s["events"], 10)
        r = self.mem.query("file_0")
        self.assertIsNone(r, "oldest event should be evicted")
        r = self.mem.query("file_14")
        self.assertIsNotNone(r, "newest event should remain")

    def test_eviction_preserves_recent_assoc(self):
        for i in range(5):
            self.mem.ingest(tool="edit", file_path="src/core.ts")
        for i in range(10):
            self.mem.ingest(tool="read", file_path=f"src/other_{i}.ts")
        r = self.mem.query("core")
        self.assertIsNone(r, "core was in oldest 5 events, evicted at max=10")
    def test_status(self):
        self.mem.ingest(tool="read", file_path="src/a.ts")
        self.mem.ingest(tool="edit", file_path="src/b.ts")
        s = self.mem.status()
        self.assertEqual(s["events"], 2)
        self.assertGreater(s["unique_tokens"], 0)

    def test_fifo_eviction(self):
        for i in range(15):
            self.mem.ingest(tool="read", file_path=f"src/file{i}.ts")
        s = self.mem.status()
        self.assertEqual(s["events"], 10)
        r = self.mem.query("file0")
        self.assertIsNone(r, "oldest event should be evicted")
        r = self.mem.query("file14")
        self.assertIsNotNone(r, "newest event should remain")

    def test_eviction_preserves_recent_assoc(self):
        for i in range(5):
            self.mem.ingest(tool="edit", file_path="src/core.ts")
        for i in range(10):
            self.mem.ingest(tool="read", file_path=f"src/other{i}.ts")
        r = self.mem.query("core")
        self.assertIsNone(r)

    def test_save_and_load(self):
        for i in range(5):
            self.mem.ingest(tool="read", file_path=f"src/file{i}.ts")
        self.mem.ingest(tool="error", error_code="ERR001", file_path="src/file0.ts")

        path = os.path.join(tempfile.mkdtemp(), "mem.json.gz")
        self.mem.save(path)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 50)

        loaded = SessionMemory.load(path)
        self.assertEqual(loaded.status()["events"], 6)
        r = loaded.query("ERR001")
        self.assertIsNotNone(r)
        self.assertIn("file0", r.associations)

    def test_load_nonexistent_file_returns_empty(self):
        loaded = SessionMemory.load("/nonexistent/path.json.gz")
        self.assertEqual(loaded.status()["events"], 0)

    def test_load_corrupted_file_returns_empty(self):
        path = os.path.join(tempfile.mkdtemp(), "corrupt.json.gz")
        import gzip
        with gzip.open(path, "wb") as f:
            f.write(b"not valid json")
        loaded = SessionMemory.load(path)
        self.assertEqual(loaded.status()["events"], 0)

    def test_cross_session_recall(self):
        path = os.path.join(tempfile.mkdtemp(), "cross.json.gz")
        day1 = SessionMemory(max_events=100)
        day1.ingest(tool="edit", file_path="src/auth.ts")
        day1.ingest(tool="error", file_path="src/auth.ts", error_code="TS2741")
        day1.save(path)

        day2 = SessionMemory.load(path)
        day2.ingest(tool="read", file_path="src/auth.ts")
        r = day2.query("TS2741")
        self.assertIsNotNone(r)
        self.assertIn("auth", r.associations)
        self.assertIn("error", r.associations)

    def test_stress_many_events(self):
        mem = SessionMemory(max_events=5000)
        for i in range(2000):
            tool = ["read", "edit", "error", "grep"][i % 4]
            file_path = f"src/mod{i % 50}/file{i}.ts"
            err = f"ERR{i % 10}" if tool == "error" else ""
            mem.ingest(tool=tool, file_path=file_path, error_code=err)
        s = mem.status()
        self.assertEqual(s["events"], 2000)
        self.assertGreater(s["unique_tokens"], 20)
        r = mem.query("ERR0")
        self.assertIsNotNone(r)
        self.assertGreater(len(r.associations), 0)

    def test_max_events_default(self):
        mem = SessionMemory()
        self.assertEqual(mem.max_events, 5000)

    def test_ingest_returns_incrementing_id(self):
        id1 = self.mem.ingest(tool="read", file_path="src/a.ts")
        id2 = self.mem.ingest(tool="read", file_path="src/b.ts")
        self.assertEqual(id1, 0)
        self.assertEqual(id2, 1)

    def test_event_has_timestamp(self):
        eid = self.mem.ingest(tool="read", file_path="src/a.ts", timestamp=12345.0)
        self.assertEqual(self.mem.events[eid].timestamp, 12345.0)

    def test_query_order_by_frequency(self):
        self.mem.ingest(tool="read", file_path="src/core.ts")
        self.mem.ingest(tool="edit", file_path="src/core.ts")
        self.mem.ingest(tool="edit", file_path="src/core.ts")
        r = self.mem.query("core")
        assocs = list(r.associations.items())
        self.assertGreater(assocs[0][1], assocs[-1][1])

    def test_eviction_does_not_crash_empty(self):
        mem = SessionMemory(max_events=1)
        mem.ingest(tool="read", file_path="src/a.ts")
        mem.ingest(tool="edit", file_path="src/b.ts")
        mem.ingest(tool="error", error_code="ERR", file_path="src/c.ts")
        s = mem.status()
        self.assertEqual(s["events"], 1)

    def test_save_then_load_maintains_max_events(self):
        path = os.path.join(tempfile.mkdtemp(), "max.json.gz")
        mem = SessionMemory(max_events=123)
        mem.ingest(tool="read", file_path="src/a.ts")
        mem.save(path)
        loaded = SessionMemory.load(path)
        self.assertEqual(loaded.max_events, 123)

    def test_query_after_save_return_preserves_assoc(self):
        mem = SessionMemory(max_events=100)
        for i in range(10):
            mem.ingest(tool="read", file_path="src/shared.ts")
        path = os.path.join(tempfile.mkdtemp(), "shared.json.gz")
        mem.save(path)
        loaded = SessionMemory.load(path)
        r = loaded.query("shared")
        self.assertIsNotNone(r)
        self.assertIn("read", r.associations)

    def test_recency_approaches_one(self):
        for i in range(10):
            self.mem.ingest(tool="read", file_path=f"src/file{i}.ts")
        r = self.mem.query("file9")
        self.assertAlmostEqual(r.recency, 1.0, places=2)

    def test_recency_approaches_zero(self):
        self.mem.ingest(tool="read", file_path="src/file0.ts")
        for i in range(1, 10):
            self.mem.ingest(tool="read", file_path=f"src/file{i}.ts")
        r = self.mem.query("file0")
        self.assertLess(r.recency, 0.3)


if __name__ == "__main__":
    unittest.main()
