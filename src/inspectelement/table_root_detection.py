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
    stable: bool
    warning: str | None = None


def detect_table_root_from_ancestry(ancestry: Iterable[dict[str, str]]) -> TableRootCandidate | None:
    candidates = detect_table_root_candidates(ancestry)
    if not candidates:
        return None
    return candidates[0]


def detect_table_root_candidates(ancestry: Iterable[dict[str, str]]) -> list[TableRootCandidate]:
    ancestry_list = list(ancestry)
    ranked: list[tuple[int, int, TableRootCandidate]] = []
    for index, raw_node in enumerate(ancestry_list):
        node = _normalize_node(raw_node)
        if not _is_table_like(node):
            continue
        candidate = _build_candidate(node=node)
        ranked.append((_candidate_priority(candidate.reason), index, candidate))

    ranked.sort(key=lambda item: (item[0], item[1]))
    deduped: list[TableRootCandidate] = []
    seen: set[tuple[str, str]] = set()
    for _priority, _index, candidate in ranked:
        key = (candidate.selector_type, candidate.selector_value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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


def _build_candidate(node: dict[str, str]) -> TableRootCandidate:
    tag = node.get("tag", "div").lower() or "div"
    element_id = node.get("id", "").strip()
    if element_id:
        return TableRootCandidate(
            selector_type="id",
            selector_value=element_id,
            reason="id",
            tag=tag,
            locator_name_hint=_to_table_locator_name(element_id),
            stable=True,
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
            stable=True,
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
            stable=True,
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
                stable=False,
                warning="Unstable table root locator (class-based).",
            )

    xpath = _fallback_xpath(node)
    return TableRootCandidate(
        selector_type="xpath",
        selector_value=xpath,
        reason="fallback-xpath",
        tag=tag,
        locator_name_hint=_to_table_locator_name(tag),
        stable=False,
        warning="Unstable table root locator (xpath fallback).",
    )


def _fallback_xpath(node: dict[str, str]) -> str:
    tag = (node.get("tag", "div") or "div").lower()
    role = (node.get("role", "") or "").strip().lower()
    if role in {"table", "grid"}:
        return f"//{tag}[@role='{role}']"

    class_name = node.get("class", "").strip()
    token = _first_valid_class_token(class_name)
    if token:
        return f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), ' {token} ')]"

    return f"//{tag}"


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


def _candidate_priority(reason: str) -> int:
    if reason == "id":
        return 0
    if reason in STABLE_ATTR_KEYS:
        return 1
    if reason.startswith("role:"):
        return 2
    if reason == "class":
        return 3
    return 4
