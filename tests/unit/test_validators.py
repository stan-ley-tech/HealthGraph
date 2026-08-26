from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationError
from app.domain.validators import (
    require_date_order,
    require_non_empty,
    require_not_future,
    require_positive,
)

pytestmark = pytest.mark.unit


def test_require_non_empty_strips_and_returns_valid_input():
    assert require_non_empty("  Aspirin  ", "name") == "Aspirin"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_require_non_empty_rejects_blank_input(value):
    with pytest.raises(ValidationError):
        require_non_empty(value, "name")


def test_require_not_future_accepts_today_and_the_past():
    today = datetime.now(UTC).date()
    assert require_not_future(today, "dateOfBirth") == today
    assert require_not_future(today - timedelta(days=1), "dateOfBirth") == today - timedelta(days=1)


def test_require_not_future_rejects_a_future_date():
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    with pytest.raises(ValidationError):
        require_not_future(tomorrow, "dateOfBirth")


def test_require_date_order_accepts_end_on_or_after_start():
    today = datetime.now(UTC).date()
    require_date_order(today, today, start_field="start", end_field="end")
    require_date_order(today, today + timedelta(days=1), start_field="start", end_field="end")
    require_date_order(today, None, start_field="start", end_field="end")


def test_require_date_order_rejects_end_before_start():
    today = datetime.now(UTC).date()
    with pytest.raises(ValidationError):
        require_date_order(today, today - timedelta(days=1), start_field="start", end_field="end")


def test_require_positive_accepts_positive_numbers():
    assert require_positive(1, "value") == 1
    assert require_positive(0.01, "value") == 0.01


@pytest.mark.parametrize("value", [0, -1, -0.5])
def test_require_positive_rejects_zero_and_negative(value):
    with pytest.raises(ValidationError):
        require_positive(value, "value")
