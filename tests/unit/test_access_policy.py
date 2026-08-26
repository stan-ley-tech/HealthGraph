"""Unit tests for PatientAccessPolicy against a fake relationship
repository — no database needed, since the policy's branching logic on
role is exactly what's under test, not the SQL behind it (that's covered
by the integration tests for PatientProviderRelationshipRepository)."""

import uuid

import pytest

from app.domain.access_policy import PatientAccessPolicy
from app.domain.current_user import CurrentUser
from app.models.enums import Role

pytestmark = pytest.mark.unit


class FakeRelationshipRepository:
    def __init__(self, active_pairs: set[tuple[uuid.UUID, uuid.UUID]]) -> None:
        self._active_pairs = active_pairs

    async def has_active_relationship(self, *, patient_id, provider_id) -> bool:
        return (patient_id, provider_id) in self._active_pairs

    async def bulk_has_active_relationship(self, *, provider_id, patient_ids) -> set:
        return {pid for pid in patient_ids if (pid, provider_id) in self._active_pairs}


@pytest.fixture
def patient_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def provider_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def policy_with_relationship(patient_id, provider_id) -> PatientAccessPolicy:
    return PatientAccessPolicy(FakeRelationshipRepository({(patient_id, provider_id)}))


@pytest.fixture
def policy_without_relationship() -> PatientAccessPolicy:
    return PatientAccessPolicy(FakeRelationshipRepository(set()))


async def test_admin_can_view_any_patient(policy_without_relationship, patient_id):
    admin = CurrentUser(user_id=uuid.uuid4(), role=Role.ADMIN)
    assert await policy_without_relationship.can_view_full_record(admin, patient_id) is True


async def test_patient_can_view_their_own_record(policy_without_relationship, patient_id):
    patient_user = CurrentUser(user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=patient_id)
    assert await policy_without_relationship.can_view_full_record(patient_user, patient_id) is True


async def test_patient_cannot_view_another_patients_record(
    policy_without_relationship, patient_id
):
    other_patient = CurrentUser(user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=uuid.uuid4())
    result = await policy_without_relationship.can_view_full_record(other_patient, patient_id)
    assert result is False


async def test_doctor_with_active_relationship_can_view_the_record(
    policy_with_relationship, patient_id, provider_id
):
    doctor = CurrentUser(user_id=uuid.uuid4(), role=Role.DOCTOR, provider_id=provider_id)
    assert await policy_with_relationship.can_view_full_record(doctor, patient_id) is True


async def test_doctor_without_a_relationship_cannot_view_the_record(
    policy_without_relationship, patient_id, provider_id
):
    doctor = CurrentUser(user_id=uuid.uuid4(), role=Role.DOCTOR, provider_id=provider_id)
    assert await policy_without_relationship.can_view_full_record(doctor, patient_id) is False


async def test_doctor_with_no_provider_profile_is_denied(policy_without_relationship, patient_id):
    doctor = CurrentUser(user_id=uuid.uuid4(), role=Role.DOCTOR, provider_id=None)
    assert await policy_without_relationship.can_view_full_record(doctor, patient_id) is False


async def test_lab_technician_can_view_lab_results_without_a_relationship(
    policy_without_relationship, patient_id
):
    lab_tech = CurrentUser(user_id=uuid.uuid4(), role=Role.LAB_TECHNICIAN, provider_id=uuid.uuid4())
    assert await policy_without_relationship.can_view_lab_results(lab_tech, patient_id) is True


async def test_lab_technician_cannot_view_the_full_record(policy_without_relationship, patient_id):
    lab_tech = CurrentUser(user_id=uuid.uuid4(), role=Role.LAB_TECHNICIAN, provider_id=uuid.uuid4())
    assert await policy_without_relationship.can_view_full_record(lab_tech, patient_id) is False


def test_only_admin_and_self_can_view_ssn(policy_without_relationship, patient_id, provider_id):
    admin = CurrentUser(user_id=uuid.uuid4(), role=Role.ADMIN)
    self_patient = CurrentUser(user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=patient_id)
    other_patient = CurrentUser(user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=uuid.uuid4())
    doctor = CurrentUser(user_id=uuid.uuid4(), role=Role.DOCTOR, provider_id=provider_id)

    assert policy_without_relationship.can_view_ssn(admin, patient_id) is True
    assert policy_without_relationship.can_view_ssn(self_patient, patient_id) is True
    assert policy_without_relationship.can_view_ssn(other_patient, patient_id) is False
    # Even a doctor with full clinical access to this patient does not
    # automatically get the SSN field — that's the point of field-level
    # authorization being separate from object-level access.
    assert policy_without_relationship.can_view_ssn(doctor, patient_id) is False


async def test_filter_accessible_patient_ids_for_admin_returns_everything(
    policy_without_relationship,
):
    admin = CurrentUser(user_id=uuid.uuid4(), role=Role.ADMIN)
    ids = {uuid.uuid4(), uuid.uuid4(), uuid.uuid4()}
    assert await policy_without_relationship.filter_accessible_patient_ids(admin, ids) == ids


async def test_filter_accessible_patient_ids_for_patient_returns_only_self(
    policy_without_relationship, patient_id
):
    patient_user = CurrentUser(user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=patient_id)
    ids = {patient_id, uuid.uuid4(), uuid.uuid4()}
    result = await policy_without_relationship.filter_accessible_patient_ids(patient_user, ids)
    assert result == {patient_id}


async def test_filter_accessible_patient_ids_for_doctor_uses_bulk_relationship_check(
    policy_with_relationship, patient_id, provider_id
):
    doctor = CurrentUser(user_id=uuid.uuid4(), role=Role.DOCTOR, provider_id=provider_id)
    ids = {patient_id, uuid.uuid4()}
    result = await policy_with_relationship.filter_accessible_patient_ids(doctor, ids)
    assert result == {patient_id}
