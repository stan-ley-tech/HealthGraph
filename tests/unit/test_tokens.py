import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.tokens import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.models.enums import Role

pytestmark = pytest.mark.unit


def test_decode_returns_the_claims_that_were_encoded():
    user_id = uuid.uuid4()
    token = create_access_token(user_id=user_id, role=Role.DOCTOR)

    claims = decode_access_token(token)

    assert claims.user_id == user_id
    assert claims.role is Role.DOCTOR


def test_decode_rejects_garbage():
    with pytest.raises(AuthenticationError):
        decode_access_token("not-a-real-token")


def test_decode_rejects_an_expired_token():
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "role": Role.ADMIN.value,
        "type": "access",
        "iat": now - timedelta(minutes=30),
        "exp": now - timedelta(minutes=15),
    }
    expired_token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(AuthenticationError):
        decode_access_token(expired_token)


def test_decode_rejects_a_token_signed_with_a_different_key():
    payload = {
        "sub": str(uuid.uuid4()),
        "role": Role.ADMIN.value,
        "type": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    forged_token = jwt.encode(payload, "a-completely-different-secret", algorithm="HS256")

    with pytest.raises(AuthenticationError):
        decode_access_token(forged_token)


def test_decode_rejects_a_refresh_token_presented_as_an_access_token():
    settings = get_settings()
    payload = {
        "sub": str(uuid.uuid4()),
        "role": Role.ADMIN.value,
        "type": "refresh",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_refresh_tokens_are_unique_and_high_entropy():
    tokens = {generate_refresh_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) >= 32 for token in tokens)


def test_hash_refresh_token_is_stable_and_one_way():
    raw = generate_refresh_token()
    assert hash_refresh_token(raw) == hash_refresh_token(raw)
    assert hash_refresh_token(raw) != raw
