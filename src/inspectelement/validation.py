from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .action_catalog import action_parameter_keys, has_table_actions, required_parameter_keys
from .selector_rules import (
    analyze_attribute_stability,
    is_forbidden_locator,
    is_stable_attribute_value,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass(frozen=True, slots=True)
class GenerationValidation:
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class LocatorValidation:
    unique: bool
    match_count: int
    stable: bool
    message: str


def validate_generation_request(
    *,
    has_page: bool,
    has_locator: bool,
    element_name: str,
    actions: Sequence[str],
    action_parameters: Mapping[str, str],
    has_table_root: bool,
) -> GenerationValidation:
    if not has_page:
        return GenerationValidation(False, "Select page before Add.")
    if not has_locator:
        return GenerationValidation(False, "Select locator before Add.")
    if not element_name.strip():
        return GenerationValidation(False, "Element name is required.")

    required_params = set(required_parameter_keys(actions))
    optional_params = {"waitBeforeSelect", "matchType"}
    for key in sorted(required_params):
        if key in optional_params:
            continue
        value = str(action_parameters.get(key, "")).strip()
        if not value:
            return GenerationValidation(False, f"{key} is required for selected action(s).")

    timeout_value = str(action_parameters.get("timeoutSec", "")).strip()
    if "timeoutSec" in set(action_parameter_keys(actions)):
        if not timeout_value.isdigit() or int(timeout_value) <= 0:
            return GenerationValidation(False, "timeoutSec must be a positive integer.")

    if has_table_actions(actions) and not has_table_root:
        return GenerationValidation(False, "Table root could not be detected. Please pick the table root container.")

    if "selectBySelectIdAuto" in actions:
        select_id = str(action_parameters.get("selectId", "")).strip()
        if not select_id:
            return GenerationValidation(False, "Select Id is required for selectBySelectIdAuto.")

    inner_locator = str(action_parameters.get("innerLocator", "")).strip()
    if "innerLocator" in required_params and inner_locator:
        if not _looks_like_by_expression(inner_locator):
            return GenerationValidation(False, "innerLocator must be valid By expression (e.g. By.cssSelector(\"...\"))")

    return GenerationValidation(True, "Validation successful.")


def _looks_like_by_expression(value: str) -> bool:
    return re.fullmatch(
        r'By\.(cssSelector|xpath|id|name)\(".*"\)',
        value,
    ) is not None


def count_locator_matches(
    page: Page,
    locator_type: str,
    locator: str,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    normalized_type = str(locator_type or "").strip()
    text = str(locator or "").strip()
    meta = dict(metadata or {})

    if not normalized_type or not text:
        return 0

    try:
        if normalized_type == "CSS":
            return len(page.query_selector_all(text))

        if normalized_type == "XPath":
            return page.locator(f"xpath={text}").count()

        if normalized_type == "Playwright":
            return _count_playwright_locator(page, meta)

        if normalized_type == "Selenium":
            selector_kind = str(meta.get("selector_kind") or "").strip().lower()
            selector_value = str(meta.get("selector_value") or "").strip()
            if selector_kind and selector_value:
                if selector_kind == "css":
                    return len(page.query_selector_all(selector_value))
                if selector_kind == "xpath":
                    return page.locator(f"xpath={selector_value}").count()
                if selector_kind == "id":
                    escaped = selector_value.replace("\\", "\\\\").replace('"', '\\"')
                    return len(page.query_selector_all(f'[id="{escaped}"]'))
                if selector_kind == "name":
                    escaped = selector_value.replace("\\", "\\\\").replace('"', '\\"')
                    return len(page.query_selector_all(f'[name="{escaped}"]'))

            parsed = _parse_selenium_locator(text)
            if not parsed:
                return 0
            parsed_kind, parsed_value = parsed
            if parsed_kind == "css":
                return len(page.query_selector_all(parsed_value))
            if parsed_kind == "xpath":
                return page.locator(f"xpath={parsed_value}").count()
            if parsed_kind == "id":
                escaped = parsed_value.replace("\\", "\\\\").replace('"', '\\"')
                return len(page.query_selector_all(f'[id="{escaped}"]'))
            if parsed_kind == "name":
                escaped = parsed_value.replace("\\", "\\\\").replace('"', '\\"')
                return len(page.query_selector_all(f'[name="{escaped}"]'))
    except Exception:
        return 0

    return 0


def validate_locator_candidate(
    page: Page,
    locator_type: str,
    locator: str,
    metadata: Mapping[str, Any] | None = None,
) -> LocatorValidation:
    meta = dict(metadata or {})
    match_count = count_locator_matches(page, locator_type, locator, meta)
    stable = not is_forbidden_locator(locator, locator_type)

    source_attr = str(meta.get("source_attr") or "").strip().lower()
    source_value = str(meta.get("source_value") or "").strip()
    if source_attr and source_value:
        analysis = analyze_attribute_stability(source_attr, source_value)
        meta["stability_score"] = analysis.score
        meta["stability_entropy"] = analysis.entropy
        meta["stability_digit_ratio"] = analysis.digit_ratio
        meta["dynamic_detected"] = analysis.dynamic
        if analysis.salvage_prefix:
            meta["salvage_prefix"] = analysis.salvage_prefix
            meta["salvage_penalty"] = analysis.salvage_penalty
        if not is_stable_attribute_value(source_attr, source_value):
            # Prefix salvaged selectors can still be considered if unique.
            if not meta.get("prefix_salvaged"):
                stable = False
            else:
                stable = bool(meta.get("allow_salvage", True))

    if meta.get("prefix_salvaged"):
        # Prefix salvage is only acceptable when unique.
        if match_count != 1:
            stable = False

    unique = match_count == 1
    if not stable:
        return LocatorValidation(False, match_count, False, "Locator rejected by stability rules.")
    if not unique:
        return LocatorValidation(False, match_count, True, "Locator is not unique in DOM.")
    return LocatorValidation(True, match_count, True, "Locator is unique and stable.")


def _parse_selenium_locator(locator: str) -> tuple[str, str] | None:
    text = locator.strip()
    patterns = [
        ("css", r"By\.(?:cssSelector|CSS_SELECTOR|css_selector)\((['\"])(.*)\1\)"),
        ("xpath", r"By\.(?:xpath|XPATH)\((['\"])(.*)\1\)"),
        ("id", r"By\.(?:id|ID)\((['\"])(.*)\1\)"),
        ("name", r"By\.(?:name|NAME)\((['\"])(.*)\1\)"),
    ]
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return kind, match.group(2)
    return None


def _count_playwright_locator(page: Page, metadata: Mapping[str, Any]) -> int:
    kind = str(metadata.get("playwright_kind") or "").strip()
    if not kind:
        return 0

    if kind == "test_id":
        return page.get_by_test_id(str(metadata.get("value") or "")).count()
    if kind == "label":
        return page.get_by_label(str(metadata.get("value") or ""), exact=True).count()
    if kind == "placeholder":
        return page.get_by_placeholder(str(metadata.get("value") or ""), exact=True).count()
    if kind == "role_name":
        return page.get_by_role(
            str(metadata.get("role") or ""),
            name=str(metadata.get("name") or ""),
            exact=True,
        ).count()
    if kind == "locator_has_text":
        return page.locator(
            str(metadata.get("tag") or "*"),
            has_text=str(metadata.get("text") or ""),
        ).count()
    return 0
