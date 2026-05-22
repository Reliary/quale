"""Concept extraction and categorization layer.

Sits on top of the vocabulary pipeline. Takes raw phrases and classifies them:
  - identifier: exported names, function calls, import paths
  - error: error types, error messages
  - config: config keys, env vars
  - api: routes, API methods
  - db: SQL tables, columns, migrations
  - syntax: structural tokens to filter out
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# Patterns to identify meaningful concepts vs syntax noise
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$')
_EXPORTED = re.compile(r'^[A-Z][A-Za-z0-9_]*$')  # starts uppercase
_IMPORT_PATH = re.compile(r'^[a-z][a-z0-9./_-]*/[A-Za-z][A-Za-z0-9_./-]*$')
_MODULE_PATH = re.compile(r'^[a-z][a-z0-9.-]+\.[a-z]{2,}/[A-Za-z]')
_ERROR_TYPE = re.compile(r'^(Err|Error|Invalid|NotFound|Unauthorized|Forbidden|BadRequest)[A-Za-z0-9]*$')
_ERROR_MESSAGE = re.compile(r'^".*error.*"$', re.I)
_ROUTE = re.compile(r'^/(api|v1|v2|health|auth|users|admin|settings)/')
_SQL_TABLE = re.compile(r'^[a-z][a-z0-9_]*$')  # snake_case

# Syntax patterns to exclude from "top concepts"
_SYNTAX = frozenset({
    # Brackets
    "}", ")", "]", "{", "(", "[", "});", "})", "]);", "},",
    "});", "}])", "}));", "}},", "}],",
    # HTML
    "</div>", "<div>", "</tr>", "<tr>", "</td>", "<td",
    "</a>", "<a", "</span>", "<span>", "</p>", "<p>",
    "</li>", "<li>", "</ul>", "<ul>", "</body>", "</html>",
    "<!DOCTYPE html>", "<head>", "</head>",
    # Markdown
    "```", "---", "```bash", "```json", "```typescript",
    "```python", "```go", "```yaml", "```sql", "```shell",
    # Code structure
    "};", "});", "});", "},", "),", ");", ");",
    "return {", "return;", "return nil", "return err",
    "if err != nil {", "if err != nil", "if err != nil {",
    "err != nil {", "err != nil",
    "t.Parallel()", "defer", "go func() {",
    "import (", "package ", "func ", "type ", "const ",
    # Common shell
    "#!/bin/bash", "#!/usr/bin/env bash", "echo \"\"", "fi", "done",
    # Common formatting
    '"dev": true,', '"license": "MIT",',
    '"dependencies": {', '"engines": {', '"scripts": {',
    "optional: true", "optional: false",
})

_SYNTAX_PREFIX = frozenset({
    "</", "<", "</td>", "</tr>", "</div>", "<td", "<tr", "<div",
    "```", "#!/",
})


@dataclass
class ConceptGroup:
    exported: list[tuple[str, int]] = None
    identifier: list[tuple[str, int]] = None
    import_path: list[tuple[str, int]] = None
    error: list[tuple[str, int]] = None
    api: list[tuple[str, int]] = None
    config: list[tuple[str, int]] = None
    db: list[tuple[str, int]] = None
    syntax: list[tuple[str, int]] = None
    other: list[tuple[str, int]] = None

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            if getattr(self, field) is None:
                setattr(self, field, [])


# Common programming keywords — not meaningful identifiers
_KEYWORDS = frozenset({
    "if", "else", "for", "while", "do", "switch", "case", "break",
    "continue", "return", "try", "catch", "finally", "throw",
    "import", "from", "export", "default", "extends", "implements",
    "interface", "type", "enum", "class", "function", "const",
    "let", "var", "new", "delete", "typeof", "instanceof",
    "void", "null", "undefined", "true", "false", "this",
    "super", "async", "await", "yield", "with", "in", "of",
    "package", "select", "range", "go", "defer", "chan", "map",
    "struct", "error", "string", "bool", "int", "int64",
    "float64", "byte", "rune", "nil", "true", "false",
    "and", "or", "not", "is", "None", "True", "False",
    "def", "raise", "lambda", "pass", "except", "finally",
    "elif", "self", "cls",
})


def classify_phrase(phrase: str) -> str:
    """Classify a single phrase into a concept category."""
    clean = phrase.strip('"').strip("'")

    # Syntax exclusion passes
    if phrase in _SYNTAX:
        return "syntax"
    if any(phrase.startswith(p) for p in _SYNTAX_PREFIX):
        return "syntax"
    if len(phrase) <= 1:
        return "syntax"
    if len(phrase) >= 80:
        return "syntax"
    if clean in _KEYWORDS:
        return "syntax"

    # Error types
    if _ERROR_TYPE.match(clean):
        return "error"
    if "error" in clean[:20].lower() or "err" == clean[:3]:
        return "error"

    # Routes
    if _ROUTE.match(clean):
        return "api"
    if clean.startswith("GET ") or clean.startswith("POST ") or clean.startswith("PUT ") or clean.startswith("DELETE "):
        return "api"
    if ".HandleFunc(" in clean or ".Handle(" in clean or "router." in clean:
        return "api"

    # Import paths
    if _MODULE_PATH.match(clean) and "/" in clean:
        return "import_path"
    if _IMPORT_PATH.match(clean) and "/" in clean:
        return "import_path"

    # Export
    if _EXPORTED.match(clean):
        return "exported"

    # Identifier
    if _IDENTIFIER.match(clean):
        return "identifier"

    # Config / env
    if clean.startswith("--") or clean.startswith("-"):
        return "config"
    if clean.startswith("_") and clean.endswith("_"):
        return "config"
    if "=" in clean and not clean.startswith('"') and not clean.startswith("'"):
        return "config"

    # DB
    if _SQL_TABLE.match(clean) and "sql" in clean[:10]:
        return "db"
    if ".sql.go" in clean:
        return "db"
    if clean.startswith("migration_"):
        return "db"

    return "other"


def extract_concepts(phrase_counter: Counter) -> ConceptGroup:
    """Categorize all phrases into concept groups. Takes full Counter, not pre-truncated list."""
    groups = ConceptGroup()

    for phrase, freq in phrase_counter.most_common():
        category = classify_phrase(phrase)
        entry = (phrase, freq)
        if category == "identifier":
            groups.identifier.append(entry)
        elif category == "exported":
            groups.exported.append(entry)
        elif category == "import_path":
            groups.import_path.append(entry)
        elif category == "error":
            groups.error.append(entry)
        elif category == "api":
            groups.api.append(entry)
        elif category == "config":
            groups.config.append(entry)
        elif category == "db":
            groups.db.append(entry)
        elif category == "syntax":
            groups.syntax.append(entry)
        else:
            groups.other.append(entry)
        # Limits per category — no need to process all 200K phrases
        if (len(groups.exported) >= 50 and len(groups.identifier) >= 50
            and len(groups.error) >= 30 and len(groups.api) >= 20
            and len(groups.config) >= 20 and len(groups.import_path) >= 20):
            break

    # Sort each group by frequency descending
    for field in groups.__dataclass_fields__:
        items = getattr(groups, field, [])
        items.sort(key=lambda x: -x[1])

    return groups


def cluster_labels(phrases: list[str], max_label_phrases: int = 3) -> str:
    """Generate a human-readable label for a co-occurrence cluster."""
    # Try to detect the theme from exported/types/imports
    exported = [p for p in phrases if _EXPORTED.match(p)]
    imports = [p for p in phrases if _MODULE_PATH.match(p) or (_IMPORT_PATH.match(p) and "/" in p)]
    errors = [p for p in phrases if _ERROR_TYPE.match(p) or "error" in p[:20].lower()]
    configs = [p for p in phrases if "=" in p and not p.startswith('"')]
    identifiers = [p for p in phrases if _IDENTIFIER.match(p)]

    theme = None
    if len(errors) >= 2:
        theme = f"Error handling ({', '.join(e[:25] for e in errors[:2])})"
    elif len(imports) >= 2:
        theme = f"Import group ({', '.join(i.split('/')[-1][:20] for i in imports[:2])})"
    elif len(exported) >= 2:
        theme = f"Exported API ({', '.join(e[:25] for e in exported[:2])})"
    elif len(configs) >= 2:
        theme = f"Config ({', '.join(c.split('=')[0][:15] for c in configs[:2])})"
    elif len(identifiers) >= max_label_phrases:
        theme = f"Pattern ({', '.join(i[:25] for i in identifiers[:max_label_phrases])})"

    if theme is None:
        # Fallback: take first few unique phrases
        sample = []
        for p in phrases:
            if p not in sample and len(p) >= 3 and len(p) <= 40:
                sample.append(p)
            if len(sample) >= max_label_phrases:
                break
        theme = f"Pattern ({', '.join(sample)})"

    return theme
