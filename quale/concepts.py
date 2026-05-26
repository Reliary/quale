"""Concept extraction — raw phrase collection, no classification.

Sits on top of the vocabulary pipeline. Takes raw phrases and returns
top-N phrases by frequency. No pattern-based classification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ConceptGroup:
    phrases: list[tuple[str, int]] = field(default_factory=list)


def extract_concepts(phrase_counter: Counter) -> ConceptGroup:
    """Collect top phrases by frequency — no classification."""
    groups = ConceptGroup()
    for phrase, freq in phrase_counter.most_common(200):
        groups.phrases.append((phrase, freq))
    return groups


def cluster_labels(phrases: list[str], max_label_phrases: int = 3) -> str:
    """Generate a human-readable label for a co-occurrence cluster."""
    sample = []
    for p in phrases:
        if p not in sample and len(p) >= 3 and len(p) <= 40:
            sample.append(p)
        if len(sample) >= max_label_phrases:
            break
    if sample:
        return f"Pattern ({', '.join(sample)})"
    return "Pattern"
