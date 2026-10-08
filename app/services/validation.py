"""validate_recipients() implementing per-recipient validation rules (added in Phase 2).

Pure function: no DB, no FastAPI. Delegates the per-field rules to `RecipientIn` (so the API
schema and the service agree on what "valid" means) and layers on duplicate detection and
best-effort extraction for rows that fail (ADR-013, ADR-018).
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.enums import ErrorCode
from app.schemas import RecipientIn

# ARCHITECTURE §4: `raw_input` is stored only when it fits; beyond this, keep a preview instead
# of the full (possibly huge) payload.
_RAW_PREVIEW_LIMIT = 2000
# Column lengths for best-effort extraction on invalid rows (ARCHITECTURE §4 / ADR-018).
_NAME_MAX_LENGTH = 200
_EMAIL_MAX_LENGTH = 320


@dataclass(frozen=True)
class FieldError:
    """One field-level problem, matching the API's invalid-row `errors[]` entries."""

    field: str | None
    message: str


@dataclass(frozen=True)
class ValidRecipient:
    """A recipient that passed validation and is not a duplicate."""

    index: int
    name: str
    email: str


@dataclass(frozen=True)
class InvalidRecipient:
    """A recipient row that failed validation or was a duplicate."""

    index: int
    code: ErrorCode
    message: str
    errors: list[FieldError]
    name: str | None
    email: str | None
    raw: Any

    def to_dict(self) -> dict[str, Any]:
        """The API invalid-row object shape (API_SPEC.md)."""
        return {
            "index": self.index,
            "code": str(self.code),
            "errors": [{"field": e.field, "message": e.message} for e in self.errors],
        }


@dataclass(frozen=True)
class ValidationResult:
    """Output of `validate_recipients`: both lists ordered by `index`."""

    valid: list[ValidRecipient]
    invalid: list[InvalidRecipient]


def _prepare_raw(item: Any) -> Any:
    """Keep `item` as-is unless its JSON form is too large, then store a bounded preview.

    `default=str` lets exotic-but-JSON-adjacent values (e.g. objects a client sent that json
    can't natively serialise) still produce a length estimate instead of raising.
    """
    try:
        serialised = json.dumps(item, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialised = str(item)
    if len(serialised) > _RAW_PREVIEW_LIMIT:
        return {"truncated": True, "preview": serialised[:_RAW_PREVIEW_LIMIT]}
    return item


def _best_effort_str(value: Any, max_length: int, *, collapse_whitespace: bool) -> str | None:
    """Trim a string value for display on an invalid row; non-strings and blanks become None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if collapse_whitespace:
        text = " ".join(text.split())
    if text == "":
        return None
    return text[:max_length]


def _best_effort_name(item: Any) -> str | None:
    value = item.get("name") if isinstance(item, dict) else None
    return _best_effort_str(value, _NAME_MAX_LENGTH, collapse_whitespace=True)


def _best_effort_email(item: Any) -> str | None:
    value = item.get("email") if isinstance(item, dict) else None
    return _best_effort_str(value, _EMAIL_MAX_LENGTH, collapse_whitespace=False)


def _field_errors(exc: ValidationError) -> list[FieldError]:
    """Convert a Pydantic ValidationError into the API's flat field/message pairs.

    `loc` for a field error is `(field_name,)`; for `extra_forbidden` it's the unexpected key;
    for `missing` it's the missing key. All three cases reduce to "first loc element, if any".
    """
    result: list[FieldError] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = str(loc[0]) if loc else None
        result.append(FieldError(field=field, message=err["msg"]))
    return result


def validate_recipients(items: list[Any]) -> ValidationResult:
    """Validate every recipient row independently; never raises (ADR-004).

    Rows are processed in order so duplicate detection can reference "the earlier index", and
    both output lists end up naturally ordered by index since we append once per row.
    """
    valid: list[ValidRecipient] = []
    invalid: list[InvalidRecipient] = []
    # Casefolded email -> index of the first VALID occurrence (ADR-013). Invalid rows never enter
    # this map, so a duplicate of an invalid row is not treated as a duplicate.
    first_valid_index_by_email: dict[str, int] = {}

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            invalid.append(
                InvalidRecipient(
                    index=index,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Invalid recipient data",
                    errors=[
                        FieldError(
                            field=None,
                            message="Recipient must be a JSON object with 'name' and 'email'",
                        )
                    ],
                    name=None,
                    email=None,
                    raw=_prepare_raw(item),
                )
            )
            continue

        try:
            recipient = RecipientIn.model_validate(item)
        except ValidationError as exc:
            invalid.append(
                InvalidRecipient(
                    index=index,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Invalid recipient data",
                    errors=_field_errors(exc),
                    name=_best_effort_name(item),
                    email=_best_effort_email(item),
                    raw=_prepare_raw(item),
                )
            )
            continue

        email_key = recipient.email.casefold()
        first_index = first_valid_index_by_email.get(email_key)
        if first_index is not None:
            invalid.append(
                InvalidRecipient(
                    index=index,
                    code=ErrorCode.DUPLICATE_RECIPIENT,
                    message=f"Duplicate of recipient at index {first_index}",
                    errors=[
                        FieldError(
                            field="email",
                            message=f"Duplicate of recipient at index {first_index}",
                        )
                    ],
                    name=recipient.name,
                    email=recipient.email,
                    raw=_prepare_raw(item),
                )
            )
            continue

        first_valid_index_by_email[email_key] = index
        valid.append(ValidRecipient(index=index, name=recipient.name, email=recipient.email))

    return ValidationResult(valid=valid, invalid=invalid)
