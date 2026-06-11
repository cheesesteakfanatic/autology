"""Doc snapshot-diff connector: .txt/.md files -> span atoms per paragraph.

Whitepaper §11.2 M1 "doc snapshot-diff". Each paragraph (maximal run of
non-blank lines, offsets over the CRLF/CR -> LF normalized text) becomes a span
atom ``atom://{source}/{doc_path}#span:{start}-{end}``.

Span identity & the EXACT stability guarantee
---------------------------------------------
The contracts.atoms default content address is xxh3(uri, value). For spans that
formula is offset-fragile: inserting a paragraph above an unchanged one shifts
its offsets, changes its uri, and would mint a new atom_id — breaking every
citation that points at text that did not change. M1 therefore anchors span
identity by paragraph CONTENT, passing an explicit atom_id:

    atom_id = xxh3("span", source_id, doc_path, xxh3(paragraph_text), occurrence)

where ``occurrence`` is the 1-based index of this paragraph among paragraphs
with identical text in the same document, counted in document order.

Guarantee (implemented and tested):
1. A paragraph whose text is unchanged keeps its atom_id across pulls EVEN IF
   it moves (paragraphs added/removed/edited around it, or the paragraph is
   reordered). Pure moves emit NO delta at all — no insert, update, or delete —
   so no downstream invalidation fires for text that did not change.
2. The uri registered in the ledger reflects the offsets at FIRST sighting; the
   connector state always tracks current offsets. Citations must resolve via
   atom_id (content addressing), which is exactly what keeps them stable.
3. For duplicated paragraph text, identity is per-occurrence (in document
   order). Adding/removing one duplicate shifts identity only within that set
   of byte-identical paragraphs, so any citation still resolves to identical
   text. Which physical duplicate "is" occurrence i is not observable.
4. An EDITED paragraph is a new atom superseding the old one: the diff pairs
   removed/added paragraphs positionally (difflib.SequenceMatcher opcodes over
   the sequence of paragraph content hashes, restricted to paragraphs that do
   not survive elsewhere in the document) and emits kind="update" with
   superseded_atom_id. Unpairable leftovers degrade to insert/delete.

Deviation note: passing an explicit atom_id departs from the atoms.py default
hash(uri, value) — the dataclass supports it, and M1's spec line ("spans
re-anchored by paragraph content hash ... content addressing must keep
citations stable") requires it. Recorded in the module README.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

import pyarrow as pa

from ontoforge.contracts import Atom, AtomDelta, DeltaBatch, span_uri

from .base import JSONState, STATE_FORMAT, check_state, hash64, next_cycle, quote_doc_path, read_text_robust

_STATE_KIND = "docs"
_DOC_SUFFIXES = (".txt", ".md")

# a paragraph = maximal run of lines that each contain at least one non-space char
_PARA_RE = re.compile(r"[^\n]*\S[^\n]*(?:\n[^\n]*\S[^\n]*)*")


def normalize_text(text: str) -> str:
    """CRLF / lone-CR -> LF. All offsets are over this normalized text."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_paragraphs(text: str) -> list[tuple[int, int, str]]:
    """Return [(start, end, paragraph_text)] over normalized text; blank lines separate."""
    return [(m.start(), m.end(), m.group()) for m in _PARA_RE.finditer(text)]


class _Para:
    __slots__ = ("start", "end", "text", "content_hash", "occ", "atom_id", "uri")

    def __init__(self, source_id: str, doc_path: str, start: int, end: int, text: str, occ: int):
        self.start = start
        self.end = end
        self.text = text
        self.content_hash = hash64(text)
        self.occ = occ
        self.atom_id = hash64("span", source_id, doc_path, self.content_hash, str(occ))
        self.uri = span_uri(source_id, quote_doc_path(doc_path), start, end)


