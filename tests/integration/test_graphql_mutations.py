import pytest

from app.models.enums import Role
from tests.integration._helpers import (
    auth_headers,
    get_user_id_by_email,
    link_provider_to_user,
    make_patient,
    make_provider,
    make_relationship,
    register_and_login,
)

pytestmark = pytest.mark.integration

_CREATE_PATIENT = """
mutation($input: CreatePatientInput!) {
  createPatient(input: $input) { id mrn }
}
"""


async def test_create_patient_requires_admin(client, unique_email, unique_mrn):
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)

    response = await client.post(
        "/graphql",
        json={
            "query": _CREATE_PATIENT,
            "variables": {
                "input": {
                    "mrn": unique_mrn, "firstName": "X", "lastName": "Y",
                    "dateOfBirth": "1990-01-01", "sex": "OTHER",
                }
            },
        },
        headers=auth_headers(token),
    )

    body = response.json()
    # createPatient's return type is non-null, so a resolver error nulls
    # the whole `data` object (GraphQL's null-propagation rule), not just
    # this field — unlike the nullable `patient` query.
    assert body["data"] is None
    assert "does not permit" in body["errors"][0]["message"]


async def test_create_patient_succeeds_for_admin(client, unique_email, unique_mrn):
    token = await register_and_login(client, email=unique_email, role=Role.ADMIN)

    response = await client.post(
        "/graphql",
        json={
            "query": _CREATE_PATIENT,
            "variables": {
                "input": {
                    "mrn": unique_mrn, "firstName": "Marie", "lastName": "Curie",
                    "dateOfBirth": "1990-01-01", "sex": "FEMALE",
                }
            },
        },
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["createPatient"]["mrn"] == unique_mrn


async def test_create_patient_rejects_a_duplicate_mrn(client, unique_email, unique_mrn):
    token = await register_and_login(client, email=unique_email, role=Role.ADMIN)
    variables = {
        "input": {
            "mrn": unique_mrn, "firstName": "A", "lastName": "B",
            "dateOfBirth": "1990-01-01", "sex": "OTHER",
        }
    }
    first = await client.post(
        "/graphql", json={"query": _CREATE_PATIENT, "variables": variables},
        headers=auth_headers(token),
    )
    assert first.json()["data"]["createPatient"] is not None

    second = await client.post(
        "/graphql", json={"query": _CREATE_PATIENT, "variables": variables},
        headers=auth_headers(token),
    )
    body = second.json()
    assert body["data"] is None
    assert "already exists" in body["errors"][0]["message"]


async def test_prescribe_medication_denied_for_nurse(client, db_session, unique_mrn, unique_email):
    """Domain-specific RBAC beyond the generic 'clinical role' check:
    nurses can record vitals/allergies but not prescribe."""
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.NURSE)
    user_id = await get_user_id_by_email(db_session, unique_email)
    provider = await make_provider(db_session, npi_number=unique_mrn[-10:].rjust(10, "5"))
    await link_provider_to_user(db_session, provider, user_id)
    await make_relationship(db_session, patient_id=patient.id, provider_id=provider.id)

    mutation = """
    mutation($input: PrescribeMedicationInput!) {
      prescribeMedication(input: $input) { id }
    }
    """
    response = await client.post(
        "/graphql",
        json={
            "query": mutation,
            "variables": {
                "input": {
                    "patientId": str(patient.id), "name": "Ibuprofen", "dosage": "200mg",
                    "frequency": "twice daily", "startDate": "2026-01-01",
                }
            },
        },
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"] is None
    assert "does not permit" in body["errors"][0]["message"]


async def test_prescribe_medication_succeeds_for_doctor_with_relationship(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)
    user_id = await get_user_id_by_email(db_session, unique_email)
    provider = await make_provider(db_session, npi_number=unique_mrn[-10:].rjust(10, "6"))
    await link_provider_to_user(db_session, provider, user_id)
    await make_relationship(db_session, patient_id=patient.id, provider_id=provider.id)

    mutation = """
    mutation($input: PrescribeMedicationInput!) {
      prescribeMedication(input: $input) { id name prescribedBy { lastName } }
    }
    """
    response = await client.post(
        "/graphql",
        json={
            "query": mutation,
            "variables": {
                "input": {
                    "patientId": str(patient.id), "name": "Ibuprofen", "dosage": "200mg",
                    "frequency": "twice daily", "startDate": "2026-01-01",
                }
            },
        },
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["prescribeMedication"]["name"] == "Ibuprofen"
    assert body["data"]["prescribeMedication"]["prescribedBy"]["lastName"] == "Provider"


async def test_update_contact_info_denied_for_unrelated_doctor(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)

    mutation = """
    mutation($id: ID!, $input: UpdatePatientContactInfoInput!) {
      updatePatientContactInfo(patientId: $id, input: $input) { phone }
    }
    """
    response = await client.post(
        "/graphql",
        json={
            "query": mutation,
            "variables": {"id": str(patient.id), "input": {"phone": "555-0000"}},
        },
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"] is None
    assert "only update your own" in body["errors"][0]["message"]


async def test_update_contact_info_succeeds_for_self(client, db_session, unique_mrn, unique_email):
    token = await register_and_login(client, email=unique_email, role=Role.PATIENT)
    user_id = await get_user_id_by_email(db_session, unique_email)
    patient = await make_patient(db_session, mrn=unique_mrn)
    patient.user_id = user_id
    await db_session.commit()

    mutation = """
    mutation($id: ID!, $input: UpdatePatientContactInfoInput!) {
      updatePatientContactInfo(patientId: $id, input: $input) { phone }
    }
    """
    response = await client.post(
        "/graphql",
        json={
            "query": mutation,
            "variables": {"id": str(patient.id), "input": {"phone": "555-0000"}},
        },
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["updatePatientContactInfo"]["phone"] == "555-0000"
