"""Benchmarks the N+1 query pattern against the DataLoader-style batched
alternative, for the exact access pattern `patientTimeline` and the
nested `Patient.encounters` field rely on: loading each patient's
encounters when resolving a list of patients.

Everything here runs inside one transaction that's rolled back at the
end, so running this against a real database — including a shared dev
one — leaves no trace.

Usage:
    python -m scripts.benchmark
    python -m scripts.benchmark --patient-counts 10,50,200,1000
"""

import argparse
import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import engine
from app.models.clinical import Encounter
from app.models.enums import EncounterType, FacilityType, Sex
from app.models.facility import Facility
from app.models.patient import Patient
from app.models.provider import Provider
from app.repositories.clinical import EncounterRepository


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    label: str
    patient_count: int
    query_count: int
    elapsed_seconds: float


async def _seed(session: AsyncSession, patient_count: int, encounters_per_patient: int) -> list:
    facility = Facility(name="Benchmark Facility", facility_type=FacilityType.HOSPITAL)
    session.add(facility)
    await session.flush()
    provider = Provider(
        facility_id=facility.id, first_name="Bench", last_name="Mark",
        npi_number=uuid.uuid4().hex[:10],
    )
    session.add(provider)
    await session.flush()

    patients = []
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(patient_count):
        patient = Patient(
            mrn=f"BENCH-{uuid.uuid4().hex[:10]}", first_name="Load", last_name=f"Test{i}",
            date_of_birth=datetime(1990, 1, 1).date(), sex=Sex.OTHER,
        )
        session.add(patient)
        patients.append(patient)
    await session.flush()

    for patient in patients:
        for j in range(encounters_per_patient):
            session.add(
                Encounter(
                    patient_id=patient.id, provider_id=provider.id, facility_id=facility.id,
                    encounter_type=EncounterType.OUTPATIENT, chief_complaint=f"Visit {j}",
                    started_at=base_time + timedelta(days=j),
                )
            )
    await session.flush()
    return patients


async def _run_naive(session: AsyncSession, patients: list) -> int:
    """The N+1 way: one query per patient. This is what a resolver reaching
    for a repository directly — instead of a DataLoader — would produce."""
    query_count = 0
    for patient in patients:
        stmt = select(Encounter).where(Encounter.patient_id == patient.id)
        await session.execute(stmt)
        query_count += 1
    return query_count


async def _run_batched(session: AsyncSession, patients: list) -> int:
    """The DataLoader way: one query for every patient in the batch."""
    repo = EncounterRepository(session)
    await repo.list_by_patient_ids([p.id for p in patients])
    return 1


async def _time_it(coro) -> tuple[float, int]:
    start = time.perf_counter()
    query_count = await coro
    elapsed = time.perf_counter() - start
    return elapsed, query_count


async def run_benchmark(
    patient_counts: list[int], encounters_per_patient: int
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []

    async with engine.connect() as connection:
        transaction = await connection.begin()
        scoped_factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            for count in patient_counts:
                session = scoped_factory()
                patients = await _seed(session, count, encounters_per_patient)

                naive_elapsed, naive_queries = await _time_it(_run_naive(session, patients))
                results.append(
                    BenchmarkResult("naive (N+1)", count, naive_queries, naive_elapsed)
                )

                batched_elapsed, batched_queries = await _time_it(
                    _run_batched(session, patients)
                )
                results.append(
                    BenchmarkResult("batched (DataLoader)", count, batched_queries, batched_elapsed)
                )
                await session.close()
        finally:
            await transaction.rollback()

    return results


def print_report(results: list[BenchmarkResult]) -> None:
    header = f"{'approach':<22}{'patients':>10}{'queries':>10}{'time (ms)':>12}{'ms/patient':>12}"
    print(header)
    print("-" * len(header))
    for r in results:
        ms = r.elapsed_seconds * 1000
        per_patient = ms / r.patient_count if r.patient_count else 0.0
        print(f"{r.label:<22}{r.patient_count:>10}{r.query_count:>10}{ms:>12.2f}{per_patient:>12.3f}")

    print()
    by_count: dict[int, dict[str, BenchmarkResult]] = {}
    for r in results:
        by_count.setdefault(r.patient_count, {})[r.label] = r
    for count, pair in by_count.items():
        naive = pair.get("naive (N+1)")
        batched = pair.get("batched (DataLoader)")
        if naive and batched and batched.elapsed_seconds > 0:
            speedup = naive.elapsed_seconds / batched.elapsed_seconds
            print(
                f"At {count} patients: {naive.query_count} queries -> {batched.query_count} "
                f"query, {speedup:.1f}x faster"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--patient-counts", type=str, default="10,50,200,1000",
        help="Comma-separated patient counts to benchmark at.",
    )
    parser.add_argument(
        "--encounters-per-patient", type=int, default=5,
        help="Number of encounters seeded per patient.",
    )
    args = parser.parse_args()
    patient_counts = [int(n) for n in args.patient_counts.split(",")]

    results = asyncio.run(run_benchmark(patient_counts, args.encounters_per_patient))
    print_report(results)


if __name__ == "__main__":
    main()
