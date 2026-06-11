"""yamlite — a deterministic emitter/parser for a strict YAML subset.

Why this exists: the competency-question gold artifact (whitepaper §17.4 Tier-2)
is specified as YAML, but PyYAML is not in the approved dependency set (§18 hard
rules: no new dependencies — implement the primitive yourself). This module
emits and parses a *restricted, well-specified* YAML subset:

- block-style mappings and sequences, 2-space indentation
- keys are bare identifiers matching [A-Za-z_][A-Za-z0-9_-]*
- scalar values are JSON-encoded (strings always double-quoted with JSON
  escaping; ints/floats/true/false/null as JSON literals); inline ``[]``/``{}``
  for empty containers
- sequence items are ``- `` prefixed; a mapping item may start inline on the
  ``- `` line (standard YAML block style)

Everything this module emits is valid YAML 1.2 readable by any conforming YAML
parser (JSON scalars are a YAML subset); the parser here accepts exactly the
emitted dialect, which is all the estate fixtures use. Deterministic: emission
preserves dict insertion order, no timestamps, '\n' newlines.
"""

from __future__ import annotations

import json
import re
from typing import Any

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# --------------------------------------------------------------------- emit


def _emit_scalar(v: Any) -> str:
    if isinstance(v, (dict, list)):
        raise TypeError("not a scalar")
    return json.dumps(v, ensure_ascii=False)


def _is_scalar(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def _emit_block(obj: Any, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            raise ValueError("empty dict must be emitted inline by caller")
        for k, v in obj.items():
            if not isinstance(k, str) or not _KEY_RE.match(k):
                raise ValueError(f"unsupported key: {k!r}")
            if _is_scalar(v):
                lines.append(f"{pad}{k}: {_emit_scalar(v)}")
            elif not v:  # empty dict/list inline
                lines.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                lines.append(f"{pad}{k}:")
                _emit_block(v, indent + 1, lines)
    elif isinstance(obj, list):
        for item in obj:
            if _is_scalar(item):
                lines.append(f"{pad}- {_emit_scalar(item)}")
            elif not item:
                lines.append(f"{pad}- {'{}' if isinstance(item, dict) else '[]'}")
            elif isinstance(item, dict):
                first = True
                for k, v in item.items():
                    if not isinstance(k, str) or not _KEY_RE.match(k):
                        raise ValueError(f"unsupported key: {k!r}")
                    prefix = f"{pad}- " if first else f"{pad}  "
                    first = False
                    if _is_scalar(v):
                        lines.append(f"{prefix}{k}: {_emit_scalar(v)}")
                    elif not v:
                        lines.append(f"{prefix}{k}: {'{}' if isinstance(v, dict) else '[]'}")
                    else:
                        lines.append(f"{prefix}{k}:")
                        _emit_block(v, indent + 2, lines)
            else:  # nested list inside list
                lines.append(f"{pad}-")
                _emit_block(item, indent + 1, lines)
    else:
        raise TypeError(f"top-level must be dict or list, got {type(obj)}")


def dumps(obj: Any) -> str:
    lines: list[str] = []
    _emit_block(obj, 0, lines)
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- parse


class _Lines:
    """Logical (non-blank, non-comment) lines with indentation depth."""

    def __init__(self, text: str) -> None:
        self.items: list[tuple[int, str]] = []
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            n_spaces = len(raw) - len(raw.lstrip(" "))
            if n_spaces % 2 != 0:
                raise ValueError(f"odd indentation in yamlite input: {raw!r}")
            self.items.append((n_spaces // 2, raw.lstrip(" ")))
        self.pos = 0

    def peek(self) -> tuple[int, str] | None:
        return self.items[self.pos] if self.pos < len(self.items) else None

    def next(self) -> tuple[int, str]:
        item = self.items[self.pos]
        self.pos += 1
        return item


def _parse_scalar(token: str) -> Any:
    return json.loads(token)


def _split_kv(content: str) -> tuple[str, str | None]:
    """Split 'key: value' / 'key:' — key is a bare identifier, so the first
    ': ' (or trailing ':') after the key terminates it."""
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?: (.*))?$", content)
    if not m:
        raise ValueError(f"cannot parse mapping line: {content!r}")
    return m.group(1), m.group(2)


def _parse_block(lines: _Lines, indent: int) -> Any:
    head = lines.peek()
    if head is None:
        raise ValueError("unexpected end of input")
    if head[1].startswith("- ") or head[1] == "-":
        return _parse_seq(lines, indent)
    return _parse_map(lines, indent)


def _parse_map(lines: _Lines, indent: int) -> dict:
    out: dict[str, Any] = {}
    while True:
        head = lines.peek()
        if head is None or head[0] < indent:
            return out
        if head[0] > indent:
            raise ValueError(f"bad indentation at {head[1]!r}")
        if head[1].startswith("- "):
            return out  # sequence at same level belongs to parent
        lines.next()
        key, val = _split_kv(head[1])
        if val is None:
            nxt = lines.peek()
            if nxt is None or nxt[0] <= indent:
                raise ValueError(f"key {key!r} has no value")
            out[key] = _parse_block(lines, indent + 1)
        else:
            out[key] = _parse_scalar(val)


def _parse_seq(lines: _Lines, indent: int) -> list:
    out: list[Any] = []
    while True:
        head = lines.peek()
        if head is None or head[0] < indent or not (head[1].startswith("- ") or head[1] == "-"):
            return out
        if head[0] > indent:
            raise ValueError(f"bad indentation at {head[1]!r}")
        lines.next()
        rest = head[1][2:] if head[1].startswith("- ") else ""
        if not rest:
            out.append(_parse_block(lines, indent + 1))
            continue
        # inline mapping start on the '- ' line?
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:( |$)", rest):
            key, val = _split_kv(rest)
            item: dict[str, Any] = {}
            if val is None:
                item[key] = _parse_block(lines, indent + 2)
            else:
                item[key] = _parse_scalar(val)
            # continuation keys are indented one deeper than the dash column
            # (emitted at indent+1 in 2-space units)
            while True:
                nxt = lines.peek()
                if nxt is None or nxt[0] != indent + 1 or nxt[1].startswith("- "):
                    break
                lines.next()
                k2, v2 = _split_kv(nxt[1])
                if v2 is None:
                    item[k2] = _parse_block(lines, indent + 2)
                else:
                    item[k2] = _parse_scalar(v2)
            out.append(item)
        else:
            out.append(_parse_scalar(rest))


def loads(text: str) -> Any:
    lines = _Lines(text)
    if lines.peek() is None:
        return {}
    result = _parse_block(lines, 0)
    if lines.peek() is not None:
        raise ValueError(f"trailing content: {lines.peek()!r}")
    return result
