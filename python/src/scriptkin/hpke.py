"""HPKE base mode (RFC 9180) for one suite: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20-Poly1305.

This is CryptoKit's `HPKE.Ciphersuite.Curve25519_SHA256_ChachaPoly`. Each export holds one sealed
message, so only sequence number 0 exists. Keys are raw 32-byte X25519 values.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand

KEM_ID, KDF_ID, AEAD_ID = 0x0020, 0x0001, 0x0003
_KEM_SUITE = b"KEM" + KEM_ID.to_bytes(2)
_HPKE_SUITE = b"HPKE" + KEM_ID.to_bytes(2) + KDF_ID.to_bytes(2) + AEAD_ID.to_bytes(2)
_MODE_BASE = b"\x00"
_NK, _NN, _NSECRET = 32, 12, 32


def _extract(suite: bytes, salt: bytes, label: bytes, ikm: bytes) -> bytes:
    return HKDF.extract(hashes.SHA256(), salt, b"HPKE-v1" + suite + label + ikm)


def _expand(suite: bytes, prk: bytes, label: bytes, info: bytes, length: int) -> bytes:
    labeled = length.to_bytes(2) + b"HPKE-v1" + suite + label + info
    return HKDFExpand(hashes.SHA256(), length, labeled).derive(prk)


def _shared_secret(dh: bytes, enc: bytes, pk_r: bytes) -> bytes:
    prk = _extract(_KEM_SUITE, b"", b"eae_prk", dh)
    return _expand(_KEM_SUITE, prk, b"shared_secret", enc + pk_r, _NSECRET)


def _key_and_nonce(shared_secret: bytes, info: bytes) -> tuple[bytes, bytes]:
    psk_id_hash = _extract(_HPKE_SUITE, b"", b"psk_id_hash", b"")
    info_hash = _extract(_HPKE_SUITE, b"", b"info_hash", info)
    context = _MODE_BASE + psk_id_hash + info_hash
    secret = _extract(_HPKE_SUITE, shared_secret, b"secret", b"")
    key = _expand(_HPKE_SUITE, secret, b"key", context, _NK)
    return key, _expand(_HPKE_SUITE, secret, b"base_nonce", context, _NN)


def public_key(private_key: bytes) -> bytes:
    return X25519PrivateKey.from_private_bytes(private_key).public_key().public_bytes_raw()


def seal(
    recipient: bytes, info: bytes, aad: bytes, plaintext: bytes, *, ephemeral: bytes | None = None
) -> tuple[bytes, bytes]:
    """Return (encapsulated key, ciphertext). `ephemeral` fixes the sender key for test vectors only."""
    sk_e = (
        X25519PrivateKey.generate() if ephemeral is None else X25519PrivateKey.from_private_bytes(ephemeral)
    )
    enc = sk_e.public_key().public_bytes_raw()
    dh = sk_e.exchange(X25519PublicKey.from_public_bytes(recipient))
    key, nonce = _key_and_nonce(_shared_secret(dh, enc, recipient), info)
    return enc, ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def open_sealed(private_key: bytes, enc: bytes, info: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    """Open one base-mode message. Raises `cryptography.exceptions.InvalidTag` if it does not authenticate."""
    sk_r = X25519PrivateKey.from_private_bytes(private_key)
    dh = sk_r.exchange(X25519PublicKey.from_public_bytes(enc))
    key, nonce = _key_and_nonce(_shared_secret(dh, enc, sk_r.public_key().public_bytes_raw()), info)
    return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
