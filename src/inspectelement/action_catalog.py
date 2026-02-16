from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .java_pom_writer import build_action_method_signature_preview

ActionReturnKind = Literal["fluent", "string", "boolean"]
ActionCategory = Literal["Click", "Read", "State", "Scroll", "JS", "Table", "ComboBox"]
ActionAddTrigger = Literal["button_click", "checkbox_confirm", "hover", "mouse_move", "selection_change", "unknown"]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    key: str
    label: str
    category: ActionCategory
    description: str
    return_kind: ActionReturnKind
    parameter_keys: tuple[str, ...] = ()
    advanced: bool = False


@dataclass(frozen=True, slots=True)
class ActionSignaturePreview:
    key: str
    label: str
    signature: str
    return_kind: ActionReturnKind


ACTION_CATALOG: tuple[ActionSpec, ...] = (
    ActionSpec(
        key="clickElement",
        label="clickElement",
        category="Click",
        description="Standard click using BaseLibrary.",
        return_kind="fluent",
    ),
    ActionSpec(
        key="javaScriptClicker",
        label="javaScriptClicker",
        category="Click",
        description="JavaScript click fallback.",
        return_kind="fluent",
    ),
    ActionSpec(
        key="getText",
        label="getText",
        category="Read",
        description="Reads visible text.",
        return_kind="string",
    ),
    ActionSpec(
        key="getAttribute",
        label="getAttribute",
        category="Read",
        description="Reads selected attribute.",
        return_kind="string",
    ),
    ActionSpec(
        key="javaScriptGetInnerText",
        label="javaScriptGetInnerText",
        category="Read",
        description="Reads innerText via JavaScript.",
        return_kind="string",
    ),
    ActionSpec(
        key="javaScriptGetValue",
        label="javaScriptGetValue",
        category="Read",
        description="Reads value via JavaScript.",
        return_kind="string",
    ),
    ActionSpec(
        key="isElementDisplayed",
        label="isElementDisplayed",
        category="State",
        description="Checks displayed state with timeout.",
        return_kind="boolean",
        parameter_keys=("timeoutSec",),
    ),
    ActionSpec(
        key="isElementEnabled",
        label="isElementEnabled",
        category="State",
        description="Checks enabled state with timeout.",
        return_kind="boolean",
        parameter_keys=("timeoutSec",),
    ),
    ActionSpec(
        key="scrollToElement",
        label="scrollToElement",
        category="Scroll",
        description="Scrolls element into view.",
        return_kind="fluent",
    ),
    ActionSpec(
        key="javaScriptClearAndSetValue",
        label="javaScriptClearAndSetValue",
        category="JS",
        description="Clears and sets value via JavaScript.",
        return_kind="fluent",
    ),
    ActionSpec(
        key="tableAssertRowExists",
        label="assertRowExists",
        category="Table",
        description="Asserts a row exists by column header and expected text.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "columnHeader", "expectedText"),
    ),
    ActionSpec(
        key="tableHasAnyRow",
        label="hasAnyRow",
        category="Table",
        description="Returns whether table contains at least one data row.",
        return_kind="boolean",
        parameter_keys=("timeoutSec",),
    ),
    ActionSpec(
        key="tableAssertHasAnyRow",
        label="assertHasAnyRow",
        category="Table",
        description="Asserts table contains at least one data row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec",),
    ),
    ActionSpec(
        key="tableFilter",
        label="filter",
        category="Table",
        description="Applies column filter in table header.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "columnHeader", "filterText"),
    ),
    ActionSpec(
        key="tableAssertRowMatches",
        label="assertRowMatches",
        category="Table",
        description="Asserts row exists using custom predicate match on a column.",
        return_kind="fluent",
        parameter_keys=("timeoutSec",),
        advanced=True,
    ),
    ActionSpec(
        key="tableAssertRowAllEquals",
        label="assertRowAllEquals",
        category="Table",
        description="Asserts row exists using multiple column=value conditions map.",
        return_kind="fluent",
        parameter_keys=("timeoutSec",),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickInColumn",
        label="clickInColumn",
        category="Table",
        description="Clicks inner locator in target column for matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "columnHeader", "innerLocator"),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickInRow",
        label="clickInRow",
        category="Table",
        description="Clicks inner locator in matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "innerLocator"),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickButtonInRow",
        label="clickButtonInRow",
        category="Table",
        description="Clicks button locator in matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "innerLocator"),
        advanced=True,
    ),
    ActionSpec(
        key="tableSetInputInColumn",
        label="setInputInColumn",
        category="Table",
        description="Sets input text in target column for matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "columnHeader"),
        advanced=True,
    ),
    ActionSpec(
        key="tableAssertColumnTextEquals",
        label="assertColumnTextEquals",
        category="Table",
        description="Asserts matched row column text equals expected value.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "columnHeader", "expectedText"),
        advanced=True,
    ),
    ActionSpec(
        key="tableGetColumnText",
        label="getColumnText",
        category="Table",
        description="Returns target column text for matched row.",
        return_kind="string",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText", "columnHeader"),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickInFirstRow",
        label="clickInFirstRow",
        category="Table",
        description="Clicks inner locator in first visible table row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "innerLocator"),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickRadioInRow",
        label="clickRadioInRow",
        category="Table",
        description="Clicks radio input in matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText"),
        advanced=True,
    ),
    ActionSpec(
        key="tableClickLink",
        label="clickLink",
        category="Table",
        description="Clicks first link in matched row.",
        return_kind="fluent",
        parameter_keys=("timeoutSec", "matchType", "matchColumnHeader", "matchText"),
        advanced=True,
    ),
    ActionSpec(
        key="selectBySelectIdAuto",
        label="selectBySelectIdAuto",
        category="ComboBox",
        description="Selects option via UniversalSelectHelper.selectBySelectIdAuto.",
        return_kind="fluent",
        parameter_keys=("selectId", "waitBeforeSelect"),
    ),
    ActionSpec(
        key="selectByLabel",
        label="selectByLabel",
        category="ComboBox",
        description="Selects option by label via UniversalSelectHelper.selectByLabel.",
        return_kind="fluent",
        parameter_keys=("waitBeforeSelect",),
    ),
)

