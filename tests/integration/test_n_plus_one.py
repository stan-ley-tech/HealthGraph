"""The regression test this whole DataLoader layer exists to satisfy:
fetching a nested, per-patient field (`encounters`) across a page of
patients must cost a constant number of queries, not one query per
patient. If a future change accidentally reintroduces N+1 behavior
(e.g. a resolver that reaches for a repository directly instead of a
DataLoader), this fails regardless of how many patients are in the page.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.models.enums import EncounterType, FacilityType, Role
from app.models.facility import Facility
from app.models.provider import Provider
from app.repositories.clinical import EncounterRepository
from tests.integration._helpers import auth_headers, make_patient, register_and_login

pytestmark = pytest.mark.integration

_QUERY = """
query($first: Int!) {
  patients(first: $first) {
    edges { node { encounters(limit: 5) { chiefComplaint } } }
  }
}
"""


_TRANSACTION_CONTROL_PREFIXES = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


@contextmanager
def count_queries(db_session):
    """Counts real SQL statements, not the SAVEPOINT bookkeeping this
    test's transaction-sharing setup adds on top (see the `db_session`
    fixture) — that's an artifact of testing through one shared
    connection, not something a production request would ever emit."""
    sync_engine = db_session.bind.engine.sync_engine
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        if not statement.startswith(_TRANSACTION_CONTROL_PREFIXES):
            counter["n"] += 1

    event.listen(sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_execute)


async def _seed_patients_with_encounters(db_session, count):
    short_id = uuid.uuid4().hex[:6].upper()
    facility = Facility(name="N+1 Test Facility", facility_type=FacilityType.CLINIC)
    db_session.add(facility)
    await db_session.flush()
    provider = Provider(
        facility_id=facility.id, first_name="N", last_name="One",
        npi_number=uuid.uuid4().hex[:10],
    )
    db_session.add(provider)
    await db_session.flush()

    encounters = EncounterRepository(db_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(count):
        patient = await make_patient(db_session, mrn=f"MRN-N1-{short_id}-{i}")
        encounters.create(
            patient_id=patient.id, provider_id=provider.id, facility_id=facility.id,
            encounter_type=EncounterType.OUTPATIENT, chief_complaint=f"Visit {i}",
            started_at=base + timedelta(days=i),
        )
    await db_session.commit()


async def _run_query(client, admin_token, first):
    response = await client.post(
        "/graphql", json={"query": _QUERY, "variables": {"first": first}},
        headers=auth_headers(admin_token),
    )
    body = response.json()
    assert "errors" not in body, body
    return body["data"]["patients"]["edges"]


async def test_query_count_does_not_grow_with_the_number_of_patients(
    client, db_session, unique_email
):
    admin_token = await register_and_login(client, email=unique_email, role=Role.ADMIN)

    await _seed_patients_with_encounters(db_session, 3)
    with count_queries(db_session) as small_page:
        small_edges = await _run_query(client, admin_token, first=3)

    await _seed_patients_with_encounters(db_session, 12)
    with count_queries(db_session) as large_page:
        large_edges = await _run_query(client, admin_token, first=12)

    assert len(small_edges) == 3
    assert len(large_edges) == 12
    assert small_page["n"] == large_page["n"], (
        f"query count grew with page size ({small_page['n']} for 3 patients vs "
        f"{large_page['n']} for 12) — a DataLoader batch probably regressed to N+1"
    )
    # Two queries total: one page of patients, one batched load of every
    # matched patient's encounters — not one encounters query per patient.
    assert small_page["n"] == 2
