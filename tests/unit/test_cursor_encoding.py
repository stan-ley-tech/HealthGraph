import pytest

from app.repositories.pagination import decode_cursor, encode_cursor

pytestmark = pytest.mark.unit


def test_round_trip_preserves_parts():
    cursor = encode_cursor("2026-01-01T00:00:00+00:00", "b6839e1e-4c92-4744-9fe1-c8f3154bbf9a")
    assert decode_cursor(cursor) == [
        "2026-01-01T00:00:00+00:00",
        "b6839e1e-4c92-4744-9fe1-c8f3154bbf9a",
    ]


def test_cursor_is_opaque_and_url_safe():
    cursor = encode_cursor("2026-01-01T00:00:00+00:00", "some-id")
    # Relay clients treat cursors as opaque tokens; ours happen to be
    # base64url, which must never contain characters that need escaping
    # in a URL query string.
    assert all(c.isalnum() or c in "-_=" for c in cursor)


def test_different_input_produces_different_cursors():
    a = encode_cursor("2026-01-01T00:00:00+00:00", "id-a")
    b = encode_cursor("2026-01-02T00:00:00+00:00", "id-a")
    assert a != b
