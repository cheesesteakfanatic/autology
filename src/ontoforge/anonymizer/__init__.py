"""Client-side anonymization toolkit — the TRUST ARCHITECTURE (v2.1 §7).

OPEN-SHELL per docs/IP_ARCHITECTURE.md. This is the part of OntoForge we WANT
customers to audit: it runs on the CUSTOMER's machine, before any data reaches
us. One-click *anonymize* on the way in, one-click *decipher* on the way out.
**The customer holds the key; we run all compute on the anonymized input and
never see a raw value.** Open-sourcing this module is the trust play — anyone can
read it and confirm the raw never leaves the building.

How it preserves the engine while hiding the data
--------------------------------------------------
The whole point is that anonymized data must still be *analyzable*: the
relationship engine has to find the SAME typed relationships on the tokenized
tables as on the raw tables, or anonymization would destroy the product. So the
tokenization is deliberately **structure-preserving**:

* **Join-preserving.** A value is mapped to its token by HMAC-SHA256 keyed with
  the customer secret. The map is a pure function of the value, so the SAME raw
  value yields the SAME token in EVERY table — a value that joins across tables
  STILL JOINS after anonymization. Tokenization is an injective relabeling of the
  value universe, and set-overlap structure (containment / Jaccard / MinHash) is
  invariant under an injective relabeling: the join survives exactly.
* **Distribution-preserving (categoricals).** Relabeling keeps every value's
  multiplicity, so the value-frequency distribution is unchanged — the
  distribution-divergence (JSD) and entropy signals see the same numbers.
* **Order- and distribution-preserving (numerics/dates).** Numbers and dates go
  through a keyed, strictly **monotone** transform (a keyed affine map per
  column-dimension), so deciles, quantile divergence, and ordering all survive
  while the literal values are scrambled.
* **Format-preserving (optional).** A token can keep the source value's length
  and character class so the profiler's format-signature and the engine still
  behave on shape-sensitive columns.

What gets tokenized is chosen by a :class:`Policy`: a PII/sensitivity classifier
(reusing the email/phone/SSN/card/name patterns from ``aimodels.secure``) plus an
explicit per-column **allow/deny**. Non-sensitive columns pass through untouched.

The reverse map (token → raw) is the :class:`~ontoforge.anonymizer.keymap.KeyMap`,
**encrypted with the customer key** (a documented, demo-grade KDF + XOR-stream
cipher — honest about being demo-grade, NOT an audited KMS). The encrypted keymap
is the ONLY path back to raw; :func:`decipher` uses it to one-click decode an
engine answer/citation on the customer side.

Stdlib-only crypto (``hashlib`` / ``hmac`` / ``secrets``) + pandas/numpy/pyarrow.
Deterministic for a fixed key; a DIFFERENT key yields different tokens but the
SAME join structure. Zero network.
"""

from __future__ import annotations

from .anonymize import (
    AnonymizationResult,
    anonymize,
    anonymize_table,
    anonymize_with_audit,
    is_token,
    scan_for_raw,
)
from .decipher import decipher, decipher_value
from .keymap import DecipherError, EncryptedKeyMap, KeyMap, decrypt_keymap, encrypt_keymap
from .tokenize import (
    Policy,
    Sensitivity,
    Tokenizer,
    classify_column,
    derive_subkey,
    monotone_numeric_map,
)

__all__ = [
    "AnonymizationResult",
    "DecipherError",
    "EncryptedKeyMap",
    "KeyMap",
    "Policy",
    "Sensitivity",
    "Tokenizer",
    "anonymize",
    "anonymize_table",
    "anonymize_with_audit",
    "classify_column",
    "decipher",
    "decipher_value",
    "decrypt_keymap",
    "derive_subkey",
    "encrypt_keymap",
    "is_token",
    "monotone_numeric_map",
    "scan_for_raw",
]
