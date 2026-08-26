# Testing Strategy

98 tests across three layers, run against a real PostgreSQL and Redis —
not mocks — because the properties worth verifying here (query batching,
transaction rollback behavior, cache round trips, rate-limit windows)
are exactly the ones a mock would assume away. Coverage sits at ~91% of
`app/` (`pytest --cov=app --cov-fail-under=80` gates CI at 80%).

```bash
docker compose up -d postgres redis
export DATABASE_URL=postgresql+asyncpg://healthgraph:healthgraph@localhost:5432/healthgraph
alembic upgrade head
pytest                                    # everything
pytest -m unit                            # fast, no I/O
pytest -m integration                     # real Postgres/Redis
pytest -m e2e                             # full HTTP-stack workflows
pytest --cov=app --cov-report=term-missing
```

## Layers

**`tests/unit/`** — pure logic, no database or network. Password
hashing, JWT claim validation and rejection (expired, forged, wrong
token type), input validators, cursor encode/decode, and
`PatientAccessPolicy`'s role/relationship branching (against a fake
relationship repository — a plain Python object implementing the same
two async methods, not a mocking framework, since the policy only needs
that narrow an interface).

**`tests/integration/`** — real Postgres and Redis, one test each for:
repository CRUD and search, keyset-pagination correctness (walking every
page of a 23-row set and asserting the union equals one unpaginated
query, with no duplicates and no gaps), the authorization backbone
(`PatientProviderRelationshipRepository`), cache-aside hit/miss/
invalidate against real Redis, the sliding-window rate limiter, the full
REST auth flow (register/login/refresh-rotation/logout), and the GraphQL
authorization matrix (every role × relationship combination against the
real HTTP stack) and mutation RBAC.

**`tests/e2e/`** — one full workflow, start to finish, through the real
HTTP stack: an admin creates a patient, a doctor is denied before a
relationship exists, the admin grants it, the doctor records an
encounter/diagnosis/prescription/vital sign, a lab technician records a
result, and `patientTimeline` shows all five event types correctly
merged and ordered — while a second, unrelated doctor still sees
nothing. This is what proves the narrower tests' pieces actually fit
together, not just that each one works in isolation.

**N+1 regression** (`tests/integration/test_n_plus_one.py`) deserves
calling out separately: it attaches a SQLAlchemy `before_cursor_execute`
listener to count real queries during a `patients { encounters }`
GraphQL request at two different page sizes, and fails if the count
differs. This is the test that would actually catch a future resolver
reaching for a repository directly instead of a DataLoader — a
regression that unit tests on the policy or repository layer alone
cannot see, because the bug only exists in how they're *composed*.

## Why transactional rollback, not a throwaway database

Every test gets its own PostgreSQL connection, wrapped in an outer
transaction; `AsyncSession(..., join_transaction_mode="create_savepoint")`
means code under test can call `session.commit()` (as real mutations do)
and it only releases a savepoint — the outer rollback at teardown still
discards everything. This gets test isolation without the overhead of
recreating the schema per test, and — critically — without silently
diverging from what the mutation code actually does in production. The
one deliberate exception is `AuditService`, which opens its own
connection by design (see [ARCHITECTURE.md](ARCHITECTURE.md)); the test
fixture patches its session factory to join the same transaction, so
audit writes get exercised for real and still roll back cleanly.

See `tests/conftest.py` for the fixture implementation and the comments
explaining two Windows/asyncpg-specific event-loop issues it works
around (a per-test engine instead of the app's shared one, and reading
the Redis client through its module rather than a captured reference) —
both amount to "don't let a long-lived async resource outlive the event
loop that created it."

## What's not covered

Load/soak testing, and any test that would require a second Postgres
major version or a multi-node Redis setup — out of scope for a system
this size. `scripts/benchmark.py` covers the performance claim
(N+1 vs. batched) with real measurements rather than an assertion in the
test suite, since "how much faster" is a number to report, not a
pass/fail check.
