"""Tests for app/services/validation.py: validate_recipients() and its helpers."""

import json

from app.enums import ErrorCode
from app.services.validation import InvalidRecipient, validate_recipients


def test_valid_row_is_normalised() -> None:
    result = validate_recipients([{"name": "  Asha   Verma  ", "email": "Asha@Example.com"}])
    assert len(result.valid) == 1
    assert result.invalid == []
    row = result.valid[0]
    assert row.index == 0
    assert row.name == "Asha Verma"
    # EmailStr lowercases the domain part (per email-validator); local part is preserved as-is.
    assert row.email == "Asha@example.com"


def test_trimming_and_whitespace_collapse_including_tabs_and_newlines() -> None:
    result = validate_recipients([{"name": "Asha\t\tVerma\n Kumar ", "email": "a@example.com"}])
    assert result.valid[0].name == "Asha Verma Kumar"


def test_nfc_normalisation() -> None:
    # "e" + combining acute (NFD) should normalise to the same string as the NFC precomposed form.
    decomposed = "José Garcia"  # Jo + s + e + COMBINING ACUTE ACCENT + " Garcia"
    composed = "José Garcia"
    result = validate_recipients([{"name": decomposed, "email": "a@example.com"}])
    assert result.valid[0].name == composed


def test_empty_name_rejected() -> None:
    result = validate_recipients([{"name": "", "email": "a@example.com"}])
    assert result.valid == []
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_whitespace_only_name_rejected() -> None:
    result = validate_recipients([{"name": "   ", "email": "a@example.com"}])
    assert result.valid == []
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_digits_only_name_rejected() -> None:
    result = validate_recipients([{"name": "12345", "email": "a@example.com"}])
    assert result.valid == []
    errors = result.invalid[0].errors
    assert any(e.field == "name" for e in errors)


def test_name_of_100_chars_is_ok() -> None:
    name = "A" * 100
    result = validate_recipients([{"name": name, "email": "a@example.com"}])
    assert result.valid[0].name == name


def test_name_of_101_chars_rejected() -> None:
    name = "A" * 101
    result = validate_recipients([{"name": name, "email": "a@example.com"}])
    assert result.valid == []
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_control_character_in_name_rejected() -> None:
    result = validate_recipients([{"name": "Asha\x00Verma", "email": "a@example.com"}])
    assert result.valid == []
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_cf_category_control_character_rejected() -> None:
    # U+200B ZERO WIDTH SPACE is category Cf.
    result = validate_recipients([{"name": "Asha​Verma", "email": "a@example.com"}])
    assert result.valid == []
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_bad_email_rejected() -> None:
    result = validate_recipients([{"name": "Asha Verma", "email": "not-an-email"}])
    assert result.valid == []
    errors = result.invalid[0].errors
    assert any(e.field == "email" for e in errors)


def test_missing_field() -> None:
    result = validate_recipients([{"name": "Asha Verma"}])
    assert result.valid == []
    errors = result.invalid[0].errors
    assert any(e.field == "email" for e in errors)


def test_extra_field_rejected() -> None:
    result = validate_recipients([{"name": "Asha Verma", "email": "a@example.com", "phone": "123"}])
    assert result.valid == []
    errors = result.invalid[0].errors
    assert any(e.field == "phone" for e in errors)


def test_non_object_items_have_null_field() -> None:
    for item in ["just a string", 42, None, ["a", "b"]]:
        result = validate_recipients([item])
        assert result.valid == []
        invalid = result.invalid[0]
        assert invalid.code == ErrorCode.VALIDATION_ERROR
        assert len(invalid.errors) == 1
        assert invalid.errors[0].field is None


def test_duplicate_emails_case_insensitive_first_wins() -> None:
    result = validate_recipients(
        [
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "Asha V", "email": "ASHA@EXAMPLE.COM"},
        ]
    )
    assert len(result.valid) == 1
    assert result.valid[0].index == 0
    assert len(result.invalid) == 1
    dup = result.invalid[0]
    assert dup.index == 1
    assert dup.code == ErrorCode.DUPLICATE_RECIPIENT
    assert "index 0" in dup.message
    assert dup.errors[0].field == "email"
    assert "index 0" in dup.errors[0].message
    assert dup.name == "Asha V"
    assert dup.email == "ASHA@example.com"


