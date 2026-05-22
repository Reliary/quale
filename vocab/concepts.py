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
from dataclasses import dataclass, field

# ── Patterns ──
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$')
_EXPORTED = re.compile(r'^[A-Z][A-Za-z0-9_]*$')
_ALLCAPS = re.compile(r'^[A-Z][A-Z0-9_]{2,}$')
_IMPORT_PATH = re.compile(r'^[a-z][a-z0-9./_-]*/[A-Za-z][A-Za-z0-9_./-]*$')
_MODULE_PATH = re.compile(r'^[a-z][a-z0-9.-]+\.[a-z]{2,}/[A-Za-z]')
_ERROR_TYPE = re.compile(r'^(Err|Error|Invalid|NotFound|Unauthorized|Forbidden|BadRequest)[A-Za-z0-9]*$')
_ERR_PREFIX = re.compile(r'^(error|Error|err[^A-Z]|Err[^A-Z]|errors\.)', re.I)  # not all-caps ERRON
_ROUTE = re.compile(r'^/(api|v1|v2|health|auth|users|admin|settings)/')
_SQL_TABLE = re.compile(r'^[a-z][a-z0-9_]*$')

# C/C++ macros that look like exports but aren't
_C_MACROS = frozenset({
    "NULL", "TRUE", "FALSE", "EOF", "MIN", "MAX", "ABS", "STDIN", "STDOUT",
    "STDERR", "CHAR_BIT", "INT_MAX", "INT_MIN", "UINT_MAX", "SIZE_MAX",
    "ERRNO", "EINTR", "EAGAIN", "EWOULDBLOCK", "EACCES", "EEXIST",
    "EINVAL", "ENOENT", "ENOMEM", "EPERM", "EPIPE", "ERANGE",
})

# Common short exports that are usually noise (C functions, test helpers, etc.)
_NOISE_EXPORTS = frozenset({
    "Hello", "World", "Main", "Test", "Benchmark", "Example",
    "Handler", "Server", "Client", "Config", "Options",
    "Request", "Response", "Error", "Result", "Status",
})
_DUNDER = re.compile(r'^__[A-Za-z0-9_]+__$')

# Syntax tokens — exact match
_SYNTAX = frozenset({
    "}", ")", "]", "{", "(", "[", "});", "})", "]);", "},",
    "});", "}])", "}));", "}},", "}],",
    "</div>", "<div>", "</tr>", "<tr>", "</td>", "<td",
    "</a>", "<a", "</span>", "<span>", "</p>", "<p>",
    "</li>", "<li>", "</ul>", "<ul>", "</body>", "</html>",
    "<!DOCTYPE html>", "<head>", "</head>",
    "```", "---", "```bash", "```json", "```typescript",
    "```python", "```go", "```yaml", "```sql", "```shell",
    "};", "});", "});", "},", "),", ");",
    "return {", "return;", "return nil", "return err",
    "if err != nil {", "if err != nil", "if err != nil {",
    "err != nil {", "err != nil",
    "t.Parallel()", "defer", "go func() {",
    "import (", "package ", "func ", "type ", "const ",
    "#!/bin/bash", "#!/usr/bin/env bash", "echo \"\"", "fi", "done",
    '"dev": true,', '"license": "MIT",',
    '"dependencies": {', '"engines": {', '"scripts": {',
    "optional: true", "optional: false",
    # C/C++ preprocessor
    "#include", "#define", "#ifdef", "#ifndef", "#endif", "#else",
    "#if", "#pragma", "#error", "#warning", "#undef",
    "endif", "end", "ifdef", "ifndef", "else", "elif",
})

_SYNTAX_PREFIX = frozenset({
    "</", "<", "</td>", "</tr>", "</div>", "<td", "<tr", "<div",
    "```", "#!/",
})

# Code syntax markers — if a phrase contains any of these, it's code not a concept
_CODE_MARKERS = frozenset({";", "(", ")", "{", "}", "[", "]", "*", "&", "->", "=>", "==", "+=", "-=", "/=", "!="})


@dataclass
class ConceptGroup:
    exported: list[tuple[str, int]] = field(default_factory=list)
    identifier: list[tuple[str, int]] = field(default_factory=list)
    import_path: list[tuple[str, int]] = field(default_factory=list)
    error: list[tuple[str, int]] = field(default_factory=list)
    api: list[tuple[str, int]] = field(default_factory=list)
    config: list[tuple[str, int]] = field(default_factory=list)
    db: list[tuple[str, int]] = field(default_factory=list)
    syntax: list[tuple[str, int]] = field(default_factory=list)
    other: list[tuple[str, int]] = field(default_factory=list)


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
    "auto", "extern", "register", "static", "volatile",
    "constexpr", "virtual", "override", "inline", "template",
    "typename", "namespace", "using", "friend", "mutable",
    "explicit", "operator", "sizeof", "typedef", "union",
    "unsigned", "signed", "short", "double", "float", "char",
    "long", "size_t", "ssize_t", "ptrdiff_t", "wchar_t",
    "int8_t", "int16_t", "int32_t", "int64_t", "uint8_t", "uint16_t",
    "uint32_t", "uint64_t",
})


