# Security Model

## Authentication

- **Passwords** are hashed with bcrypt (`app/auth/passwords.py`) — a
  deliberately slow, salted algorithm with no known practical break, sized
  for the actual threat (offline brute force against a leaked hash), not
  for throughput.
- **Access tokens** are short-lived JWTs (15 minutes by default,
  `ACCESS_TOKEN_EXPIRE_MINUTES`), signed HS256, carrying `sub` (user id)
  and `role`. They're stateless by design — verified without a database
  round trip — which is exactly why they're kept short-lived.
- **Refresh tokens** are opaque, high-entropy random strings
  (`secrets.token_urlsafe(48)`), stored **hashed** (SHA-256) in
  `refresh_tokens.token_hash`. A database leak alone never hands out a
  usable session, because the raw token isn't recoverable from its hash.
- **Refresh rotation**: every `/api/v1/auth/refresh` call revokes the
  presented token and issues a new one (`AuthService.refresh`). A token
  can be used exactly once; replaying an already-used one fails with
  401. In production this is also the signal worth alerting on — a
  replay attempt means a refresh token was captured by someone other
  than its legitimate holder.
- **Logout** revokes the specific refresh token presented, not every
  session for the user — a deliberate choice to make logout scoped to
  one device/client.

## Authorization

Two independent layers, checked in this order:

1. **Role-based access control (RBAC)** — coarse-grained, "can this role
   even attempt this action." Enforced at the REST route level
   (`app/auth/dependencies.py::require_roles`) and per-mutation in
   GraphQL (`app/api/graphql/mutations.py::_require_role`). Example:
   `prescribeMedication` is gated to `DOCTOR`/`ADMIN` — a `NURSE` is
   rejected before any patient is even looked up, modeling that nurses
   don't hold prescriptive authority.

2. **Patient-data access policy** — fine-grained, "can this specific
   caller touch this specific patient." This is the part that keeps RBAC
   from being a rubber stamp: **holding the `DOCTOR` role does not grant
   access to any patient.** `app/domain/access_policy.py` checks the
   `patient_provider_relationships` table for an active relationship
   between the calling provider and the target patient on every
   patient-scoped read and write. A doctor with no relationship to a
   patient is refused exactly like an unauthenticated caller would be —
   whether they call GraphQL directly, bypass the intended UI, or guess
   a patient id.

   | Role | `patient(id)` / full record | Lab results | Own contact info |
   |---|---|---|---|
   | `ADMIN` | any patient | any patient | any patient |
   | `DOCTOR` / `NURSE` | only with an active relationship | only with an active relationship | — |
   | `LAB_TECHNICIAN` | never | any patient (labs are processed system-wide, not tied to a personal relationship) | — |
   | `PATIENT` | only their own record | only their own record | only their own record |

   Relationships are established via `addPatientProviderRelationship`,
   which is **ADMIN-only** — a provider cannot grant themselves access to
   a patient.

3. **Field-level authorization** — object-level access does not imply
   field-level access. `Patient.ssn` is withheld even from a doctor with
   full record access to that patient; only `ADMIN` or the patient
   themselves can read it (`PatientAccessPolicy.can_view_ssn`, enforced
   in the `ssn` field resolver on the GraphQL type, not just at the
   query boundary — so no combination of query shape exposes it to an
   unauthorized caller).

## Rate limiting

A Redis-backed sliding-window counter (`app/middleware/rate_limit.py`)
wraps every request except `/health`. It's keyed by a hash of the bearer
token when present, falling back to client IP for anonymous traffic —
deliberately *not* decoding the JWT here, so a request with an expired
token is still throttled rather than skipping the limiter. The sliding-
window blend (weighting the previous window's count by how much of the
current window has elapsed) avoids the classic fixed-window bug where a
client can burst to 2x the limit across a window boundary.

## Audit trail

Every authentication event (register, login, login failure, logout) and
every mutation that writes patient data is recorded via `AuditService`
(`app/services/audit_service.py`) — actor, action, resource type/id, IP
address, and a JSON details blob. Audit writes commit through their
**own** database connection rather than the caller's, specifically so a
security-relevant event survives even if the request that triggered it
fails or rolls back afterward (a failed login is exactly the kind of
event you cannot afford to lose because "the operation didn't succeed").
Audit rows are append-only — the `AuditLog` model has no `updated_at`,
by design.

Administrators can list the audit trail via `GET /api/v1/admin/audit-logs`
(cursor-paginated, `ADMIN`-only).

## Input validation

- GraphQL's type system rejects malformed shapes (wrong scalar types,
  invalid enum values, missing required fields) before a resolver runs.
- Domain-level validators (`app/domain/validators.py`) enforce rules the
  schema can't express: non-empty strings after trimming, dates not in
  the future, an end date not preceding a start date, positive numeric
  measurements. Every mutation resolver runs its inputs through these
  before touching a repository.
- REST request bodies are validated via Pydantic models
  (`app/api/rest/schemas.py`) — e.g. password length, valid email format.

## Structured error handling

Every handled error is one of the typed exceptions in
`app/core/errors.py` (`AuthenticationError`, `AuthorizationError`,
`ValidationError`, `NotFoundError`, `ConflictError`,
`RateLimitExceededError`, `QueryComplexityError`), each carrying a stable
`code` and an HTTP status. REST responses render these through one
exception handler (`app/core/error_handlers.py`) into a consistent
`{"error": {"code", "message"}}` envelope; GraphQL errors surface through
the standard `errors` array. Clients never see a raw stack trace or an
ad hoc error shape depending on which layer failed.

## GraphQL-specific hardening

Query cost is bounded before any resolver executes
(`app/api/graphql/schema.py`, `app/api/graphql/complexity.py`):

- **Depth limiting** (`QueryDepthLimiter`) rejects pathologically nested
  queries.
- **Alias-count limiting** (`MaxAliasesLimiter`) rejects the classic
  "alias bombing" attack, where one query repeats an expensive field
  under hundreds of aliases to multiply its cost past what depth alone
  would catch.
- **Complexity estimation** (`QueryComplexityLimiter`) walks the query
  AST and multiplies each field's cost by any `first`/`limit` argument on
  it — catching a *shallow but wide* query (`patients(first: 1000) {
  encounters(limit: 1000) { ... } }`) that depth limiting alone would
  miss.

See [PERFORMANCE.md](PERFORMANCE.md) for the query-batching side of
expensive-query prevention (N+1 avoidance via DataLoader).

## What's intentionally out of scope

This is a portfolio-scale system, not a HIPAA-certified production
deployment. Notably absent, and worth naming rather than pretending
otherwise:

- Encryption at rest for `ssn` and other sensitive columns (they're
  access-controlled, not encrypted, in the database).
- Multi-factor authentication.
- IP allowlisting / VPN-gated admin access.
- A secrets manager for `SECRET_KEY` / database credentials — the
  `.env.example` pattern here is for local development and CI, not for
  a real deployment's credential handling.
