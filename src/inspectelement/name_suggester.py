from __future__ import annotations

import re

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
        stable_test_attr: str | None = None
        for key in ("data-testid", "data-test", "data-qa"):
            value = summary.attributes.get(key)
            if value and value.strip() and not _is_noisy_identifier(value):
                stable_test_attr = value.strip()
                break

        # For clickable elements, prefer visible human text only if stable test-id is not available.
        if _is_clickable(summary) and stable_test_attr is None:
            for value in (summary.text, summary.aria_label, summary.label_text):
                if _is_meaningful_human_text(value):
                    return value.strip()

        if stable_test_attr:
            return stable_test_attr

        for value in (summary.id, summary.name, summary.aria_label, summary.placeholder, summary.label_text, summary.text):
            if not value or not value.strip():
                continue
            candidate = value.strip()
            if _looks_like_locator_expression(candidate):
                continue
            if _is_noisy_identifier(candidate) and _is_clickable(summary) and _is_meaningful_human_text(summary.text):
                continue
            return candidate

    if fallback and fallback.strip():
        normalized_fallback = fallback.strip()
        extracted_text = _extract_text_from_locator(normalized_fallback)
        if extracted_text:
            return extracted_text
        if not _looks_like_locator_expression(normalized_fallback):
            return normalized_fallback
    return "ELEMENT"


def _is_clickable(summary: ElementSummary) -> bool:
    tag = (summary.tag or "").strip().lower()
    role = (summary.role or "").strip().lower()
    return tag in {"a", "button"} or role in {"button", "link", "menuitem", "tab"}


def _is_meaningful_human_text(value: str | None) -> bool:
    if not value or not value.strip():
        return False
    cleaned = value.strip()
    if len(cleaned) < 2:
        return False
    if len(cleaned) > 64:
        return False
    if _looks_like_locator_expression(cleaned):
        return False
    if not any(char.isalpha() for char in cleaned):
        return False
    lowered = cleaned.lower()
    if lowered in {"none", "null", "undefined", "item", "button", "link"}:
        return False
    return True


def _is_noisy_identifier(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return True

    if _looks_like_locator_expression(lowered):
        return True

    generic_tokens = {
        "none",
        "null",
        "undefined",
        "item",
        "items",
        "container",
        "wrapper",
        "section",
        "slider",
        "menu",
        "nav",
        "content",
        "row",
        "col",
    }
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if any(token in generic_tokens for token in tokens):
        return True

    if re.search(r"\d{2,}", lowered):
        return True

    return False


def _looks_like_locator_expression(value: str) -> bool:
    lowered = value.lower()
    patterns = (
        "//",
        "/html",
        "normalize-space",
        "by.",
        "css=",
        "xpath",
        "@id",
        "@name",
        "@class",
        "@text",
        "@data-",
    )
    return any(pattern in lowered for pattern in patterns)


def _extract_text_from_locator(locator: str) -> str | None:
    # Common patterns:
    # //a[normalize-space()='Yemek']
    # //*[text()='Kaydet']
    patterns = (
        r"normalize-space\(\)\s*=\s*['\"]([^'\"]+)['\"]",
        r"text\(\)\s*=\s*['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, locator, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if _is_meaningful_human_text(candidate):
                return candidate
    return None


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
