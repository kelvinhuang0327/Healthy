from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from pwdlib import PasswordHash

PASSWORD_HASHER = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH
    return PASSWORD_HASHER.verify(password, candidate_hash)


def new_session_credential() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_session_token(raw_token)


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_csrf_token(session_id: uuid.UUID, secret: bytes) -> str:
    nonce = secrets.token_urlsafe(24)
    message = f"{session_id}.{nonce}".encode()
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def validate_csrf_token(
    *,
    session_id: uuid.UUID,
    cookie_token: str,
    header_token: str,
    secret: bytes,
) -> bool:
    if not hmac.compare_digest(cookie_token, header_token):
        return False
    try:
        nonce, supplied_signature = cookie_token.split(".", maxsplit=1)
    except ValueError:
        return False
    message = f"{session_id}.{nonce}".encode()
    expected_signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied_signature, expected_signature)
