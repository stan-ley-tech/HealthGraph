"""Caching tests for the cache-aside `PatientService`, against a real
Redis instance rather than a mock — the thing worth verifying is the
actual serialize/deserialize round trip and TTL behavior, which a mock
would just assume away."""

from datetime import UTC, date, datetime

import pytest

from app.cache.cache_service import CacheService
from app.models.enums import Sex
from app.repositories.patient import PatientRepository
from app.services.patient_service import PatientRecord, PatientService

pytestmark = pytest.mark.integration


async def _make_patient(db_session, mrn):
    repo = PatientRepository(db_session)
    patient = repo.create(
        mrn=mrn, first_name="Cache", last_name="Test",
        date_of_birth=date(1990, 1, 1), sex=Sex.FEMALE, ssn="000-00-0000",
    )
    await db_session.commit()
    return patient


@pytest.fixture(autouse=True)
async def _clean_cache_key(redis_client, unique_mrn):
    # The service derives its cache key from the patient id, which we
    # don't know until the test creates the patient — so just make sure
    # this test's slice of the keyspace is clean going in and coming out.
    yield
    async for key in redis_client.scan_iter(match="patient:*"):
        await redis_client.delete(key)


async def test_first_read_is_a_cache_miss_and_populates_the_cache(
    db_session, redis_client, unique_mrn
):
    patient = await _make_patient(db_session, unique_mrn)
    cache = CacheService(redis_client)
    service = PatientService(db_session, cache)

    assert await cache.get(f"patient:{patient.id}") is None

    record = await service.get_patient(patient.id)

    assert record is not None
    assert record.first_name == "Cache"
    assert await cache.get(f"patient:{patient.id}") is not None


async def test_second_read_is_served_from_cache_even_if_row_is_deleted(
    db_session, redis_client, unique_mrn
):
    """The defining behavior of cache-aside: once populated, a read no
    longer depends on the row still existing in the source of truth."""
    patient = await _make_patient(db_session, unique_mrn)
    cache = CacheService(redis_client)
    service = PatientService(db_session, cache)

    first = await service.get_patient(patient.id)
    assert first is not None

    await db_session.delete(await PatientRepository(db_session).get_by_id(patient.id))
    await db_session.commit()

    second = await service.get_patient(patient.id)
    assert second == first


async def test_invalidate_clears_the_cached_entry(db_session, redis_client, unique_mrn):
    patient = await _make_patient(db_session, unique_mrn)
    cache = CacheService(redis_client)
    service = PatientService(db_session, cache)

    await service.get_patient(patient.id)
    assert await cache.get(f"patient:{patient.id}") is not None

    await service.invalidate(patient.id)

    assert await cache.get(f"patient:{patient.id}") is None


async def test_get_patient_returns_none_for_missing_patient_without_caching(
    db_session, redis_client
):
    import uuid

    cache = CacheService(redis_client)
    service = PatientService(db_session, cache)
    missing_id = uuid.uuid4()

    assert await service.get_patient(missing_id) is None
    assert await cache.get(f"patient:{missing_id}") is None


def test_patient_record_survives_a_cache_round_trip():
    from uuid import uuid4

    now = datetime.now(UTC)
    original = PatientRecord(
        id=uuid4(), mrn="MRN-1", first_name="Ada", last_name="Lovelace",
        date_of_birth=date(1815, 12, 10), sex=Sex.FEMALE, phone=None, email=None,
        address=None, ssn="123-45-6789",
        created_at=now, updated_at=now,
    )

    restored = PatientRecord.from_cache(original.to_cache())

    assert restored == original
