"""The encrypted keymap: round-trip, key-binding, tamper-evidence, determinism."""

from __future__ import annotations

import pytest

from ontoforge.anonymizer.keymap import (
    DecipherError,
    EncryptedKeyMap,
    KeyMap,
    decrypt_keymap,
    encrypt_keymap,
)

KEY = b"customer-secret-key-001"
MAP = {"OFX_aaa": "alice@acme.com", "OFX_bbb": "bob@acme.com", "-42": "7"}


def test_encrypt_decrypt_round_trip_is_exact() -> None:
    enc = encrypt_keymap(KeyMap(MAP), KEY)
    back = decrypt_keymap(enc, KEY)
    assert back.mapping == MAP


def test_ciphertext_contains_no_plaintext() -> None:
    enc = encrypt_keymap(KeyMap(MAP), KEY)
    blob = enc.to_bytes()
    assert b"alice@acme.com" not in blob
    assert b"bob@acme.com" not in blob


def test_wrong_key_is_rejected_not_garbage() -> None:
    enc = encrypt_keymap(KeyMap(MAP), KEY)
    with pytest.raises(DecipherError):
        decrypt_keymap(enc, b"the-wrong-key")


def test_tampering_is_detected() -> None:
    enc = encrypt_keymap(KeyMap(MAP), KEY)
    blob = bytearray(enc.to_bytes())
    blob[-1] ^= 0x01  # flip a ciphertext bit
    with pytest.raises(DecipherError):
        decrypt_keymap(EncryptedKeyMap(bytes(blob)), KEY)


def test_deterministic_under_fixed_nonce() -> None:
    nonce = b"0123456789abcdef"
    a = encrypt_keymap(KeyMap(MAP), KEY, nonce=nonce).to_bytes()
    b = encrypt_keymap(KeyMap(MAP), KEY, nonce=nonce).to_bytes()
    assert a == b


def test_random_nonce_makes_ciphertext_unequal_but_decryptable() -> None:
    a = encrypt_keymap(KeyMap(MAP), KEY)
    b = encrypt_keymap(KeyMap(MAP), KEY)
    assert a.to_bytes() != b.to_bytes()  # different nonces
    assert decrypt_keymap(a, KEY).mapping == decrypt_keymap(b, KEY).mapping


def test_bytes_round_trip_of_encrypted_blob() -> None:
    enc = encrypt_keymap(KeyMap(MAP), KEY)
    restored = EncryptedKeyMap.from_bytes(enc.to_bytes())
    assert decrypt_keymap(restored, KEY).mapping == MAP


def test_passphrase_key_accepted() -> None:
    enc = encrypt_keymap(KeyMap(MAP), "a-human-passphrase")
    assert decrypt_keymap(enc, "a-human-passphrase").mapping == MAP


def test_corrupt_blob_rejected() -> None:
    with pytest.raises(DecipherError):
        decrypt_keymap(EncryptedKeyMap(b"not-a-keymap"), KEY)
