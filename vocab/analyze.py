"""Cross-document co-occurrence matrix — concept relationship discovery."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class CoOccurrenceMatrix:
    """Tracks how often pairs of phrases co-occur in the same file."""
    pairs: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    phrase_count: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total_docs: int = 0

    def add_file(self, phrases: set[str]):
        """Add a file's phrase set to the matrix."""
        self.total_docs += 1
        for p in phrases:
            self.phrase_count[p] += 1
        for a in phrases:
            for b in phrases:
                if a < b:
                    self.pairs[(a, b)] += 1

    def pmi(self, a: str, b: str) -> float:
        """Pointwise mutual information: log2(P(a,b) / P(a)P(b)).
        
        Positive PMI = co-occur more than expected by chance.
        PMI = 0 = independent.
        Negative PMI = co-occur less than expected (avoidance).
        """
        if self.total_docs == 0:
            return 0.0
        p_a = self.phrase_count.get(a, 0) / self.total_docs
        p_b = self.phrase_count.get(b, 0) / self.total_docs
        p_ab = self.pairs.get((a, b) if a < b else (b, a), 0) / self.total_docs
        if p_a == 0 or p_b == 0 or p_ab == 0:
            return 0.0
        return math.log2(p_ab / (p_a * p_b))

    def top_pmi_for(self, phrase: str, limit: int = 10, min_freq: int = 3) -> list[tuple[str, float]]:
        """Top PMI co-occurrences for a given phrase.
        
        min_freq: minimum times a partner must appear to be considered
        (filters out single-file artifacts with inflated PMI).
        """
        candidates: set[str] = set()
        for (a, b) in self.pairs:
            if a == phrase:
                candidates.add(b)
            elif b == phrase:
                candidates.add(a)
        scored = [(c, self.pmi(phrase, c)) for c in candidates
                  if self.phrase_count.get(c, 0) >= min_freq]
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    def cluster(self, min_cooccurrence: int = 3, min_phrases: int = 2) -> list[list[str]]:
        """Extract co-occurrence clusters — groups of phrases that frequently appear together."""
        clusters: list[set[str]] = []
        seen: set[str] = set()
        sorted_pairs = sorted(self.pairs.items(), key=lambda x: -x[1])
        for (a, b), count in sorted_pairs:
            if count < min_cooccurrence:
                break
            if a in seen or b in seen:
                merged = False
                for cluster in clusters:
                    if a in cluster or b in cluster:
                        cluster.add(a)
                        cluster.add(b)
                        merged = True
                        break
                if not merged:
                    clusters.append({a, b})
            else:
                clusters.append({a, b})
            seen.add(a)
            seen.add(b)
        result = [sorted(c) for c in clusters if len(c) >= min_phrases]
        return sorted(result, key=lambda x: -len(x))


@dataclass
class FileVocab:
    path: str
    vocabulary: dict[str, int]  # phrase -> frequency
    language: str
    total_phrases: int = 0


def classify_language(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    lang_map = {
        "go": "Go", "ts": "TypeScript", "tsx": "TypeScript", "js": "JavaScript",
        "jsx": "JavaScript", "py": "Python", "rs": "Rust", "rb": "Ruby",
        "java": "Java", "kt": "Kotlin", "swift": "Swift", "c": "C", "h": "C",
        "cpp": "C++", "cc": "C++", "hpp": "C++", "hxx": "C++", "cxx": "C++",
        "cs": "C#", "php": "PHP", "sql": "SQL", "yaml": "YAML", "yml": "YAML",
        "json": "JSON", "xml": "XML", "md": "Markdown", "toml": "TOML",
        "dockerfile": "Dockerfile", "sh": "Shell", "bash": "Shell",
        "zsh": "Shell", "fish": "Shell", "proto": "Protobuf",
        "html": "HTML", "css": "CSS", "scss": "SCSS",
        # Weird / niche languages
        "nix": "Nix", "ml": "OCaml", "mli": "OCaml",
        "erl": "Erlang", "hrl": "Erlang",
        "ex": "Elixir", "exs": "Elixir",
        "eex": "Elixir", "heex": "Elixir",
        "zig": "Zig",
        "hs": "Haskell", "lhs": "Haskell",
        "clj": "Clojure", "cljs": "Clojure", "cljc": "Clojure",
        "sml": "SML", "fs": "F#", "fsx": "F#",
        "r": "R", "jl": "Julia", "scala": "Scala",
    }
    if path.endswith("Dockerfile") or path.endswith("dockerfile"):
        return "Dockerfile"
    return lang_map.get(ext, "Unknown")


def compute_uniqueness(file_vocab: FileVocab, all_vocabs: list[FileVocab]) -> float:
    """What fraction of this file's phrases are unique to it within the codebase?"""
    all_phrases: set[str] = set()
    other_phrases: set[str] = set()
    for fv in all_vocabs:
        phrases = set(fv.vocabulary.keys())
        if fv.path == file_vocab.path:
            my_phrases = phrases
        else:
            other_phrases |= phrases
        all_phrases |= phrases
    unique = my_phrases - other_phrases
    if not my_phrases:
        return 0.0
    return len(unique) / len(my_phrases)
