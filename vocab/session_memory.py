"""Session memory — co-occurrence matrix over agent behavioral events.

An agent's own tool events (reads, edits, errors, searches) form a
co-occurrence structure. This module builds a matrix from the event
stream for associative recall: "what co-occurs with spool.ts?"

Not for: semantic search, full-text retrieval, content storage.
"""

from __future__ import annotations

import json
import gzip
import re
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterator

from vocab.analyze import CoOccurrenceMatrix


_TOOL_CLASS = {
    "read_file": ("read",),
    "read": ("read",),
    "file_edit": ("edit",),
    "edit": ("edit",),
    "grep": ("search", "grep"),
    "glob": ("search", "glob"),
    "search": ("search",),
    "error": ("error",),
    "tool_error": ("error",),
    "bash": ("exec", "shell"),
    "exec": ("exec",),
    "write": ("write",),
    "write_file": ("write",),
    "think": ("think",),
}

_PATH_DELIMITERS = re.compile(r"[/\\\._\-\:]")
_IDENTIFIER = re.compile(r"\b[A-Z][A-Za-z0-9_]{3,40}\b")


def extract_tokens(*, tool: str = "", file_path: str = "",
                   error_code: str = "", raw: str = "",
                   identifiers: list[str] | None = None) -> list[str]:
    tokens: list[str] = []
    tokens.extend(_TOOL_CLASS.get(tool, [tool]))
    if file_path:
        for part in _PATH_DELIMITERS.split(file_path):
            if len(part) >= 3:
                tokens.append(part)
    if error_code and len(error_code) >= 2:
        tokens.append(error_code)
    if identifiers:
        for ident in identifiers:
            if len(ident) >= 3:
                tokens.append(ident[:40])
    if raw:
        for match in _IDENTIFIER.finditer(raw):
            ident = match.group()
            if len(ident) >= 3:
                tokens.append(ident[:40])
    return tokens


@dataclass
class SessionEvent:
    id: int
    timestamp: float
    tool: str = ""
    file_path: str = ""
    error_code: str = ""
    tokens: list[str] = field(default_factory=list)


@dataclass
class RecallResult:
    concept: str
    associations: dict[str, int] = field(default_factory=dict)
    total_events: int = 0
    recency: float = 0.0


class SessionMemory:
    """Co-occurrence matrix over agent session events for associative recall.

    Queries are O(1) dict lookups. Eviction is FIFO when max_events exceeded.
    """

    def __init__(self, max_events: int = 5000):
        self.max_events = max_events
        self.matrix = CoOccurrenceMatrix()
        self.events: list[SessionEvent] = []
        self._next_id = 0
        self._event_token_counts: list[Counter[str, int]] = []
        self._token_event_map: dict[str, list[int]] = defaultdict(list)
        self._token_assoc: dict[str, dict[str, int]] = defaultdict(dict)

    def _add_token_assoc(self, token_set: set[str]) -> None:
        for a in token_set:
            for b in token_set:
                if a < b:
                    self._token_assoc[a][b] = self._token_assoc[a].get(b, 0) + 1
                    self._token_assoc[b][a] = self._token_assoc[b].get(a, 0) + 1

    def _remove_token_assoc(self, token_set: set[str]) -> None:
        for a in token_set:
            for b in token_set:
                if a < b:
                    for x in (a, b):
                        d = self._token_assoc.get(x, {})
                        y = b if x == a else a
                        if y in d:
                            d[y] -= 1
                            if d[y] <= 0:
                                del d[y]

    def ingest(self, *, tool: str = "", file_path: str = "",
               error_code: str = "", raw: str = "",
               identifiers: list[str] | None = None,
               timestamp: float | None = None) -> int:
        tokens = extract_tokens(tool=tool, file_path=file_path,
                                error_code=error_code, raw=raw,
                                identifiers=identifiers)
        token_set = set(tokens)
        event = SessionEvent(
            id=self._next_id,
            timestamp=timestamp or datetime.now(timezone.utc).timestamp(),
            tool=tool,
            file_path=file_path,
            error_code=error_code,
            tokens=tokens,
        )
        self.events.append(event)
        self.matrix.add_file(token_set)
        self._event_token_counts.append(Counter(tokens))
        self._add_token_assoc(token_set)
        for tok in token_set:
            self._token_event_map[tok].append(event.id)
        self._next_id += 1

        if len(self.events) > self.max_events:
            self._evict_oldest()
        return event.id

    def _evict_oldest(self) -> None:
        old = self.events.pop(0)
        old_count = self._event_token_counts.pop(0)
        token_set = set(old.tokens)
        for a in token_set:
            self.matrix.phrase_count[a] -= old_count[a]
            if self.matrix.phrase_count[a] <= 0:
                del self.matrix.phrase_count[a]
                self._token_event_map.pop(a, None)
        self._remove_token_assoc(token_set)
        for tok in token_set:
            lst = self._token_event_map.get(tok, [])
            if lst and lst[0] == old.id:
                lst.pop(0)

    def query(self, concept: str) -> RecallResult | None:
        if concept not in self._token_assoc:
            return None
        sorted_assoc = dict(sorted(
            self._token_assoc[concept].items(), key=lambda x: -x[1]
        ))
        event_ids = self._token_event_map.get(concept, [])
        recency = 1.0
        if event_ids and self.events:
            recency = event_ids[-1] / max(self.events[-1].id, 1)
        return RecallResult(
            concept=concept,
            associations=sorted_assoc,
            total_events=len(self.events),
            recency=round(recency, 3),
        )

    def status(self) -> dict:
        return {
            "events": len(self.events),
            "max_events": self.max_events,
            "unique_tokens": len(self.matrix.phrase_count),
            "pairs": len(self.matrix.pairs),
        }

    def save(self, path: str) -> None:
        obj = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "max_events": self.max_events,
            "event_count": len(self.events),
            "matrix": dict(self.matrix.phrase_count),
            "pairs": {f"{a}||{b}": c for (a, b), c in self.matrix.pairs.items()},
            "events": [
                {"id": e.id, "t": e.timestamp, "tool": e.tool,
                 "file": e.file_path, "code": e.error_code}
                for e in self.events
            ],
            "token_event_map": {k: v for k, v in self._token_event_map.items()},
            "token_assoc": self._token_assoc,
        }
        payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        with gzip.open(path, "wb") as f:
            f.write(payload)

    @classmethod
    def load(cls, path: str) -> SessionMemory:
        if not os.path.isfile(path):
            return cls()
        try:
            with gzip.open(path, "rb") as f:
                obj = json.loads(f.read())
        except Exception:
            return cls()
        mem = cls(max_events=obj.get("max_events", 5000))
        mem.matrix.phrase_count.update(obj.get("matrix", {}))
        for key, count in obj.get("pairs", {}).items():
            a, b = key.split("||", 1)
            mem.matrix.pairs[(a, b)] = count
        mem.events = [
            SessionEvent(id=e["id"], timestamp=e["t"],
                         tool=e.get("tool", ""),
                         file_path=e.get("file", ""),
                         error_code=e.get("code", ""))
            for e in obj.get("events", [])
        ]
        mem._next_id = (mem.events[-1].id + 1) if mem.events else 0
        mem._event_token_counts = [Counter() for _ in mem.events]
        mem._token_event_map.update(obj.get("token_event_map", {}))
        mem._token_assoc.update(obj.get("token_assoc", {}))
        return mem


