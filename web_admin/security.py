from __future__ import annotations

import hashlib
import hmac
import secrets


SESSION_TOKEN_BYTES = 32
CSRF_TOKEN_BYTES = 32


def generate_token(*, num_bytes: int = SESSION_TOKEN_BYTES) -> str:
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)