def _is_code_line(phrase: str) -> bool:
    """Detect if a phrase looks like source code rather than a concept name."""
    stripped = phrase.strip('"').strip("'")
    if any(m in stripped for m in _CODE_MARKERS) and len(stripped) >= 4:
        return True
    if stripped.startswith(("*", "&")):
        return True
    # Variable assignment: `x = value` (not config-key style `KEY=value`)
    if "=" in stripped and " " in stripped and not _ALLCAPS.match(stripped.split("=")[0].strip()):
        return True
    return False


def classify_phrase(phrase: str) -> str:
    """Classify a single phrase into a concept category."""
    clean = phrase.strip('"').strip("'")

    # ── Syntax exclusion passes ──
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
    if _DUNDER.match(phrase):
        return "syntax"
    if _ALLCAPS.match(clean) and len(clean) <= 5 and clean in _C_MACROS:
        return "syntax"
    if _is_code_line(phrase):
        return "syntax"

    # ── Error types (word boundary aware) ──
    if _ERROR_TYPE.match(clean):
        return "error"
    if _ERR_PREFIX.match(clean) and not _ALLCAPS.match(clean):
        return "error"

    # ── API routes ──
    if _ROUTE.match(clean):
        return "api"
    if clean.startswith("GET ") or clean.startswith("POST ") or clean.startswith("PUT ") or clean.startswith("DELETE "):
        return "api"
    if ".HandleFunc(" in clean or ".Handle(" in clean or "router." in clean:
        return "api"

    # ── Import paths ──
    if _MODULE_PATH.match(clean) and "/" in clean:
        return "import_path"
    if _IMPORT_PATH.match(clean) and "/" in clean:
        return "import_path"

    # ── Exports (reject all-caps C macros and common noise) ──
    if _EXPORTED.match(clean):
        if clean in _NOISE_EXPORTS:
            return "identifier"
        if _ALLCAPS.match(clean) and (len(clean) <= 6 or clean in _C_MACROS):
            return "identifier"
        return "exported"

    # ── Identifiers ──
    if _IDENTIFIER.match(clean):
        return "identifier"

    # ── Config / env (reject code lines with =) ──
    if clean.startswith("--") or clean.startswith("-"):
        return "config"
    if clean.startswith("_") and clean.endswith("_"):
        return "config"
    if "=" in clean and not clean.startswith('"') and not clean.startswith("'"):
        if _is_code_line(clean):
            return "syntax"
        return "config"

    # ── DB ──
    if _SQL_TABLE.match(clean) and "sql" in clean[:10]:
        return "db"
    if ".sql.go" in clean:
        return "db"
    if clean.startswith("migration_"):
        return "db"

    return "other"


def extract_concepts(phrase_counter: Counter) -> ConceptGroup:
    """Categorize all phrases into concept groups."""
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

        if (len(groups.exported) >= 50 and len(groups.identifier) >= 50
            and len(groups.error) >= 30 and len(groups.api) >= 20
            and len(groups.config) >= 20 and len(groups.import_path) >= 20):
            break

    for field_name in groups.__dataclass_fields__:
        items = getattr(groups, field_name, [])
        items.sort(key=lambda x: -x[1])

    return groups


def cluster_labels(phrases: list[str], max_label_phrases: int = 3) -> str:
    """Generate a human-readable label for a co-occurrence cluster."""
    exported = [p for p in phrases if _EXPORTED.match(p) and not _ALLCAPS.match(p)]
    imports = [p for p in phrases if _MODULE_PATH.match(p) or (_IMPORT_PATH.match(p) and "/" in p)]
    errors = [p for p in phrases if _ERROR_TYPE.match(p) or _ERR_PREFIX.match(p)]
    configs = [p for p in phrases if "=" in p and not p.startswith('"') and not _is_code_line(p)]
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
        sample = []
        for p in phrases:
            if p not in sample and len(p) >= 3 and len(p) <= 40:
                sample.append(p)
            if len(sample) >= max_label_phrases:
                break
        theme = f"Pattern ({', '.join(sample)})"

    return theme