CATEGORY_FILTERS: tuple[str, ...] = ("All", "Click", "Read", "State", "Scroll", "JS", "Table", "ComboBox")

ACTION_PRESETS: dict[str, tuple[str, ...]] = {
    "Common UI": ("clickElement", "isElementDisplayed", "scrollToElement"),
    "Read": ("getText", "getAttribute", "javaScriptGetValue"),
    "JS": ("javaScriptClicker", "javaScriptClearAndSetValue", "javaScriptGetInnerText"),
    "Table Common": (
        "tableAssertRowExists",
        "tableHasAnyRow",
        "tableAssertHasAnyRow",
        "tableFilter",
        "tableAssertColumnTextEquals",
        "tableGetColumnText",
        "tableClickInFirstRow",
        "tableClickRadioInRow",
        "tableClickLink",
    ),
    "ComboBox": ("selectBySelectIdAuto",),
}

_ACTION_BY_KEY: dict[str, ActionSpec] = {spec.key: spec for spec in ACTION_CATALOG}
_EXPLICIT_ADD_TRIGGERS: frozenset[str] = frozenset({"button_click", "checkbox_confirm"})


def list_action_specs(include_advanced: bool = True) -> tuple[ActionSpec, ...]:
    if include_advanced:
        return ACTION_CATALOG
    return tuple(spec for spec in ACTION_CATALOG if not spec.advanced)


def get_action_spec(action_key: str) -> ActionSpec | None:
    return _ACTION_BY_KEY.get(action_key)


def action_label(action_key: str) -> str:
    spec = get_action_spec(action_key)
    if spec:
        return spec.label
    return action_key


def action_category(action_key: str) -> str:
    spec = get_action_spec(action_key)
    if not spec:
        return "Unknown"
    return spec.category


def return_kind_badge(return_kind: ActionReturnKind) -> str:
    if return_kind == "fluent":
        return "Fluent"
    if return_kind == "string":
        return "Returns String"
    return "Returns boolean"


def normalize_selected_actions(action_keys: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for key in action_keys:
        if key not in _ACTION_BY_KEY:
            continue
        if key in normalized:
            continue
        normalized.append(key)
    return normalized


def should_add_action_from_trigger(trigger: str) -> bool:
    return trigger in _EXPLICIT_ADD_TRIGGERS


def add_action_by_trigger(
    selected_actions: Iterable[str],
    action_key: str,
    trigger: ActionAddTrigger | str,
) -> list[str]:
    normalized = normalize_selected_actions(selected_actions)
    if not should_add_action_from_trigger(str(trigger)):
        return normalized
    if action_key not in _ACTION_BY_KEY:
        return normalized
    if action_key in normalized:
        return normalized
    return normalized + [action_key]


def has_table_actions(selected_actions: Iterable[str]) -> bool:
    return any(get_action_spec(action_key) and get_action_spec(action_key).category == "Table" for action_key in selected_actions)


def has_combo_actions(selected_actions: Iterable[str]) -> bool:
    return any(get_action_spec(action_key) and get_action_spec(action_key).category == "ComboBox" for action_key in selected_actions)


def required_parameter_keys(selected_actions: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for action_key in normalize_selected_actions(selected_actions):
        spec = get_action_spec(action_key)
        if not spec:
            continue
        for key in spec.parameter_keys:
            if key not in ordered:
                ordered.append(key)
    return tuple(ordered)


def filter_action_specs(
    search_text: str = "",
    category: str = "All",
    selected_actions: Iterable[str] | None = None,
    include_advanced: bool = False,
) -> list[ActionSpec]:
    selected = set(selected_actions or [])
    query = search_text.strip().lower()
    filtered: list[ActionSpec] = []
    for spec in ACTION_CATALOG:
        if spec.key in selected:
            continue
        if category != "All" and spec.category != category:
            continue
        if not include_advanced and spec.advanced:
            continue
        haystack = f"{spec.key} {spec.label} {spec.description} {spec.category}".lower()
        if query and query not in haystack:
            continue
        filtered.append(spec)
    return filtered


def build_signature_previews(
    page_class_name: str,
    locator_name: str,
    selected_actions: Iterable[str],
    *,
    table_locator_name: str | None = None,
    action_parameters: dict[str, str] | None = None,
) -> list[ActionSignaturePreview]:
    previews: list[ActionSignaturePreview] = []
    for key in normalize_selected_actions(selected_actions):
        spec = _ACTION_BY_KEY.get(key)
        if not spec:
            continue
        signature = build_action_method_signature_preview(
            page_class_name,
            locator_name,
            key,
            table_locator_name=table_locator_name,
            action_parameters=action_parameters or {},
        )
        if not signature:
            continue
        previews.append(
            ActionSignaturePreview(
                key=key,
                label=spec.label,
                signature=signature,
                return_kind=spec.return_kind,
            )
        )
    return previews
