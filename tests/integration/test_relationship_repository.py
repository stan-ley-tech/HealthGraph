from datetime import date

import pytest

from app.models.enums import FacilityType, RelationshipType, Sex
from app.models.facility import Facility
from app.models.provider import Provider
from app.repositories.patient import PatientRepository
from app.repositories.relationship import PatientProviderRelationshipRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_patients_one_provider(db_session, unique_mrn):
    patients = PatientRepository(db_session)
    patient_a = patients.create(
        mrn=unique_mrn, first_name="A", last_name="Patient",
        date_of_birth=date(1990, 1, 1), sex=Sex.OTHER,
    )
    patient_b = patients.create(
        mrn=f"{unique_mrn}-B", first_name="B", last_name="Patient",
        date_of_birth=date(1990, 1, 1), sex=Sex.OTHER,
    )
    await db_session.flush()

    facility = Facility(name="Test Facility", facility_type=FacilityType.CLINIC)
    db_session.add(facility)
    await db_session.flush()

    provider = Provider(
        facility_id=facility.id, first_name="Test", last_name="Provider",
        npi_number=unique_mrn[-10:].rjust(10, "1"),
    )
    db_session.add(provider)
    await db_session.flush()

    return patient_a, patient_b, provider


async def test_has_active_relationship_is_true_once_created(db_session, two_patients_one_provider):
    patient_a, _patient_b, provider = two_patients_one_provider
    repo = PatientProviderRelationshipRepository(db_session)

    repo.create(
        patient_id=patient_a.id, provider_id=provider.id,
        relationship_type=RelationshipType.PRIMARY_CARE, start_date=date(2026, 1, 1),
    )
    await db_session.flush()

    assert await repo.has_active_relationship(patient_id=patient_a.id, provider_id=provider.id)


async def test_has_active_relationship_is_false_when_none_exists(
    db_session, two_patients_one_provider
):
    _patient_a, patient_b, provider = two_patients_one_provider
    repo = PatientProviderRelationshipRepository(db_session)

    assert not await repo.has_active_relationship(patient_id=patient_b.id, provider_id=provider.id)


async def test_accessible_patient_ids_returns_only_related_patients(
    db_session, two_patients_one_provider
):
    patient_a, _patient_b, provider = two_patients_one_provider
    repo = PatientProviderRelationshipRepository(db_session)
    repo.create(
        patient_id=patient_a.id, provider_id=provider.id,
        relationship_type=RelationshipType.PRIMARY_CARE, start_date=date(2026, 1, 1),
    )
    await db_session.flush()

    accessible = await repo.accessible_patient_ids(provider_id=provider.id)

    assert accessible == {patient_a.id}


async def test_bulk_has_active_relationship_checks_many_patients_in_one_call(
    db_session, two_patients_one_provider
):
    patient_a, patient_b, provider = two_patients_one_provider
    repo = PatientProviderRelationshipRepository(db_session)
    repo.create(
        patient_id=patient_a.id, provider_id=provider.id,
        relationship_type=RelationshipType.PRIMARY_CARE, start_date=date(2026, 1, 1),
    )
    await db_session.flush()

    result = await repo.bulk_has_active_relationship(
        provider_id=provider.id, patient_ids=[patient_a.id, patient_b.id]
    )

    assert result == {patient_a.id}


async def test_bulk_has_active_relationship_handles_empty_input(
    db_session, two_patients_one_provider
):
    _patient_a, _patient_b, provider = two_patients_one_provider
    repo = PatientProviderRelationshipRepository(db_session)

    assert await repo.bulk_has_active_relationship(provider_id=provider.id, patient_ids=[]) == set()
