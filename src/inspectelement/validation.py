from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .action_catalog import has_table_actions, required_parameter_keys


@dataclass(frozen=True, slots=True)
class GenerationValidation:
    ok: bool
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
    if "timeoutSec" in required_params:
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
        r'By\.(cssSelector|xpath|id|name)\(\".*\"\)',
        value,
    ) is not None
