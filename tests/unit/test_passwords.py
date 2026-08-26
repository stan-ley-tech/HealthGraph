import pytest

from app.auth.passwords import hash_password, verify_password

pytestmark = pytest.mark.unit


def test_hash_is_not_the_plain_password():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"


def test_verify_accepts_the_correct_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_rejects_the_wrong_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_hashing_the_same_password_twice_gives_different_hashes():
    # bcrypt salts each hash, so two hashes of the same password should
    # never be byte-identical even though both verify correctly.
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True
