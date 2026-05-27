"""Unit tests for pure functions in reports.py and bootstrap.py."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock

from quale.reports import (
    _source_stem, _test_stem,
    _risk_vector,
    _fused_priority_ranking,
    _file_temperature,
    _safety_envelope,
    _marginal_candidate_score,
    _scope_creep_guard,
    _verification_confidence,
    _verification_desert_reason,
    _verification_desert_score,
    _lattice_confidence,
    _patterns_confidence,
)
from quale.bootstrap import _task_file_role, _task_role_rank
from quale.scanner import FileVocab, _structural_information_score, _is_actionable_identifier
from quale.reports import (
    _check_hub_risk, _check_clone_flag,
)
from unittest.mock import MagicMock


class TestSourceStem(unittest.TestCase):

    def test_basic_path(self):
        self.assertEqual(_source_stem("src/handlers/ingest.go"), "ingest")

    def test_with_special_chars(self):
        self.assertEqual(_source_stem("src/handlers/ingest_test.go"), "ingesttest")

    def test_upper_case(self):
        self.assertEqual(_source_stem("src/API/UserProfile.ts"), "userprofile")

    def test_dot_in_name(self):
        self.assertEqual(_source_stem("src/core.v2.ts"), "corev2")

    def test_nested_path(self):
        self.assertEqual(_source_stem("packages/core/src/spool.ts"), "spool")


class TestTestStem(unittest.TestCase):

    def test_test_prefix_stripped(self):
        self.assertEqual(_test_stem("tests/test_ingest.go"), "ingest")

    def test_test_suffix_stripped(self):
        self.assertEqual(_test_stem("tests/math_test.go"), "math")

    def test_spec_stripped(self):
        self.assertEqual(_test_stem("spec/router_spec.rb"), "router")

    def test_dot_test_stripped(self):
        self.assertEqual(_test_stem("tests/core.test.ts"), "core")

    def test_multiple_markers(self):
        self.assertEqual(_test_stem("tests/test_failure_test.go"), "failure")

    def test_no_marker(self):
        self.assertEqual(_test_stem("tests/helper.go"), "helper")

    def test_empty_stem(self):
        self.assertEqual(_test_stem("tests/_test.go"), "")


class TestRiskVector(unittest.TestCase):

    def test_cascade_dominant(self):
        result = _risk_vector([], [], {"damped_score": 10.0}, {})
        self.assertEqual(result["dominant"], "cascade")
        self.assertGreater(result["vector"]["cascade"], 0.5)

    def test_stable_dominant(self):
        result = _risk_vector([{"file": "a.go"}, {"file": "b.go"}], [], None, None)
        self.assertEqual(result["dominant"], "stable")

    def test_blast_dominant(self):
        blast = [{"file": "a.go", "shared_concepts": 5}]
        result = _risk_vector([], blast, None, None)
        self.assertEqual(result["dominant"], "blast")

    def test_zero_inputs(self):
        result = _risk_vector([], [], None, None)
        self.assertEqual(result["dominant"], "cascade")
        self.assertIn("instruction", result)

    def test_deficit_dominant(self):
        result = _risk_vector([], [], None, {"missing": 5})
        self.assertEqual(result["dominant"], "deficit")

    def test_vector_sums_to_one(self):
        result = _risk_vector(
            [{"file": "x.go"}],
            [{"file": "y.go", "shared_concepts": 3}],
            {"damped_score": 0.5},
            {"missing": 1},
        )
        self.assertAlmostEqual(sum(result["vector"].values()), 1.0, places=2)

    def test_instruction_present(self):
        result = _risk_vector([], [{"file": "x.go"}], None, None)
        self.assertIn("instruction", result)
        self.assertIn("risk", result["instruction"].lower())


class TestFusedPriorityRanking(unittest.TestCase):

    def test_both_high_gets_super_linear(self):
        blast = [{"file": "a.go", "shared_concepts": 5}, {"file": "b.go", "shared_concepts": 1}]
        mirror = {"mirror_files": ["a.go"]}
        result = _fused_priority_ranking(["c.go"], blast, mirror)
        self.assertEqual(result[0], "a.go")

    def test_no_mirror(self):
        blast = [{"file": "a.go", "shared_concepts": 5}]
        result = _fused_priority_ranking([], blast, {})
        self.assertEqual(result[0], "a.go")

    def test_empty_inputs(self):
        result = _fused_priority_ranking([], [], {})
        self.assertEqual(result, [])


class TestFileTemperature(unittest.TestCase):

    def test_hot_recent_changes(self):
        lc = [{"file": "x.go", "signal": "GROWING"}]
        st = [{"file": "x.go", "persistence": 0.1}]
        self.assertEqual(_file_temperature("x.go", lc, st, {}), "HOT")

    def test_cold_stable(self):
        lc = [{"file": "x.go", "signal": "DECAYING", "stale_weeks": 12}]
        st = [{"file": "x.go", "persistence": 0.9}]
        self.assertEqual(_file_temperature("x.go", lc, st, {}), "COLD")

    def test_warm_mixed(self):
        lc = [{"file": "x.go", "signal": "ACTIVE", "stale_weeks": 4}]
        st = [{"file": "x.go", "persistence": 0.5}]
        self.assertEqual(_file_temperature("x.go", lc, st, {}), "WARM")

    def test_fallback_on_no_data(self):
        self.assertEqual(_file_temperature("x.go", [], [], None), "WARM")

    def test_stability_only_cold(self):
        lc = [{"file": "other.go", "signal": "STABLE"}]
        st = [{"file": "x.go", "persistence": 0.9}]
        self.assertEqual(_file_temperature("x.go", lc, st, {}), "COLD")


class TestSafetyEnvelope(unittest.TestCase):

    def test_inside_when_changed_in_blast(self):
        blast = [{"file": "a.go"}]
        result = _safety_envelope(["a.go"], blast, [])
        self.assertIn("a.go", result.get("inside", []))

    def test_boundary_when_unrelated_to_changed(self):
        blast = [{"file": "b.go"}]
        result = _safety_envelope(["a.go"], blast, [])
        self.assertIn("b.go", result.get("at_boundary", []))

    def test_stable_on_boundary(self):
        stable = [{"file": "c.go"}]
        result = _safety_envelope(["a.go"], [], stable)
        self.assertIn("c.go", result.get("stable_on_boundary", []))

    def test_empty_inputs(self):
        result = _safety_envelope([], [], [])
        self.assertEqual(result["at_boundary"], [])
        self.assertEqual(result["inside"], [])


class TestMarginalCandidateScore(unittest.TestCase):

    def test_top_rank_scores_positive(self):
        result = _marginal_candidate_score(0, ["a_test.go"], [], set())
        self.assertEqual(result, 0.3)

    def test_last_rank_low_score(self):
        result = _marginal_candidate_score(9, ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"], [], {"z"})
        self.assertEqual(result, 0.3)

    def test_stem_match_gets_high_score(self):
        result = _marginal_candidate_score(0, ["test_a.py"], [], {"a"})
        self.assertEqual(result, 0.9)

    def test_out_of_range_returns_zero(self):
        result = _marginal_candidate_score(99, ["a_test.go"], [], set())
        self.assertEqual(result, 0.0)


class TestEditSprawlGuard(unittest.TestCase):

    def test_no_sprawl_when_empty(self):
        result = _scope_creep_guard(["a.go"], [], [], [])
        self.assertEqual(result["mode"], "report_only")

    def test_has_questioned_when_avoid_expanding(self):
        result = _scope_creep_guard(["a.go"], ["b.go"], [], [])
        self.assertGreaterEqual(len(result["question_extra_edits"]), 1)

    def test_stable_touched_present(self):
        result = _scope_creep_guard(["a.go"], [], [{"file": "s.go"}], [])
        self.assertGreaterEqual(len(result["stable_anchors_touched"]), 1)

    def test_instruction_present(self):
        result = _scope_creep_guard(["a.go"], ["b.go"], [], [])
        self.assertIn("instruction", result)


class TestVerificationConfidence(unittest.TestCase):

    def test_low_when_no_candidates(self):
        result = _verification_confidence(["a.go"], [], None, [])
        self.assertEqual(result["level"], "low")
        self.assertIn("no structural", str(result["reasons"]))

    def test_high_when_strong_mirror(self):
        fv = [FileVocab("tests/test_a.go", {"test": 1}, "py")]
        result = _verification_confidence(["src/a.go"], ["tests/test_a.go"],
                                          {"mirror_ratio": 0.8}, fv)
        self.assertEqual(result["level"], "high")

    def test_mixed_when_thin_mirror(self):
        fv = [FileVocab("tests/test_a.go", {"test": 1}, "py")]
        result = _verification_confidence(["src/a.go"], ["tests/test_a.go"],
                                          {"mirror_ratio": 0.4}, fv)
        self.assertEqual(result["level"], "mixed")

    def test_low_when_candidates_not_in_scan(self):
        fv = []
        result = _verification_confidence(["src/a.go"], ["tests/test_a.go"],
                                          {"mirror_ratio": 0.8}, fv)
        self.assertEqual(result["level"], "low")


class TestVerificationDesertScore(unittest.TestCase):

    def test_empty_candidates_is_desert(self):
        score = _verification_desert_score("src/workers/parse.py", [], set())
        self.assertGreaterEqual(score, 0.8)

    def test_good_candidates_low_desert(self):
        score = _verification_desert_score("src/workers/parse.py",
                                           ["tests/test_workers/test_parse.py"],
                                           {"tests/test_workers"})
        self.assertLess(score, 0.5)

    def test_missing_test_dir_penalty(self):
        score_no_dir = _verification_desert_score("src/workers/parse.py", [], set())
        score_with_dir = _verification_desert_score("src/workers/parse.py", [],
                                                    {"tests/test_workers"})
        self.assertGreater(score_no_dir, score_with_dir)

    def test_desert_reason_high(self):
        reason = _verification_desert_reason(0.9, [], set(), "src/a.go")
        self.assertIn("test", reason.lower())

    def test_desert_reason_low(self):
        reason = _verification_desert_reason(0.1, ["tests/test_a.go"], {"tests"}, "src/a.go")
        self.assertIn("candidate", reason.lower())


class TestLatticeConfidence(unittest.TestCase):

    def test_high_when_no_defects(self):
        result = _lattice_confidence({}, ["a.go"])
        self.assertIn("high", result)

    def test_low_when_many_defects(self):
        result = _lattice_confidence({"vacancies": ["a.go"], "interstitials": ["b.go"]}, ["a.go", "b.go"])
        self.assertIn("mixed", result)

    def test_moderate_when_some_defects(self):
        result = _lattice_confidence({"vacancies": ["b.go"]}, ["a.go"])
        self.assertIn("mixed", result)


class TestPatternsConfidence(unittest.TestCase):

    def test_high_when_no_patterns(self):
        result = _patterns_confidence([], ["a.go"])
        self.assertIn("none", result.lower())

    def test_low_when_defect_pattern(self):
        result = _patterns_confidence([{"type": "defect", "pattern": "X"}], ["a.go"])
        self.assertIn("low", result.lower())

    def test_refactor_pattern(self):
        result = _patterns_confidence([{"type": "refactor", "pattern": "X"}], ["a.go"])
        self.assertIn("low", result.lower())


class TestStructuralInformationScore(unittest.TestCase):

    def test_rare_phrase_gets_high_score(self):
        score = _structural_information_score(2, 100, 5)
        self.assertGreater(score, 0.5)

    def test_common_phrase_gets_zero(self):
        score = _structural_information_score(80, 100, 1)
        self.assertEqual(score, 0.0)

    def test_single_file_zero(self):
        score = _structural_information_score(0, 100, 1)
        self.assertEqual(score, 0.0)

    def test_cross_language_bonus(self):
        mono = _structural_information_score(10, 100, 1)
        multi = _structural_information_score(10, 100, 3)
        self.assertGreater(multi, mono)


class TestActionableIdentifier(unittest.TestCase):

    def test_short_names_filtered(self):
        self.assertFalse(_is_actionable_identifier("if"))
        self.assertFalse(_is_actionable_identifier("ok"))

    def test_long_meaningful_names_kept(self):
        self.assertTrue(_is_actionable_identifier("IngestHandler"))
        self.assertTrue(_is_actionable_identifier("SpoolManager"))

    def test_empty_filtered(self):
        self.assertFalse(_is_actionable_identifier(""))

    def test_secret_shaped_filtered(self):
        self.assertFalse(_is_actionable_identifier("AIzaSyABC123DEF456GHI789JKL012"))


class TestTaskFileRole(unittest.TestCase):

    def test_test_files_identified(self):
        self.assertEqual(_task_file_role("tests/test_spool.ts"), "test")

    def test_source_files(self):
        self.assertEqual(_task_file_role("src/spool.ts"), "source")

    def test_scripts(self):
        self.assertEqual(_task_file_role("scripts/build.sh"), "script")

    def test_config_as_source(self):
        self.assertEqual(_task_file_role("config.yaml"), "source")

    def test_docs_as_source(self):
        self.assertEqual(_task_file_role("README.md"), "source")

    def test_examples(self):
        self.assertEqual(_task_file_role("examples/hello.py"), "example")


class TestTaskRoleRank(unittest.TestCase):

    def test_source_ranks_highest(self):
        source = _task_role_rank("source")
        test = _task_role_rank("test")
        self.assertLess(source, test)

    def test_script_mid(self):
        script = _task_role_rank("script")
        source = _task_role_rank("source")
        example = _task_role_rank("example")
        self.assertGreater(script, source)
        self.assertLess(script, example)

    def test_example_ranks_above_test(self):
        example = _task_role_rank("example")
        test = _task_role_rank("test")
        self.assertLess(example, test)

    def test_unknown_returns_lowest(self):
        self.assertEqual(_task_role_rank("unknown"), 4)


class TestCheckHubRisk(unittest.TestCase):

    def test_no_analysis_returns_empty(self):
        self.assertEqual(_check_hub_risk("path", ["a.ts"], None), [])

    def test_no_changed_returns_empty(self):
        mock = MagicMock()
        mock.file_vocabs = []
        self.assertEqual(_check_hub_risk("path", [], mock), [])

    def test_empty_file_vocabs(self):
        mock = MagicMock()
        mock.file_vocabs = []
        self.assertEqual(_check_hub_risk("path", ["a.ts"], mock), [])


class TestCheckCloneFlag(unittest.TestCase):

    def test_no_analysis_returns_empty(self):
        self.assertEqual(_check_clone_flag("path", ["a.ts"], None, "HEAD"), [])

    def test_no_changed_returns_empty(self):
        mock = MagicMock()
        mock.file_vocabs = []
        self.assertEqual(_check_clone_flag("path", [], mock, "HEAD"), [])


class TestReviewSummaryPure(unittest.TestCase):

    def test_review_summary_required_fields(self):
        from quale.reports import review_summary
        import tempfile, os
        tmp = tempfile.TemporaryDirectory()
        repo = os.path.join(tmp.name, "repo")
        os.makedirs(os.path.join(repo, "src"))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with open(os.path.join(repo, "src", "a.ts"), "w") as f:
            f.write("export const A = 1;\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        with open(os.path.join(repo, "src", "a.ts"), "w") as f:
            f.write("export const A = 2;\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t.test", "commit", "-q", "-m", "change"], cwd=repo, check=True)
        result = review_summary(path=repo, base_ref="HEAD~1", head_ref="HEAD")
        self.assertIn("review", result)
        self.assertIn("changed_files", result)
        self.assertIn("blast_radius_count", result)


class TestCiGateCodes(unittest.TestCase):

    def test_gate_codes_are_unique(self):
        from quale.cli import GATE_CODES
        codes = list(GATE_CODES.values())
        self.assertEqual(len(codes), len(set(codes)))