def test_duplicate_of_invalid_row_is_not_a_duplicate() -> None:
    result = validate_recipients(
        [
            {"name": "", "email": "asha@example.com"},  # invalid: bad name
            {"name": "Asha Verma", "email": "asha@example.com"},  # should be valid, not a dup
        ]
    )
    assert len(result.valid) == 1
    assert result.valid[0].index == 1
    assert len(result.invalid) == 1
    assert result.invalid[0].index == 0
    assert result.invalid[0].code == ErrorCode.VALIDATION_ERROR


def test_mixed_list_preserves_indices() -> None:
    items = [
        {"name": "Asha Verma", "email": "asha@example.com"},
        {"name": "", "email": "bad"},
        {"name": "Rahul Mehta", "email": "rahul@example.com"},
        "not an object",
        {"name": "Asha V2", "email": "ASHA@example.com"},  # duplicate of index 0
    ]
    result = validate_recipients(items)
    valid_indices = [r.index for r in result.valid]
    invalid_indices = [r.index for r in result.invalid]
    assert valid_indices == [0, 2]
    assert invalid_indices == [1, 3, 4]
    # Each list is itself ordered by index.
    assert valid_indices == sorted(valid_indices)
    assert invalid_indices == sorted(invalid_indices)


def test_best_effort_name_and_email_extraction() -> None:
    result = validate_recipients([{"name": "  Spaced   Name  ", "email": 12345}])
    invalid = result.invalid[0]
    assert invalid.name == "Spaced Name"
    assert invalid.email is None  # non-string email -> None


def test_best_effort_truncation() -> None:
    long_name = "A" * 300
    long_email_local = "b" * 400
    result = validate_recipients([{"name": long_name, "email": f"{long_email_local}@x.com"}])
    invalid = result.invalid[0]
    assert invalid.name is not None
    assert len(invalid.name) == 200
    assert invalid.email is not None
    assert len(invalid.email) == 320


def test_best_effort_non_dict_item_gives_none_name_and_email() -> None:
    result = validate_recipients(["just a string"])
    invalid = result.invalid[0]
    assert invalid.name is None
    assert invalid.email is None


def test_raw_truncation_over_2000_chars() -> None:
    huge_name = "x" * 3000
    item = {"name": huge_name, "email": "not-an-email"}
    result = validate_recipients([item])
    invalid = result.invalid[0]
    assert isinstance(invalid.raw, dict)
    assert invalid.raw["truncated"] is True
    assert len(invalid.raw["preview"]) == 2000


def test_raw_kept_as_is_when_small() -> None:
    item = {"name": "Asha Verma", "email": "not-an-email"}
    result = validate_recipients([item])
    invalid = result.invalid[0]
    assert invalid.raw == item


def test_to_dict_shape_matches_api_spec() -> None:
    result = validate_recipients([{"name": "", "email": "not-an-email"}])
    invalid = result.invalid[0]
    data = invalid.to_dict()
    assert set(data.keys()) == {"index", "code", "errors"}
    assert data["index"] == 0
    assert data["code"] == "VALIDATION_ERROR"
    assert isinstance(data["errors"], list)
    for err in data["errors"]:
        assert set(err.keys()) == {"field", "message"}
    # Must be JSON-serialisable as-is (it goes straight into the error envelope / API response).
    json.dumps(data)


def test_duplicate_to_dict_shape() -> None:
    result = validate_recipients(
        [
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "Asha V", "email": "asha@example.com"},
        ]
    )
    dup = result.invalid[0]
    assert isinstance(dup, InvalidRecipient)
    data = dup.to_dict()
    assert data["code"] == "DUPLICATE_RECIPIENT"
    assert data["errors"] == [{"field": "email", "message": "Duplicate of recipient at index 0"}]
