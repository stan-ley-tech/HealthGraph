"""End-to-end authorization tests for the GraphQL API: a caller's role
plus the PatientProviderRelationship rows in the database determine what
they can see — never the role alone. These exercise the real HTTP
stack (auth headers, context construction, resolver, access policy) in
one pass rather than unit-testing the policy in isolation.
"""

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

_PATIENT_QUERY = "query($id: ID!) { patient(id: $id) { id firstName ssn } }"


async def test_anonymous_caller_is_rejected(client, db_session, unique_mrn):
    patient = await make_patient(db_session, mrn=unique_mrn)

    variables = {"id": str(patient.id)}
    response = await client.post("/graphql", json={"query": _PATIENT_QUERY, "variables": variables})

    body = response.json()
    assert body["data"]["patient"] is None
    assert "Authentication required" in body["errors"][0]["message"]


async def test_admin_can_view_any_patient_including_ssn(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn, ssn="123-45-6789")
    token = await register_and_login(client, email=unique_email, role=Role.ADMIN)

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"]["ssn"] == "123-45-6789"


async def test_doctor_without_a_relationship_is_denied(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)
    user_id = await get_user_id_by_email(db_session, unique_email)
    provider = await make_provider(db_session, npi_number=unique_mrn[-10:].rjust(10, "2"))
    await link_provider_to_user(db_session, provider, user_id)

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"] is None
    assert "active clinical relationship" in body["errors"][0]["message"]


async def test_doctor_with_a_relationship_can_view_but_not_ssn(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn, ssn="999-99-9999")
    token = await register_and_login(client, email=unique_email, role=Role.DOCTOR)
    user_id = await get_user_id_by_email(db_session, unique_email)
    provider = await make_provider(db_session, npi_number=unique_mrn[-10:].rjust(10, "3"))
    await link_provider_to_user(db_session, provider, user_id)
    await make_relationship(db_session, patient_id=patient.id, provider_id=provider.id)

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"]["firstName"] == "Test"
    # Field-level authorization: full record access does not imply SSN access.
    assert body["data"]["patient"]["ssn"] is None


async def test_patient_can_view_their_own_record(client, db_session, unique_mrn, unique_email):
    token = await register_and_login(client, email=unique_email, role=Role.PATIENT)
    user_id = await get_user_id_by_email(db_session, unique_email)
    patient = await make_patient(db_session, mrn=unique_mrn, ssn="111-11-1111")
    patient.user_id = user_id
    await db_session.commit()

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"]["ssn"] == "111-11-1111"


async def test_patient_cannot_view_another_patients_record(
    client, db_session, unique_mrn, unique_email
):
    token = await register_and_login(client, email=unique_email, role=Role.PATIENT)
    other_patient = await make_patient(db_session, mrn=unique_mrn)

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(other_patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"] is None
    assert "active clinical relationship" in body["errors"][0]["message"]


async def test_lab_technician_can_read_lab_results_without_a_relationship(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.LAB_TECHNICIAN)

    query = (
        "query($id: ID!) { patientLabResults(patientId: $id, first: 5) "
        "{ edges { node { id } } } }"
    )
    response = await client.post(
        "/graphql",
        json={"query": query, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert "errors" not in body
    assert body["data"]["patientLabResults"]["edges"] == []


async def test_lab_technician_cannot_read_the_full_patient_record(
    client, db_session, unique_mrn, unique_email
):
    patient = await make_patient(db_session, mrn=unique_mrn)
    token = await register_and_login(client, email=unique_email, role=Role.LAB_TECHNICIAN)

    response = await client.post(
        "/graphql",
        json={"query": _PATIENT_QUERY, "variables": {"id": str(patient.id)}},
        headers=auth_headers(token),
    )

    body = response.json()
    assert body["data"]["patient"] is None
    assert "errors" in body
