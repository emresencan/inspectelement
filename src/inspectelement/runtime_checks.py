from __future__ import annotations

import re
from typing import Any, Mapping

_MISSING_BROWSER_ERROR_HINTS = (
    "executable doesn't exist",
    "executable does not exist",
    "download new browsers",
    "playwright install",
    "could not find browser",
    "failed to launch chromium because executable",
)


def _is_missing_browser_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _MISSING_BROWSER_ERROR_HINTS)


_CSS_SAFE_ID_PATTERN = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")


def _normalize_space(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def is_css_safe_id(value: str) -> bool:
    return bool(_CSS_SAFE_ID_PATTERN.fullmatch(value.strip()))


def escape_css_attribute_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_id_selector_candidates(raw_id: Any) -> list[str]:
    if raw_id is None:
        return []

    id_value = str(raw_id).strip()
    if not id_value:
        return []

    selectors: list[str] = []
    if is_css_safe_id(id_value):
        selectors.append(f"#{id_value}")
    selectors.append(f'[id="{escape_css_attribute_value(id_value)}"]')
    return selectors


def payload_matches_observed_element(payload: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    payload_tag = _normalize_space(payload.get("tag")).lower()
    observed_tag = _normalize_space(observed.get("tag")).lower()
    if not payload_tag or not observed_tag or payload_tag != observed_tag:
        return False

    comparisons = (
        ("text", "text"),
        ("ariaLabel", "aria_label"),
        ("placeholder", "placeholder"),
        ("name", "name"),
    )
    checks: list[bool] = []
    for payload_key, observed_key in comparisons:
        expected = _normalize_space(payload.get(payload_key))
        if not expected:
            continue
        actual = _normalize_space(observed.get(observed_key))
        checks.append(actual == expected)

    if not checks:
        return True
    return any(checks)
