"""Shared setup helpers for HTTP-level tests. Not a test module itself
(no `test_` prefix), so pytest won't try to collect it."""

from datetime import date

import httpx

from app.models.enums import FacilityType, RelationshipType, Role, Sex
from app.models.facility import Facility
from app.models.provider import Provider
from app.repositories.patient import PatientRepository
from app.repositories.relationship import PatientProviderRelationshipRepository
from app.repositories.user import UserRepository

_PASSWORD = "supersecret123"


async def register_and_login(client: httpx.AsyncClient, *, email: str, role: Role) -> str:
    """Creates a user via the real REST endpoints and returns an access
    token — exercising the actual auth flow rather than minting a token
    by hand, so these tests catch regressions in registration/login too.
    """
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "role": role.value},
    )
    assert response.status_code == 201, response.text

    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def get_user_id_by_email(db_session, email: str):
    user = await UserRepository(db_session).get_by_email(email)
    assert user is not None
    return user.id


async def make_patient(db_session, *, mrn: str, ssn: str | None = None):
    repo = PatientRepository(db_session)
    patient = repo.create(
        mrn=mrn,
        first_name="Test",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
        sex=Sex.OTHER,
        ssn=ssn,
    )
    await db_session.commit()
    return patient


async def make_provider(db_session, *, npi_number: str):
    facility = Facility(name="Test Facility", facility_type=FacilityType.CLINIC)
    db_session.add(facility)
    await db_session.flush()

    provider = Provider(
        facility_id=facility.id,
        first_name="Test",
        last_name="Provider",
        npi_number=npi_number,
    )
    db_session.add(provider)
    await db_session.commit()
    return provider


async def link_provider_to_user(db_session, provider, user_id) -> None:
    provider.user_id = user_id
    await db_session.commit()


async def make_relationship(db_session, *, patient_id, provider_id) -> None:
    repo = PatientProviderRelationshipRepository(db_session)
    repo.create(
        patient_id=patient_id,
        provider_id=provider_id,
        relationship_type=RelationshipType.PRIMARY_CARE,
        start_date=date(2026, 1, 1),
    )
    await db_session.commit()
