from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

TABLE_LIKE_CLASS_TOKENS = ("table", "grid", "datatable", "ag-grid", "k-grid", "mat-table")
STABLE_ATTR_KEYS = ("data-testid", "data-test", "data-qa")


@dataclass(frozen=True, slots=True)
class TableRootCandidate:
    selector_type: str
    selector_value: str
    reason: str
    tag: str
    locator_name_hint: str


def detect_table_root_from_ancestry(ancestry: Iterable[dict[str, str]]) -> TableRootCandidate | None:
    ancestry_list = list(ancestry)
    for index, raw_node in enumerate(ancestry_list):
        node = _normalize_node(raw_node)
        if not _is_table_like(node):
            continue
        return _build_candidate(node=node, ancestry=ancestry_list, candidate_index=index)
    return None


def _normalize_node(node: dict[str, str]) -> dict[str, str]:
    return {str(key): str(value) for key, value in node.items() if value is not None}


def _is_table_like(node: dict[str, str]) -> bool:
    tag = node.get("tag", "").lower()
    role = node.get("role", "").lower()
    if tag == "table":
        return True
    if role in {"table", "grid"}:
        return True

    class_name = node.get("class", "").lower()
    element_id = node.get("id", "").lower()
    combined = f"{class_name} {element_id}"
    return any(token in combined for token in TABLE_LIKE_CLASS_TOKENS)


def _build_candidate(node: dict[str, str], ancestry: list[dict[str, str]], candidate_index: int) -> TableRootCandidate:
    tag = node.get("tag", "div").lower() or "div"
    element_id = node.get("id", "").strip()
    if element_id:
        return TableRootCandidate(
            selector_type="id",
            selector_value=element_id,
            reason="id",
            tag=tag,
            locator_name_hint=_to_table_locator_name(element_id),
        )

    for attr in STABLE_ATTR_KEYS:
        value = node.get(attr, "").strip()
        if not value:
            continue
        css = f"{tag}[{attr}='{_escape_css(value)}']"
        return TableRootCandidate(
            selector_type="css",
            selector_value=css,
            reason=attr,
            tag=tag,
            locator_name_hint=_to_table_locator_name(value),
        )

    role = node.get("role", "").strip().lower()
    if role in {"table", "grid"}:
        css = f"{tag}[role='{role}']"
        return TableRootCandidate(
            selector_type="css",
            selector_value=css,
            reason=f"role:{role}",
            tag=tag,
            locator_name_hint=_to_table_locator_name(role),
        )

    class_name = node.get("class", "").strip()
    if class_name:
        token = _first_valid_class_token(class_name)
        if token:
            css = f"{tag}.{token}"
            return TableRootCandidate(
                selector_type="css",
                selector_value=css,
                reason="class",
                tag=tag,
                locator_name_hint=_to_table_locator_name(token),
            )

    xpath = _fallback_xpath(ancestry, candidate_index)
    return TableRootCandidate(
        selector_type="xpath",
        selector_value=xpath,
        reason="fallback-xpath",
        tag=tag,
        locator_name_hint=_to_table_locator_name(tag),
    )


def _fallback_xpath(ancestry: list[dict[str, str]], candidate_index: int) -> str:
    node = ancestry[candidate_index]
    tag = (node.get("tag", "div") or "div").lower()
    role = (node.get("role", "") or "").strip().lower()
    if role in {"table", "grid"}:
        return f"//{tag}[@role='{role}']"

    steps: list[str] = []
    for path_node in ancestry[candidate_index : candidate_index + 4]:
        tag_name = (path_node.get("tag", "div") or "div").lower()
        nth = path_node.get("nth", "").strip()
        if nth.isdigit():
            steps.append(f"{tag_name}[{nth}]")
        else:
            steps.append(tag_name)
    if not steps:
        return f"//{tag}"
    return f"//{'/'.join(reversed(steps))}"


def _first_valid_class_token(class_name: str) -> str | None:
    for token in class_name.split():
        token = token.strip()
        if not token:
            continue
        if re.fullmatch(r"[a-zA-Z_-][a-zA-Z0-9_-]*", token):
            return token
    return None


def _to_table_locator_name(seed: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", seed).strip("_").upper()
    if not cleaned:
        cleaned = "TABLE"
    if cleaned[0].isdigit():
        cleaned = f"T_{cleaned}"
    if not cleaned.endswith("_TABLE"):
        cleaned = f"{cleaned}_TABLE"
    return cleaned


def _escape_css(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
