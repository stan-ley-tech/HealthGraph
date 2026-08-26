# API Reference

Two surfaces: GraphQL for patient data (`/graphql`), REST for
authentication and system administration (`/api/v1/...`). The full
generated schema — every type, field, argument, and description — is in
[docs/schema.graphql](docs/schema.graphql); this document covers how to
use it rather than repeating it field by field.

## Authentication

Every GraphQL request and every non-auth REST request expects
`Authorization: Bearer <access_token>`.

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "doc@example.com", "password": "supersecret123", "role": "DOCTOR"}'

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "doc@example.com", "password": "supersecret123"}'
# => {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}

curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "..."}'

curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "..."}'
```

Access tokens expire in 15 minutes; refresh tokens rotate on every use
(the old one stops working the moment a new one is issued) and last 7
days. See [SECURITY.md](SECURITY.md) for the reasoning.

## GraphQL

### Queries

| Query | Purpose | Access |
|---|---|---|
| `patient(id)` | A single patient's demographics | Full-record access required |
| `patients(name, first, after)` | Cursor-paginated search, scoped to the caller | See [SECURITY.md](SECURITY.md) |
| `patientEncounters(patientId, first, after)` | A patient's encounters | Full-record access required |
| `patientMedications(patientId, first, after)` | A patient's medications | Full-record access required |
| `patientLabResults(patientId, first, after)` | A patient's lab results | Broader — `LAB_TECHNICIAN` included |
| `patientTimeline(patientId, limit)` | Encounters, diagnoses, medications, labs, procedures, and vitals merged into one reverse-chronological feed | Full-record access required |

`Patient` also exposes `encounters`, `medications`, `labResults`, and
`timeline` as nested fields (each DataLoader-batched — see
[PERFORMANCE.md](PERFORMANCE.md)), so a `patients` list can pull each
patient's history in one extra query rather than looping client-side.

**The flagship query** — a patient's full clinical history in one
reverse-chronological feed:

```graphql
query PatientTimeline($patientId: ID!) {
  patientTimeline(patientId: $patientId, limit: 50) {
    occurredAt
    event {
      __typename
      ... on Encounter { encounterType chiefComplaint provider { firstName lastName } }
      ... on Diagnosis { icd10Code description status }
      ... on Medication { name dosage frequency }
      ... on LabResult { testName value unit abnormalFlag }
      ... on Procedure { cptCode description }
      ... on VitalSign { vitalType value unit }
    }
  }
}
```

`event` is a GraphQL union (`TimelineEvent`); clients discriminate on
`__typename` and select fields per concrete type via inline fragments,
same as any polymorphic GraphQL field.

**Cursor pagination**, used identically across every list field:

```graphql
query PatientEncounters($patientId: ID!, $after: String) {
  patientEncounters(patientId: $patientId, first: 20, after: $after) {
    edges { cursor node { encounterType startedAt chiefComplaint } }
    pageInfo { hasNextPage hasPreviousPage endCursor }
  }
}
```

Pass the previous response's `pageInfo.endCursor` as `after` to fetch the
next page; stop when `hasNextPage` is `false`.

### Mutations

| Mutation | Who | Notes |
|---|---|---|
| `createPatient` | `ADMIN` | |
| `updatePatientContactInfo` | `ADMIN` or the patient themselves | Invalidates the patient's cache entry |
| `addPatientProviderRelationship` | `ADMIN` | The only way a `DOCTOR`/`NURSE` gains access to a patient |
| `createEncounter` | `DOCTOR`/`NURSE`/`ADMIN` | Requires an active relationship with the patient |
| `addDiagnosis` | `DOCTOR`/`NURSE`/`ADMIN` | Requires an active relationship |
| `prescribeMedication` | `DOCTOR`/`ADMIN` | **Not** `NURSE` — modeling prescriptive authority |
| `recordLabResult` | `LAB_TECHNICIAN`/`ADMIN` | No relationship required (see [SECURITY.md](SECURITY.md)) |
| `recordVitalSign` | `DOCTOR`/`NURSE`/`ADMIN` | Requires an active relationship |
| `addAllergy` | `DOCTOR`/`NURSE`/`ADMIN` | Requires an active relationship |

```graphql
mutation PrescribeMedication($input: PrescribeMedicationInput!) {
  prescribeMedication(input: $input) {
    id
    name
    dosage
    prescribedBy { firstName lastName }
  }
}
```

```json
{
  "input": {
    "patientId": "…",
    "name": "Lisinopril",
    "dosage": "10mg",
    "frequency": "once daily",
    "startDate": "2026-01-01"
  }
}
```

A rejected mutation returns `data: null` at the top level (not just the
mutation field) whenever its return type is non-null, per standard
GraphQL null-propagation — check `errors[0].message` for the reason
(`"Your role does not permit this action."`,
`"You do not have an active clinical relationship with this patient."`,
etc.).

### Query cost limits

Requests exceeding depth, alias-count, or complexity limits are rejected
before touching the database — see
[SECURITY.md#graphql-specific-hardening](SECURITY.md#graphql-specific-hardening).
Defaults: max depth 10, max complexity 1000, configurable via
`GRAPHQL_MAX_QUERY_DEPTH` / `GRAPHQL_MAX_QUERY_COMPLEXITY`.

## REST

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/v1/auth/register` | POST | none | Create a user |
| `/api/v1/auth/login` | POST | none | Issue an access/refresh token pair |
| `/api/v1/auth/refresh` | POST | none (refresh token in body) | Rotate to a new token pair |
| `/api/v1/auth/logout` | POST | none (refresh token in body) | Revoke a refresh token |
| `/api/v1/admin/users` | GET | `ADMIN` | List users |
| `/api/v1/admin/facilities` | GET, POST | `ADMIN` | List / create facilities |
| `/api/v1/admin/audit-logs` | GET | `ADMIN` | Cursor-paginated audit trail |
| `/health` | GET | none | Liveness check |

Every error — REST or GraphQL-adjacent — renders through the same
envelope: `{"error": {"code": "...", "message": "..."}}`. See
[SECURITY.md#structured-error-handling](SECURITY.md#structured-error-handling).

## Interactive exploration

With the server running, `POST /graphql` serves GraphiQL in a browser
(Strawberry's default), and `/docs` serves FastAPI's interactive Swagger
UI for the REST endpoints.
