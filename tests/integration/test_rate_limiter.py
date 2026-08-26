import uuid

import pytest

from app.middleware.rate_limit import SlidingWindowRateLimiter

pytestmark = pytest.mark.integration


@pytest.fixture
def rate_limit_key() -> str:
    # Unique per test so concurrent test runs (or reruns) never share a
    # bucket and see each other's counts.
    return f"test:{uuid.uuid4().hex}"


async def test_requests_within_the_limit_are_allowed(redis_client, rate_limit_key):
    limiter = SlidingWindowRateLimiter(redis_client, limit=5, window_seconds=60)

    for _ in range(5):
        allowed, _remaining = await limiter.check(rate_limit_key)
        assert allowed is True


async def test_requests_beyond_the_limit_are_blocked(redis_client, rate_limit_key):
    limiter = SlidingWindowRateLimiter(redis_client, limit=3, window_seconds=60)

    results = [await limiter.check(rate_limit_key) for _ in range(5)]
    allowed_flags = [allowed for allowed, _ in results]

    assert allowed_flags == [True, True, True, False, False]


async def test_remaining_count_decreases_toward_zero(redis_client, rate_limit_key):
    limiter = SlidingWindowRateLimiter(redis_client, limit=3, window_seconds=60)

    _, first_remaining = await limiter.check(rate_limit_key)
    _, second_remaining = await limiter.check(rate_limit_key)

    assert first_remaining == 2
    assert second_remaining == 1


async def test_different_keys_have_independent_limits(redis_client):
    limiter = SlidingWindowRateLimiter(redis_client, limit=1, window_seconds=60)
    key_a = f"test:{uuid.uuid4().hex}"
    key_b = f"test:{uuid.uuid4().hex}"

    allowed_a, _ = await limiter.check(key_a)
    allowed_b, _ = await limiter.check(key_b)

    assert allowed_a is True
    assert allowed_b is True
