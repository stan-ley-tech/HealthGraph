from datetime import date

import pytest

from app.models.enums import Sex
from app.repositories.patient import PatientRepository

pytestmark = pytest.mark.integration


async def _make_patient(repo, mrn, first_name, last_name):
    patient = repo.create(
        mrn=mrn,
        first_name=first_name,
        last_name=last_name,
        date_of_birth=date(1985, 6, 15),
        sex=Sex.OTHER,
    )
    await repo.session.flush()
    return patient


async def test_create_and_get_by_id(db_session, unique_mrn):
    repo = PatientRepository(db_session)
    created = await _make_patient(repo, unique_mrn, "Ada", "Lovelace")

    fetched = await repo.get_by_id(created.id)

    assert fetched is not None
    assert fetched.mrn == unique_mrn
    assert fetched.first_name == "Ada"


async def test_get_by_id_returns_none_for_unknown_id(db_session):
    import uuid

    repo = PatientRepository(db_session)
    assert await repo.get_by_id(uuid.uuid4()) is None


async def test_get_by_mrn_finds_the_right_patient(db_session, unique_mrn):
    repo = PatientRepository(db_session)
    await _make_patient(repo, unique_mrn, "Grace", "Hopper")

    fetched = await repo.get_by_mrn(unique_mrn)

    assert fetched is not None
    assert fetched.first_name == "Grace"


async def test_get_by_mrn_returns_none_when_not_found(db_session):
    repo = PatientRepository(db_session)
    assert await repo.get_by_mrn("MRN-DOES-NOT-EXIST") is None


async def test_search_filters_by_name_case_insensitively(db_session, unique_mrn):
    repo = PatientRepository(db_session)
    marker = unique_mrn[-8:]
    await _make_patient(repo, unique_mrn, f"Zzy{marker}", "Match")
    await _make_patient(repo, f"{unique_mrn}-B", "Someone", "Else")

    page = await repo.search(name=f"zzy{marker}", first=10, after=None)

    assert [p.first_name for p in page.items] == [f"Zzy{marker}"]


async def test_get_by_ids_batches_a_lookup(db_session, unique_mrn):
    repo = PatientRepository(db_session)
    first = await _make_patient(repo, unique_mrn, "One", "Patient")
    second = await _make_patient(repo, f"{unique_mrn}-2", "Two", "Patient")

    found = await repo.get_by_ids([first.id, second.id])

    assert {p.id for p in found} == {first.id, second.id}


async def test_get_by_ids_returns_empty_list_for_empty_input(db_session):
    repo = PatientRepository(db_session)
    assert await repo.get_by_ids([]) == []
