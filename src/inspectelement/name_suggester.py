from __future__ import annotations

from .models import ElementSummary

_MAX_BASE_LENGTH = 40


def suggest_element_name(summary: ElementSummary | None, fallback: str | None = None) -> str:
    source = _best_name_source(summary, fallback)
    base = to_upper_snake(source, max_length=_MAX_BASE_LENGTH)
    suffix = _suffix_for_summary(summary)
    return f"{base}_{suffix}" if not base.endswith(f"_{suffix}") else base


def to_upper_snake(value: str, max_length: int = _MAX_BASE_LENGTH) -> str:
    normalized = _normalize_turkish(value)

    characters: list[str] = []
    previous_was_underscore = False
    for char in normalized:
        if char.isalnum():
            characters.append(char.upper())
            previous_was_underscore = False
            continue

        if not previous_was_underscore:
            characters.append("_")
            previous_was_underscore = True

    collapsed = "".join(characters).strip("_")
    if not collapsed:
        collapsed = "ELEMENT"

    if collapsed[0].isdigit():
        collapsed = f"E_{collapsed}"

    if len(collapsed) > max_length:
        collapsed = collapsed[:max_length].rstrip("_") or "ELEMENT"

    return collapsed


def _best_name_source(summary: ElementSummary | None, fallback: str | None) -> str:
    if summary:
        for key in ("data-testid", "data-test", "data-qa"):
            value = summary.attributes.get(key)
            if value and value.strip():
                return value

        for value in (summary.id, summary.name, summary.aria_label, summary.placeholder, summary.text):
            if value and value.strip():
                return value

    if fallback and fallback.strip():
        return fallback
    return "ELEMENT"


def _suffix_for_summary(summary: ElementSummary | None) -> str:
    if not summary:
        return "TXT"

    tag = (summary.tag or "").strip().lower()
    role = (summary.role or "").strip().lower()
    input_type = (summary.attributes.get("type", "") if summary.attributes else "").lower()

    if tag == "button" or role == "button":
        return "BTN"
    if tag == "input" and input_type in {"button", "submit", "reset"}:
        return "BTN"
    if tag == "a":
        return "LNK"
    return "TXT"


def _normalize_turkish(value: str) -> str:
    table = str.maketrans(
        {
            "ç": "c",
            "Ç": "C",
            "ğ": "g",
            "Ğ": "G",
            "ı": "i",
            "İ": "I",
            "ö": "o",
            "Ö": "O",
            "ş": "s",
            "Ş": "S",
            "ü": "u",
            "Ü": "U",
        }
    )
    return value.translate(table)
