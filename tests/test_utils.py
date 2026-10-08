import re

from app.utils import new_certificate_number, slugify_filename, utcnow


def test_utcnow_is_timezone_aware_utc():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


CERT_NUMBER_RE = re.compile(r"^CERT-[A-HJ-NP-Z2-9]{10}$")


def test_certificate_number_format():
    for _ in range(50):
        assert CERT_NUMBER_RE.match(new_certificate_number())


def test_certificate_number_excludes_ambiguous_characters():
    numbers = "".join(new_certificate_number() for _ in range(200))
    for forbidden in "0OIL1":
        assert forbidden not in numbers


def test_certificate_number_is_effectively_unique():
    numbers = {new_certificate_number() for _ in range(1000)}
    assert len(numbers) == 1000


def test_slugify_basic_spaces_and_case():
    assert slugify_filename("Jane Doe") == "jane-doe"


def test_slugify_punctuation_collapses():
    assert slugify_filename("Jane   Doe!!!") == "jane-doe"
    assert slugify_filename("a---b") == "a-b"


def test_slugify_accents_are_transliterated():
    assert slugify_filename("José Núñez") == "jose-nunez"


def test_slugify_path_traversal_is_neutralised():
    result = slugify_filename("../../etc/passwd")
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert result == "etc-passwd"


def test_slugify_empty_or_all_symbols_falls_back():
    assert slugify_filename("") == "certificate"
    assert slugify_filename("!!!???") == "certificate"


def test_slugify_truncates_to_max_length():
    long_name = "a" * 100
    result = slugify_filename(long_name, max_length=60)
    assert len(result) <= 60
