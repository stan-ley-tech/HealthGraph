"""Correctness tests for the keyset (cursor) pagination in
`app.repositories.pagination`, exercised through `EncounterRepository`.

The property under test: walking every page front-to-back with a small
page size must yield exactly the same set of rows, in the same order, as
one unpaginated query — no duplicates, no gaps, and `hasNextPage` must
turn false exactly at the end.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import EncounterType, FacilityType, Sex
from app.repositories.clinical import EncounterRepository
from app.repositories.patient import PatientRepository
from app.repositories.provider import FacilityRepository, ProviderRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def patient_with_many_encounters(db_session, unique_mrn):
    patients = PatientRepository(db_session)
    facilities = FacilityRepository(db_session)
    providers = ProviderRepository(db_session)
    encounters = EncounterRepository(db_session)

    from datetime import date

    patient = patients.create(
        mrn=unique_mrn, first_name="Paginated", last_name="Patient",
        date_of_birth=date(1990, 1, 1), sex=Sex.OTHER,
    )
    await db_session.flush()

    from app.models.facility import Facility
    from app.models.provider import Provider

    facility = facilities.add(
        Facility(name="Test Hospital", facility_type=FacilityType.HOSPITAL)
    )
    await db_session.flush()
    provider = providers.add(
        Provider(
            facility_id=facility.id, first_name="Test", last_name="Provider",
            npi_number=unique_mrn[-10:].rjust(10, "0"),
        )
    )
    await db_session.flush()

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(23):
        encounters.create(
            patient_id=patient.id,
            provider_id=provider.id,
            facility_id=facility.id,
            encounter_type=EncounterType.OUTPATIENT,
            chief_complaint=f"Visit {i}",
            started_at=base_time + timedelta(days=i),
        )
    await db_session.flush()
    return patient


async def test_paginating_through_every_page_covers_every_row_exactly_once(
    db_session, patient_with_many_encounters
):
    repo = EncounterRepository(db_session)
    patient_id = patient_with_many_encounters.id

    seen_ids: list = []
    after = None
    page_count = 0
    while True:
        page = await repo.paginate_by_patient(patient_id, first=7, after=after)
        seen_ids.extend(item.id for item in page.items)
        page_count += 1
        if not page.has_next_page:
            break
        after = page.end_cursor
        assert page_count < 20, "pagination did not terminate"

    assert len(seen_ids) == 23
    assert len(set(seen_ids)) == 23, "pagination returned a duplicate row"
    assert page_count == 4  # 7 + 7 + 7 + 2


async def test_pages_are_ordered_most_recent_first(db_session, patient_with_many_encounters):
    repo = EncounterRepository(db_session)
    page = await repo.paginate_by_patient(patient_with_many_encounters.id, first=5, after=None)

    started_ats = [item.started_at for item in page.items]
    assert started_ats == sorted(started_ats, reverse=True)
    # The first page should hold the *latest* encounters (day 22 down to 18).
    assert page.items[0].chief_complaint == "Visit 22"


async def test_full_pagination_matches_single_unpaginated_query(
    db_session, patient_with_many_encounters
):
    repo = EncounterRepository(db_session)
    patient_id = patient_with_many_encounters.id

    paginated_ids: list = []
    after = None
    while True:
        page = await repo.paginate_by_patient(patient_id, first=10, after=after)
        paginated_ids.extend(item.id for item in page.items)
        if not page.has_next_page:
            break
        after = page.end_cursor

    grouped = await repo.list_by_patient_ids([patient_id])
    unpaginated_ids = [item.id for item in grouped[patient_id]]

    assert paginated_ids == unpaginated_ids


async def test_empty_result_set_has_no_next_page(db_session):
    import uuid

    repo = EncounterRepository(db_session)
    page = await repo.paginate_by_patient(uuid.uuid4(), first=10, after=None)

    assert page.items == []
    assert page.has_next_page is False
    assert page.start_cursor is None
    assert page.end_cursor is None
