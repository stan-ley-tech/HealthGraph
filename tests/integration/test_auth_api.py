import pytest

from app.models.enums import Role
from tests.integration._helpers import auth_headers, register_and_login

pytestmark = pytest.mark.integration


async def test_register_creates_a_user(client, unique_email):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "supersecret123", "role": "ADMIN"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == unique_email
    assert body["role"] == "ADMIN"
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_rejects_a_duplicate_email(client, unique_email):
    payload = {"email": unique_email, "password": "supersecret123", "role": "ADMIN"}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)

    assert second.status_code == 409


async def test_register_rejects_a_short_password(client, unique_email):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "short", "role": "ADMIN"},
    )
    assert response.status_code == 422


async def test_login_succeeds_with_correct_credentials(client, unique_email):
    await register_and_login(client, email=unique_email, role=Role.ADMIN)


async def test_login_fails_with_wrong_password(client, unique_email):
    await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "supersecret123", "role": "ADMIN"},
    )

    response = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": "wrong-password"}
    )

    assert response.status_code == 401


async def test_login_fails_for_unknown_email(client, unique_email):
    response = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": "whatever123"}
    )
    assert response.status_code == 401


async def test_refresh_token_rotates_and_the_old_one_becomes_invalid(client, unique_email):
    await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "supersecret123", "role": "ADMIN"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": "supersecret123"}
    )
    old_refresh_token = login.json()["refresh_token"]

    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
    )
    assert refreshed.status_code == 200
    # The refresh token is cryptographically random, so it's guaranteed
    # to differ. The access token is a JWT with second-resolution
    # timestamps and otherwise-identical claims, so it can legitimately
    # come out byte-identical if minted within the same wall-clock
    # second — that's not a property worth asserting on.
    assert refreshed.json()["refresh_token"] != old_refresh_token

    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh_token}
    )
    assert reused.status_code == 401


async def test_logout_revokes_the_refresh_token(client, unique_email):
    await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "supersecret123", "role": "ADMIN"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": "supersecret123"}
    )
    refresh_token = login.json()["refresh_token"]

    logout_response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )
    assert logout_response.status_code == 204

    reuse_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert reuse_response.status_code == 401


async def test_admin_endpoint_requires_a_token(client):
    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 401


async def test_admin_endpoint_rejects_a_non_admin_role(client, unique_email):
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)

    response = await client.get("/api/v1/admin/users", headers=auth_headers(token))

    assert response.status_code == 403


async def test_admin_endpoint_allows_an_admin(client, unique_email):
    token = await register_and_login(client, email=unique_email, role=Role.ADMIN)

    response = await client.get("/api/v1/admin/users", headers=auth_headers(token))

    assert response.status_code == 200
    assert any(u["email"] == unique_email for u in response.json())