class DocConnector:
    """Snapshot-diff over a directory of .txt/.md files (recursive)."""

    def __init__(self, source_id: str, dir: Path | str) -> None:
        self.source_id = source_id
        self.dir = Path(dir)
        self._last_table: pa.Table | None = None

    # ----------------------------------------------------------------- protocol

    def snapshot_tables(self) -> list[tuple[str, pa.Table]]:
        if self._last_table is None:
            raise RuntimeError("snapshot_tables() requires a prior pull()")
        return [("docs", self._last_table)]

    def pull(self, state: JSONState) -> tuple[DeltaBatch, dict]:
        prior = check_state(state, _STATE_KIND)
        cycle = next_cycle(prior)
        old_docs: dict[str, dict] = prior.get("docs", {})

        new_docs: dict[str, dict] = {}
        deltas: list[AtomDelta] = []
        rows: list[dict] = []

        files = sorted(
            p for p in self.dir.rglob("*") if p.is_file() and p.suffix.lower() in _DOC_SUFFIXES
        )
        for f in files:
            rel = f.relative_to(self.dir).as_posix()
            text = normalize_text(read_text_robust(f))
            doc_hash = hash64(text)
            paras = self._index_paragraphs(rel, text)
            rows.extend(
                {"doc_path": rel, "start": p.start, "end": p.end, "content": p.text} for p in paras
            )
            new_docs[rel] = {
                "doc_hash": doc_hash,
                "paras": [
                    {"h": p.content_hash, "occ": p.occ, "atom_id": p.atom_id,
                     "start": p.start, "end": p.end}
                    for p in paras
                ],
            }
            old = old_docs.get(rel)
            if old is None:
                deltas.extend(AtomDelta(kind="insert", atom=self._atom(p)) for p in paras)
            elif old["doc_hash"] != doc_hash:
                deltas.extend(self._diff_doc(rel, old["paras"], paras))
            # identical doc_hash: zero deltas

        for rel, old in old_docs.items():
            if rel not in new_docs:
                deltas.extend(self._delete(rel, op) for op in old["paras"])

        self._last_table = pa.table(
            {
                "doc_path": pa.array([r["doc_path"] for r in rows], type=pa.string()),
                "start": pa.array([r["start"] for r in rows], type=pa.int64()),
                "end": pa.array([r["end"] for r in rows], type=pa.int64()),
                "content": pa.array([r["content"] for r in rows], type=pa.string()),
            }
        )
        new_state = {"format": STATE_FORMAT, "kind": _STATE_KIND, "cycle": cycle, "docs": new_docs}
        return DeltaBatch(source_id=self.source_id, cycle=cycle, deltas=deltas), new_state

    # ------------------------------------------------------------------ helpers

    def _index_paragraphs(self, rel: str, text: str) -> list[_Para]:
        occ_counter: dict[str, int] = {}
        out: list[_Para] = []
        for start, end, ptext in split_paragraphs(text):
            ch = hash64(ptext)
            occ = occ_counter.get(ch, 0) + 1
            occ_counter[ch] = occ
            out.append(_Para(self.source_id, rel, start, end, ptext, occ))
        return out

    def _atom(self, p: _Para) -> Atom:
        return Atom(uri=p.uri, value=p.text, atom_id=p.atom_id)

    def _delete(self, rel: str, old_para: dict) -> AtomDelta:
        uri = span_uri(self.source_id, quote_doc_path(rel), old_para["start"], old_para["end"])
        return AtomDelta(
            kind="delete", atom=Atom(uri=uri, value=None), superseded_atom_id=old_para["atom_id"]
        )

    def _diff_doc(self, rel: str, old_paras: list[dict], new_paras: list[_Para]) -> list[AtomDelta]:
        """Diff one document's paragraph sequence.

        Survivor rule first: any identity (content-hash + occurrence) present on
        BOTH sides emits nothing — that is the move-stability guarantee. The
        remaining removed/added paragraphs are aligned positionally inside
        SequenceMatcher replace blocks and emitted as updates; leftovers become
        deletes/inserts.
        """
        old_ids = {p["atom_id"] for p in old_paras}
        new_ids = {p.atom_id for p in new_paras}

        sm = SequenceMatcher(
            None, [p["h"] for p in old_paras], [p.content_hash for p in new_paras], autojunk=False
        )
        deltas: list[AtomDelta] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            # paragraphs that survive elsewhere in the doc are moves: skip them
            removed = [p for p in old_paras[i1:i2] if p["atom_id"] not in new_ids]
            added = [p for p in new_paras[j1:j2] if p.atom_id not in old_ids]
            k = min(len(removed), len(added)) if tag == "replace" else 0
            for op, np in zip(removed[:k], added[:k]):
                deltas.append(
                    AtomDelta(kind="update", atom=self._atom(np), superseded_atom_id=op["atom_id"])
                )
            deltas.extend(self._delete(rel, op) for op in removed[k:])
            deltas.extend(AtomDelta(kind="insert", atom=self._atom(np)) for np in added[k:])
        return deltas


__all__ = ["DocConnector", "normalize_text", "split_paragraphs"]
