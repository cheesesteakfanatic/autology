"""decipher(obj, keymap, key) — one-click decode of the engine output (v2.1 §7).

OPEN-SHELL (docs/IP_ARCHITECTURE.md). The return trip: the engine computed over
the ANONYMIZED tables and handed back tokens; on the CUSTOMER machine we open the
encrypted keymap with the customer key and substitute every token back to its raw
value. Works on a scalar, a list/dict tree, an OQIR :class:`Answer` (rows + cited
cells), or any nested structure — tokens are replaced WHEREVER they appear, raw
literals are left untouched.

Decipher is the ONLY component that ever reconstructs raw values, and it can only
do so with BOTH the encrypted keymap AND the key — which only the customer holds.
"""

from __future__ import annotations

import copy
from typing import Any

from .keymap import EncryptedKeyMap, KeyMap, decrypt_keymap

__all__ = ["decipher", "decipher_value"]


def _open(keymap: Any, key: bytes) -> KeyMap:
    """Accept a sealed keymap (+ key), bytes, or an already-open KeyMap."""
    if isinstance(keymap, KeyMap):
        return keymap
    if isinstance(keymap, (EncryptedKeyMap, bytes, bytearray)):
        return decrypt_keymap(
            keymap if isinstance(keymap, EncryptedKeyMap) else EncryptedKeyMap(bytes(keymap)),
            key,
        )
    raise TypeError(f"unsupported keymap type: {type(keymap)!r}")


def decipher_value(value: Any, mapping: dict[str, str]) -> Any:
    """Replace a single token with its raw value; pass through non-tokens.

    Match is on the displayed string form, so an integer/float numeric token is
    deciphered the same as a string token (the keymap is keyed by display form)."""
    if value is None:
        return None
    if isinstance(value, str):
        return mapping.get(value, value)
    # numeric / date tokens were stored in the keymap by their display string
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        s = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        return mapping.get(s, value)
    return value


def _walk(obj: Any, mapping: dict[str, str]) -> Any:
    """Deep-substitute tokens in an arbitrary list/dict/tuple tree."""
    if isinstance(obj, dict):
        return {k: _walk(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, mapping) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_walk(v, mapping) for v in obj)
    return decipher_value(obj, mapping)


def _decipher_answer(answer: Any, mapping: dict[str, str]) -> Any:
    """Decipher an OQIR-style Answer: rows + cited cells, in place on a copy.

    Duck-typed (``rows`` + ``citations``) so we do not import the closed-core OQIR
    contract — the open-shell toolkit stays decoupled from the engine internals."""
    out = copy.copy(answer)
    if getattr(out, "rows", None) is not None:
        out.rows = [[decipher_value(c, mapping) for c in row] for row in out.rows]
    cites = getattr(out, "citations", None)
    if cites is not None:
        new_cites = []
        for cell in cites:
            # CitedCell is frozen; rebuild it with the deciphered value if needed.
            val = getattr(cell, "value", None)
            deciphered = decipher_value(val, mapping)
            if deciphered is val or deciphered == val:
                new_cites.append(cell)
            else:
                new_cites.append(_replace_value(cell, deciphered))
        out.citations = new_cites
    return out


def _replace_value(cell: Any, value: Any) -> Any:
    """Rebuild a (possibly frozen) cited-cell dataclass with a new ``value``."""
    import dataclasses

    if dataclasses.is_dataclass(cell) and not isinstance(cell, type):
        try:
            return dataclasses.replace(cell, value=value)
        except (TypeError, ValueError):
            pass
    return cell


def decipher(obj: Any, keymap: Any, key: bytes = b"") -> Any:
    """Decode engine output by substituting every token back to its raw value.

    Parameters
    ----------
    obj : the engine result — a scalar, list/dict tree, or an Answer-like object
        with ``rows`` / ``citations``.
    keymap : the :class:`~ontoforge.anonymizer.keymap.EncryptedKeyMap` (bytes also
        accepted) or an already-open :class:`KeyMap`.
    key : the customer key (required unless ``keymap`` is already an open KeyMap).

    Returns a deciphered COPY; the input is never mutated. Tokens with no entry in
    the keymap are left as-is (they were never sensitive)."""
    km = _open(keymap, key)
    mapping = km.mapping
    # Answer-like (has rows or citations) → typed path
    if hasattr(obj, "rows") or hasattr(obj, "citations"):
        return _decipher_answer(obj, mapping)
    return _walk(obj, mapping)
