# Performance

This system treats performance as a design constraint, not an
afterthought bolted on at the end. This document covers each mechanism
and, for the one that matters most, actual measured numbers.

## N+1 prevention: DataLoader batching

**The problem.** A GraphQL field like `Patient.encounters` resolves once
per `Patient` object in the result. Resolve it naively — reach for
`EncounterRepository` directly inside the field resolver — and a
20-patient page turns into 20 separate `SELECT ... WHERE patient_id = ?`
queries. `patientTimeline` makes this worse: it merges six entity types,
so a naive implementation would run six queries *per patient*.

**The fix.** Every patient-scoped entity has a batched load function
(`app/repositories/base.py::PatientScopedRepository.list_by_patient_ids`)
that takes a list of patient ids and returns one grouped dict from a
single `WHERE patient_id IN (...)` query. `app/api/graphql/dataloaders.py`
wraps each one in a `strawberry.dataloader.DataLoader`, built fresh per
GraphQL request. Because GraphQL resolves sibling fields concurrently,
every `Patient.encounters` resolver across a whole page of patients calls
`.load(patient_id)` before any of them actually awaits a result —
DataLoader coalesces all of those calls into one batch, one query,
regardless of page size.

`patientTimeline` (`app/services/timeline_service.py`) fans out six
DataLoader calls via `asyncio.gather`, then merge-sorts the results in
Python. Requesting the timeline for 20 patients in one query costs 6
queries total (one per entity type, covering all 20 patients), not 120.

### Benchmark: naive per-patient queries vs. one batched query

Run with `python -m scripts.benchmark` (seeds data inside a transaction
that's rolled back afterward — safe to run against any environment,
including a shared dev database). Sample results, 5 encounters per
patient, against a local Postgres:

```
approach                patients   queries   time (ms)  ms/patient
------------------------------------------------------------------
naive (N+1)                   10        10       30.03       3.003
batched (DataLoader)          10         1        9.70       0.970
naive (N+1)                   50        50      166.63       3.333
batched (DataLoader)          50         1       15.29       0.306
naive (N+1)                  200       200      566.24       2.831
batched (DataLoader)         200         1       80.41       0.402
naive (N+1)                 1000      1000     3860.33       3.860
batched (DataLoader)        1000         1      302.19       0.302

At 10 patients: 10 queries -> 1 query, 3.1x faster
At 50 patients: 50 queries -> 1 query, 10.9x faster
At 200 patients: 200 queries -> 1 query, 7.0x faster
At 1000 patients: 1000 queries -> 1 query, 12.8x faster
```

The naive approach's per-patient cost stays roughly flat (~3ms) because
each query pays the same fixed round-trip overhead regardless of how
little data it returns — that overhead is the entire point of batching
away. The batched approach's total time grows sublinearly with patient
count (result-set size dominates, not round trips), which is why the
speedup *increases* with scale rather than staying constant.

A regression test (`tests/integration/test_n_plus_one.py`) asserts this
property directly: it counts real SQL statements executed during a
`patients { encounters }` GraphQL request at two different page sizes
and fails if the count differs — catching, for example, a future
resolver that reaches for a repository directly instead of a DataLoader.

## Database indexing

Every patient-scoped table has a composite index on `(patient_id,
<timestamp column>)` — e.g. `ix_encounters_patient_started`,
`ix_lab_results_patient_resulted` — matching exactly how
`PatientScopedRepository` queries and orders them. This is what keeps
`paginate_by_patient` a single index range scan rather than a sequential
scan plus sort, at any table size.

The authorization backbone gets the same treatment:
`ix_ppr_provider_active` and `ix_ppr_patient_active` on
`patient_provider_relationships` — since every patient-scoped read runs
an access-policy check against this table, an unindexed lookup there
would be the slowest part of every single request, not just the
occasional expensive one.

See [migrations/versions/0b93bd8345a0_initial_schema.py](migrations/versions/0b93bd8345a0_initial_schema.py)
for the complete index list.

## Cursor-based pagination

Every list — `patients`, `patientEncounters`, `patientMedications`,
`patientLabResults`, the admin audit log — uses keyset pagination
(`app/repositories/pagination.py`), not `OFFSET`. `OFFSET 50000` still
has to scan and discard 50,000 rows; a keyset query carries the last-seen
row's sort key in an opaque cursor and asks for "everything after that
point," which the composite indexes above answer in effectively constant
time regardless of how deep a caller has paged.

Trade-off, stated rather than hidden: `hasPreviousPage` is inferred from
whether a cursor was supplied, not computed with a second reverse-
direction query. That's correct for the sequential-paging pattern every
caller here uses (there's no "jump to the last page" UI) and avoids
doubling the query count on every page fetch.

## Redis caching

`patient(id)` is the single most repeated lookup in this API — every
encounter, medication, lab result, and timeline entry resolves back to
its parent patient — while a patient's demographic record changes far
less often than it's read. `PatientService` (`app/services/patient_service.py`)
implements cache-aside: check Redis first, fall back to Postgres on a
miss, populate the cache for next time, with a 5-minute TTL. Any mutation
that writes to the `patients` table directly (`updatePatientContactInfo`)
invalidates the key explicitly rather than waiting on TTL expiry, since a
stale phone number after an explicit update would be a correctness bug,
not just a minor staleness issue.

`patientTimeline` is deliberately **not** cached: it's a six-way merge
over frequently-changing clinical data, and the staleness/hit-rate
trade-off is worse than for the comparatively static patient record —
caching the stable object (`Patient`) and batching the volatile one
(the timeline) is the right split here, not caching everything reachable.

## Query depth and complexity limits

Covered in [SECURITY.md](SECURITY.md#graphql-specific-hardening), but
it's a performance mechanism as much as a security one: depth limiting,
alias-count limiting, and a pagination-aware cost estimate
(`app/api/graphql/complexity.py`) reject expensive queries before a
resolver — let alone a database connection — is ever touched.

## Connection pooling

The async engine (`app/database/session.py`) is configured with an
explicit `pool_size`/`max_overflow` (10/20 by default,
`DATABASE_POOL_SIZE`/`DATABASE_MAX_OVERFLOW`) rather than the driver
default, plus `pool_pre_ping=True` (so a connection killed by the
database or an intermediate load balancer is detected and replaced
rather than handed to a request that will fail) and `pool_recycle=1800`
(connections are proactively recycled before a 30-minute idle timeout
some proxies enforce). Sizing this is a real trade-off: too small and
requests queue on a connection under load; too large and Postgres itself
becomes the bottleneck, since each connection is a full backend process.
