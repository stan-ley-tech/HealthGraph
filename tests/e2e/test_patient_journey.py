"""A full, realistic workflow exercised start to finish through the real
HTTP stack: an admin sets up a facility, a provider, and a patient; a
doctor with a clinical relationship records a visit's worth of clinical
data across every entity type; and the resulting `patientTimeline` shows
all of it, correctly merged and ordered — while an unrelated doctor sees
none of it. This is the scenario the rest of the test suite's narrower
tests (unit, repository, single-mutation) exist to support; this test is
what proves those pieces actually fit together.
"""

import pytest

from app.models.enums import Role
from tests.integration._helpers import (
    auth_headers,
    get_user_id_by_email,
    link_provider_to_user,
    make_provider,
    register_and_login,
)

pytestmark = pytest.mark.e2e


async def test_full_patient_journey(client, db_session, unique_mrn, unique_email):
    admin_token = await register_and_login(client, email=unique_email, role=Role.ADMIN)
    admin_headers = auth_headers(admin_token)

    # --- Admin creates the patient ---
    create_patient = """
    mutation($input: CreatePatientInput!) {
      createPatient(input: $input) { id }
    }
    """
    response = await client.post(
        "/graphql",
        json={
            "query": create_patient,
            "variables": {
                "input": {
                    "mrn": unique_mrn, "firstName": "Journey", "lastName": "Patient",
                    "dateOfBirth": "1985-06-15", "sex": "FEMALE",
                }
            },
        },
        headers=admin_headers,
    )
    body = response.json()
    assert "errors" not in body, body
    patient_id = body["data"]["createPatient"]["id"]

    # --- A doctor and their provider profile ---
    doctor_email = f"doctor-{unique_email}"
    doctor_token = await register_and_login(client, email=doctor_email, role=Role.DOCTOR)
    doctor_user_id = await get_user_id_by_email(db_session, doctor_email)
    provider = await make_provider(db_session, npi_number=unique_mrn[-10:].rjust(10, "8"))
    await link_provider_to_user(db_session, provider, doctor_user_id)
    doctor_headers = auth_headers(doctor_token)

    # --- An unrelated doctor, for the negative case at the end ---
    outsider_token = await register_and_login(
        client, email=f"outsider-{unique_email}", role=Role.DOCTOR
    )
    outsider_headers = auth_headers(outsider_token)

    # --- Before any relationship exists, the doctor is denied ---
    patient_query = "query($id: ID!) { patient(id: $id) { firstName } }"
    denied = await client.post(
        "/graphql", json={"query": patient_query, "variables": {"id": patient_id}},
        headers=doctor_headers,
    )
    assert denied.json()["data"]["patient"] is None

    # --- Admin establishes the relationship ---
    add_relationship = """
    mutation($input: AddPatientProviderRelationshipInput!) {
      addPatientProviderRelationship(input: $input) { relationshipType active }
    }
    """
    rel_response = await client.post(
        "/graphql",
        json={
            "query": add_relationship,
            "variables": {
                "input": {
                    "patientId": patient_id, "providerId": str(provider.id),
                    "relationshipType": "PRIMARY_CARE",
                }
            },
        },
        headers=admin_headers,
    )
    assert rel_response.json()["data"]["addPatientProviderRelationship"]["active"] is True

    # --- Now the doctor can see the (still mostly empty) record ---
    allowed = await client.post(
        "/graphql", json={"query": patient_query, "variables": {"id": patient_id}},
        headers=doctor_headers,
    )
    assert allowed.json()["data"]["patient"]["firstName"] == "Journey"

    # --- The doctor records an encounter ---
    create_encounter = """
    mutation($input: CreateEncounterInput!) {
      encounter: createEncounter(input: $input) { id }
    }
    """
    encounter_response = await client.post(
        "/graphql",
        json={
            "query": create_encounter,
            "variables": {
                "input": {
                    "patientId": patient_id, "encounterType": "OUTPATIENT",
                    "chiefComplaint": "Annual physical", "startedAt": "2026-03-01T09:00:00+00:00",
                }
            },
        },
        headers=doctor_headers,
    )
    encounter_id = encounter_response.json()["data"]["encounter"]["id"]

    # --- ...a diagnosis, a prescription, a lab result, and a vital sign ---
    await client.post(
        "/graphql",
        json={
            "query": "mutation($i: AddDiagnosisInput!) { addDiagnosis(input: $i) { id } }",
            "variables": {
                "i": {
                    "patientId": patient_id, "icd10Code": "I10",
                    "description": "Essential hypertension", "encounterId": encounter_id,
                    "diagnosedAt": "2026-03-01T09:15:00+00:00",
                }
            },
        },
        headers=doctor_headers,
    )
    await client.post(
        "/graphql",
        json={
            "query": (
                "mutation($i: PrescribeMedicationInput!) "
                "{ prescribeMedication(input: $i) { id } }"
            ),
            "variables": {
                "i": {
                    "patientId": patient_id, "name": "Lisinopril", "dosage": "10mg",
                    "frequency": "once daily", "startDate": "2026-03-01",
                    "encounterId": encounter_id,
                }
            },
        },
        headers=doctor_headers,
    )
    await client.post(
        "/graphql",
        json={
            "query": (
                "mutation($i: RecordVitalSignInput!) { recordVitalSign(input: $i) { id } }"
            ),
            "variables": {
                "i": {
                    "patientId": patient_id, "vitalType": "HEART_RATE", "value": 72.0,
                    "unit": "bpm", "recordedAt": "2026-03-01T09:05:00+00:00",
                    "encounterId": encounter_id,
                }
            },
        },
        headers=doctor_headers,
    )

    lab_tech_token = await register_and_login(
        client, email=f"labtech-{unique_email}", role=Role.LAB_TECHNICIAN
    )
    await client.post(
        "/graphql",
        json={
            "query": (
                "mutation($i: RecordLabResultInput!) { recordLabResult(input: $i) { id } }"
            ),
            "variables": {
                "i": {
                    "patientId": patient_id, "orderedByProviderId": str(provider.id),
                    "testName": "Hemoglobin A1c", "testCode": "4548-4", "value": "5.4",
                    "resultedAt": "2026-03-02T08:00:00+00:00", "encounterId": encounter_id,
                }
            },
        },
        headers=auth_headers(lab_tech_token),
    )

    # --- The timeline shows everything, newest first ---
    timeline_query = """
    query($id: ID!) {
      patientTimeline(patientId: $id) { occurredAt event { __typename } }
    }
    """
    timeline_response = await client.post(
        "/graphql", json={"query": timeline_query, "variables": {"id": patient_id}},
        headers=doctor_headers,
    )
    entries = timeline_response.json()["data"]["patientTimeline"]
    kinds = [entry["event"]["__typename"] for entry in entries]

    assert set(kinds) == {"Encounter", "Diagnosis", "Medication", "VitalSign", "LabResult"}
    occurred_ats = [entry["occurredAt"] for entry in entries]
    assert occurred_ats == sorted(occurred_ats, reverse=True)

    # --- An unrelated doctor still sees nothing, even now that the
    # record is fully populated ---
    outsider_response = await client.post(
        "/graphql", json={"query": timeline_query, "variables": {"id": patient_id}},
        headers=outsider_headers,
    )
    outsider_body = outsider_response.json()
    # patientTimeline's return type is non-null, so an authorization
    # error nulls the whole `data` object rather than just this field.
    assert outsider_body["data"] is None
    assert "active clinical relationship" in outsider_body["errors"][0]["message"]
