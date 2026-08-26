"""Sanity checks for the transactional test fixtures themselves. If these
fail, every other integration/e2e test's isolation guarantee is suspect.

`test_step_1_commit_a_row` and `test_step_2_row_is_gone_after_teardown`
rely on running in file order (pytest's default, and we don't use a
randomizing plugin): the first commits a row through the fixture — the
same `session.commit()` a real mutation resolver would call — and the
second, in a completely fresh `db_session`, confirms it never actually
persisted. That's the whole point of `join_transaction_mode="create_savepoint"`
in the `db_session` fixture: an internal commit only releases a
savepoint, so the outer rollback at teardown still discards it.
"""

from datetime import date

import pytest

from app.models.enums import Sex
from app.repositories.patient import PatientRepository
from app.repositories.user import UserRepository

pytestmark = pytest.mark.integration

_ROLLBACK_CHECK_MRN = "MRN-ROLLBACK-CHK"


async def test_db_session_can_read_and_write(db_session, unique_mrn):
    repo = PatientRepository(db_session)
    patient = repo.create(
        mrn=unique_mrn,
        first_name="Test",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
        sex=Sex.OTHER,
    )
    await db_session.flush()

    fetched = await repo.get_by_id(patient.id)
    assert fetched is not None
    assert fetched.mrn == unique_mrn


async def test_step_1_commit_a_row(db_session):
    repo = PatientRepository(db_session)
    repo.create(
        mrn=_ROLLBACK_CHECK_MRN,
        first_name="Rollback",
        last_name="Check",
        date_of_birth=date(1990, 1, 1),
        sex=Sex.OTHER,
    )
    await db_session.commit()


async def test_step_2_row_is_gone_after_teardown(db_session):
    repo = PatientRepository(db_session)
    assert await repo.get_by_mrn(_ROLLBACK_CHECK_MRN) is None


async def test_client_and_db_session_share_the_same_transaction(client, db_session, unique_email):
    """A row created by a request made through `client` must be visible
    to `db_session` directly — proving the dependency override actually
    routes the app's DB access through the test's own transaction rather
    than a separate, real connection."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": "supersecret123", "role": "ADMIN"},
    )
    assert response.status_code == 201

    user = await UserRepository(db_session).get_by_email(unique_email)
    assert user is not None
