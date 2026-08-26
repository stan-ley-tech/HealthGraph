# HealthGraph

![CI](https://github.com/stan-ley-tech/HealthGraph/actions/workflows/ci.yml/badge.svg)

A healthcare data aggregation backend that exposes a unified GraphQL API
over patient records collected from disparate clinical services
(encounters, diagnoses, medications, allergies, lab results, vital
signs, procedures). Built to exercise the parts of backend engineering
that a plain CRUD API glosses over: N+1 query batching, cache
invalidation, per-object and per-field authorization, and query-cost
control on a GraphQL surface that spans a real relational schema.

## Why this exists

Most "healthcare API" side projects stop at exposing tables over
GraphQL. This one treats the interesting problems as the point:

- A doctor querying `patient(id)` does not see every patient in the
  system — access is checked per patient, against an actual clinical
  relationship, not just per endpoint. See [SECURITY.md](SECURITY.md).
- `patientTimeline` merges six different record types (encounters,
  diagnoses, medications, lab results, procedures, vital signs) into one
  chronological feed without N+1 fan-out, using per-request DataLoaders
  — and a regression test asserts the query count stays constant as the
  page size grows. See [PERFORMANCE.md](PERFORMANCE.md).
- GraphQL queries are rejected on depth, alias count, and a pagination-
  aware complexity estimate before a resolver ever runs.
- Every authentication event and every patient-data mutation is audited,
  through a connection independent of the request's own transaction, so
  the audit trail survives even when the triggering request doesn't.
- `Patient.ssn` is withheld from a doctor with full record access to
  that patient — field-level authorization is a distinct check from
  object-level access, not a byproduct of it.

## Stack

- **API**: FastAPI + Strawberry GraphQL, REST for auth and system administration
- **Data**: PostgreSQL via SQLAlchemy 2.0 (async) + Alembic migrations
- **Cache**: Redis (cache-aside patient reads, sliding-window rate limiting)
- **Auth**: JWT access/refresh tokens (rotation on refresh), RBAC + patient-scoped authorization
- **Testing**: Pytest — unit, integration (real Postgres/Redis), and end-to-end
- **Infra**: Docker Compose, GitHub Actions CI

## Documentation

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System diagram, ERD, directory layout, request lifecycle |
| [API.md](API.md) | GraphQL queries/mutations with examples, REST endpoints |
| [SECURITY.md](SECURITY.md) | Auth, RBAC, the patient-access policy, rate limiting, audit trail |
| [PERFORMANCE.md](PERFORMANCE.md) | N+1 benchmark results, indexing, pagination, caching |
| [TESTING.md](TESTING.md) | Test layers, the transactional-isolation fixture, what's not covered |
| [docs/schema.graphql](docs/schema.graphql) | The full generated GraphQL SDL |

## Quickstart

```bash
git clone https://github.com/stan-ley-tech/HealthGraph.git
cd HealthGraph
cp .env.example .env

docker compose up -d postgres redis
pip install -r requirements-dev.txt
alembic upgrade head
python -m scripts.seed          # demo facilities, providers, and patients

uvicorn app.main:app --reload
```

Then open `http://localhost:8000/graphql` for GraphiQL, or
`http://localhost:8000/docs` for the REST API's Swagger UI. The seed
script prints demo login credentials (an admin, two doctors, a nurse, a
lab technician, and three patient-role accounts) — see
[scripts/seed.py](scripts/seed.py) or its own console output.

Alternatively, `docker compose up --build` runs the whole stack
(Postgres, Redis, and the API, migrations applied automatically) without
a local Python install.

### Running the tests

```bash
pytest                                     # all 98 tests
pytest --cov=app --cov-report=term-missing # with coverage (~91%)
python -m scripts.benchmark                # N+1 vs. batched query benchmark
```

Requires a running Postgres and Redis (see Quickstart above) — the test
suite runs against real instances of both, wrapped in a transaction
that's rolled back per test. See [TESTING.md](TESTING.md).

## Example roles

```
ADMIN            full access
DOCTOR           patients they have an active relationship with; can prescribe
NURSE            patients they have an active relationship with; cannot prescribe
LAB_TECHNICIAN   lab results for any patient; nothing else
PATIENT          their own record only
```

## License

MIT — see [LICENSE](LICENSE).
