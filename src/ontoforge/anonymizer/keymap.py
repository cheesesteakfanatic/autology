"""The reverse map (token → raw), ENCRYPTED with the customer key (v2.1 §7).

OPEN-SHELL (docs/IP_ARCHITECTURE.md). The keymap is the ONLY path back to raw:
:func:`~ontoforge.anonymizer.anonymize.anonymize` emits it, the customer keeps it
and the key, and :func:`~ontoforge.anonymizer.decipher.decipher` uses it to decode
the engine's output. The anonymized tables alone are useless without it.

DEMO-GRADE CIPHER — be honest about it
--------------------------------------
This uses **only the Python standard library** (``hashlib`` / ``hmac`` /
``secrets``) to stay dependency-free and fully auditable, so it is a *documented,
demo-grade* construction, **NOT an audited KMS / AEAD**. The construction is:

    keystream = SHA256-CTR(KDF(key, "keymap-enc") || nonce || counter)
    ciphertext = plaintext XOR keystream                 # XOR stream cipher
    tag = HMAC-SHA256(KDF(key, "keymap-mac"), nonce || ciphertext)   # encrypt-then-MAC

i.e. an encrypt-then-MAC XOR stream cipher with a random 16-byte nonce and a
SHA256-based counter-mode keystream. It gives confidentiality + tamper-evidence
for the demo and a clean place to swap in real AEAD (libsodium / AWS-KMS) in
production. The honesty is the point: a customer auditing this module sees
exactly what protects their data and that nothing here phones home.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from .tokenize import derive_subkey

__all__ = [
    "KeyMap",
    "EncryptedKeyMap",
    "encrypt_keymap",
    "decrypt_keymap",
    "DecipherError",
]

_NONCE_LEN = 16
_TAG_LEN = 32
_MAGIC = b"OFXKM1\x00\x00"  # 8-byte format tag, versioned


class DecipherError(ValueError):
    """Raised when a keymap fails to authenticate (wrong key or tampering)."""


@dataclass(frozen=True, slots=True)
class KeyMap:
    """The plaintext reverse map: token (as displayed) → raw value (display str).

    Built on the customer machine during anonymization and immediately sealed by
    :func:`encrypt_keymap`. It should never be persisted in the clear."""

    mapping: dict[str, str]

    def raw_for(self, token: str) -> str | None:
        return self.mapping.get(token)

    def to_json(self) -> str:
        # sorted keys ⇒ deterministic plaintext ⇒ deterministic ciphertext under a
        # fixed nonce (we use a random nonce by default; pass one for repro tests).
        return json.dumps(self.mapping, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "KeyMap":
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise DecipherError("keymap payload is not an object")
        return cls(mapping={str(k): str(v) for k, v in obj.items()})


@dataclass(frozen=True, slots=True)
class EncryptedKeyMap:
    """Opaque sealed keymap: ``MAGIC || nonce || tag || ciphertext`` as bytes.

    Carries no plaintext. Round-trips through :meth:`to_bytes` / :meth:`from_bytes`
    so the customer can store it next to the anonymized extract."""

    blob: bytes

    def to_bytes(self) -> bytes:
        return self.blob

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedKeyMap":
        return cls(blob=bytes(data))

    def __len__(self) -> int:
        return len(self.blob)


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA256 counter-mode keystream of ``length`` bytes."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_keymap(keymap: KeyMap, key: bytes, *, nonce: bytes | None = None) -> EncryptedKeyMap:
    """Seal ``keymap`` under the customer ``key`` (encrypt-then-MAC, stdlib only).

    A fresh random 16-byte nonce is drawn unless one is supplied (supply one only
    for deterministic tests). The result authenticates the nonce + ciphertext, so
    a wrong key or any tampering is detected on :func:`decrypt_keymap`."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    enc_key = derive_subkey(key, "keymap-enc")
    mac_key = derive_subkey(key, "keymap-mac")
    if nonce is None:
        nonce = secrets.token_bytes(_NONCE_LEN)
    elif len(nonce) != _NONCE_LEN:
        raise ValueError(f"nonce must be {_NONCE_LEN} bytes")

    plaintext = keymap.to_json().encode("utf-8")
    ciphertext = bytes(p ^ k for p, k in zip(plaintext, _keystream(enc_key, nonce, len(plaintext))))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return EncryptedKeyMap(blob=_MAGIC + nonce + tag + ciphertext)


def decrypt_keymap(enc: EncryptedKeyMap | bytes, key: bytes) -> KeyMap:
    """Open a sealed keymap with the customer ``key``.

    Verifies the HMAC tag in constant time BEFORE decrypting; raises
    :class:`DecipherError` on a wrong key or tampered blob — never returns garbage
    plaintext."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    blob = enc.blob if isinstance(enc, EncryptedKeyMap) else bytes(enc)
    if len(blob) < len(_MAGIC) + _NONCE_LEN + _TAG_LEN:
        raise DecipherError("encrypted keymap is too short / corrupt")
    if blob[: len(_MAGIC)] != _MAGIC:
        raise DecipherError("not an OntoForge encrypted keymap (bad magic)")
    off = len(_MAGIC)
    nonce = blob[off : off + _NONCE_LEN]
    off += _NONCE_LEN
    tag = blob[off : off + _TAG_LEN]
    off += _TAG_LEN
    ciphertext = blob[off:]

    enc_key = derive_subkey(key, "keymap-enc")
    mac_key = derive_subkey(key, "keymap-mac")
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise DecipherError("keymap authentication failed — wrong key or tampered data")

    plaintext = bytes(c ^ k for c, k in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))
    return KeyMap.from_json(plaintext.decode("utf-8"))