def run_daemon(save_path: str = "", max_events: int = 5000) -> None:
    """Persistent process: reads JSON-line commands from stdin, writes JSON to stdout.
    
    Commands (one per line):
      {"action":"ingest","tool":"...","file_path":"...","error_code":"...","raw":"..."}
      {"action":"query","concept":"..."}
      {"action":"status"}
      {"action":"save"}
    
    Responses (one per line):
      {"ok":true,"event_id":42,"total_events":8}
      {"concept":"spool","associations":{...},"total_events":8,"recency":0.5}
      {"events":8,"unique_tokens":12}
      {"ok":true,"saved":"path"}
    
    Exit: send {"action":"exit"} or close stdin.
    Uses sys.stdin.read() line-by-line — no binary framing needed.
    """
    import sys
    
    mem = SessionMemory(max_events=max_events)
    if save_path and os.path.isfile(save_path):
        try:
            mem = SessionMemory.load(save_path)
        except Exception:
            mem = SessionMemory(max_events=max_events)
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"error": "invalid JSON"}) + "\n")
            sys.stdout.flush()
            continue
        
        action = cmd.get("action", "")
        
        if action == "exit":
            break
        
        if action == "ingest":
            eid = mem.ingest(
                tool=cmd.get("tool", ""),
                file_path=cmd.get("file_path", ""),
                error_code=cmd.get("error_code", ""),
                raw=cmd.get("raw", ""),
                identifiers=cmd.get("identifiers"),
            )
            if save_path:
                try:
                    mem.save(save_path)
                except Exception:
                    pass
            sys.stdout.write(json.dumps({
                "ok": True, "event_id": eid, "total_events": len(mem.events)
            }) + "\n")
            sys.stdout.flush()
        
        elif action == "query":
            concept = cmd.get("concept", "")
            if not concept:
                sys.stdout.write(json.dumps({"error": "missing concept"}) + "\n")
                sys.stdout.flush()
                continue
            result = mem.query(concept)
            if result:
                sys.stdout.write(json.dumps({
                    "concept": result.concept,
                    "associations": result.associations,
                    "total_events": result.total_events,
                    "recency": result.recency,
                }) + "\n")
            else:
                sys.stdout.write(json.dumps({
                    "concept": concept,
                    "associations": {},
                    "total_events": len(mem.events),
                    "recency": 0,
                }) + "\n")
            sys.stdout.flush()
        
        elif action == "status":
            sys.stdout.write(json.dumps(mem.status()) + "\n")
            sys.stdout.flush()
        
        elif action == "save":
            if save_path:
                mem.save(save_path)
                sys.stdout.write(json.dumps({"ok": True, "saved": save_path}) + "\n")
            else:
                sys.stdout.write(json.dumps({"error": "no save path"}) + "\n")
            sys.stdout.flush()
        
        else:
            sys.stdout.write(json.dumps({"error": f"unknown action: {action}"}) + "\n")
            sys.stdout.flush()
