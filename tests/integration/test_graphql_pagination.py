"""Pagination behavior through the actual GraphQL API, complementing the
lower-level keyset-pagination correctness tests in
`test_pagination_keyset.py` (which exercise the repository directly)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import EncounterType, FacilityType, Role
from app.models.facility import Facility
from app.models.provider import Provider
from app.repositories.clinical import EncounterRepository
from tests.integration._helpers import auth_headers, make_patient, register_and_login

pytestmark = pytest.mark.integration

_ENCOUNTERS_QUERY = """
query($id: ID!, $first: Int!, $after: String) {
  patientEncounters(patientId: $id, first: $first, after: $after) {
    edges { cursor node { chiefComplaint } }
    pageInfo { hasNextPage hasPreviousPage endCursor }
  }
}
"""


@pytest.fixture
async def admin_token(client, unique_email):
    return await register_and_login(client, email=unique_email, role=Role.ADMIN)


async def _seed_encounters(db_session, patient_id, count, npi_seed):
    facility = Facility(name="Pagination Test Facility", facility_type=FacilityType.CLINIC)
    db_session.add(facility)
    await db_session.flush()
    provider = Provider(
        facility_id=facility.id, first_name="Pag", last_name="Ination",
        npi_number=npi_seed[-10:].rjust(10, "4"),
    )
    db_session.add(provider)
    await db_session.flush()

    encounters = EncounterRepository(db_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(count):
        encounters.create(
            patient_id=patient_id, provider_id=provider.id, facility_id=facility.id,
            encounter_type=EncounterType.OUTPATIENT, chief_complaint=f"Visit {i}",
            started_at=base + timedelta(days=i),
        )
    await db_session.commit()


async def test_first_page_respects_the_first_argument(client, db_session, unique_mrn, admin_token):
    patient = await make_patient(db_session, mrn=unique_mrn)
    await _seed_encounters(db_session, patient.id, 5, unique_mrn)

    variables = {"id": str(patient.id), "first": 2, "after": None}
    response = await client.post(
        "/graphql",
        json={"query": _ENCOUNTERS_QUERY, "variables": variables},
        headers=auth_headers(admin_token),
    )

    data = response.json()["data"]["patientEncounters"]
    assert len(data["edges"]) == 2
    assert data["pageInfo"]["hasNextPage"] is True
    assert data["pageInfo"]["hasPreviousPage"] is False


async def test_walking_every_page_returns_every_row_once(
    client, db_session, unique_mrn, admin_token
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    await _seed_encounters(db_session, patient.id, 5, unique_mrn)

    seen = []
    after = None
    for _ in range(10):
        response = await client.post(
            "/graphql",
            json={
                "query": _ENCOUNTERS_QUERY,
                "variables": {"id": str(patient.id), "first": 2, "after": after},
            },
            headers=auth_headers(admin_token),
        )
        data = response.json()["data"]["patientEncounters"]
        seen.extend(edge["node"]["chiefComplaint"] for edge in data["edges"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]

    assert len(seen) == 5
    assert len(set(seen)) == 5
    # Most recent (Visit 4) first.
    assert seen[0] == "Visit 4"


async def test_empty_patient_has_no_next_page(client, db_session, unique_mrn, admin_token):
    patient = await make_patient(db_session, mrn=unique_mrn)

    variables = {"id": str(patient.id), "first": 10, "after": None}
    response = await client.post(
        "/graphql",
        json={"query": _ENCOUNTERS_QUERY, "variables": variables},
        headers=auth_headers(admin_token),
    )

    data = response.json()["data"]["patientEncounters"]
    assert data["edges"] == []
    assert data["pageInfo"]["hasNextPage"] is False
