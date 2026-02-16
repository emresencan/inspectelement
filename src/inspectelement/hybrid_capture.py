from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CaptureCoordinates:
    x: int
    y: int
    source_width: int
    source_height: int


@dataclass(frozen=True, slots=True)
class PointProbe:
    status: str
    tag: str
    cross_origin_iframe: bool


@dataclass(frozen=True, slots=True)
class RefinedTargetChoice:
    index: int
    reason: str
    score: float


@dataclass(frozen=True, slots=True)
class RawRefinedChoice:
    raw_index: int
    refined_index: int
    refined_reason: str
    refined_score: float


@dataclass(frozen=True, slots=True)
class HoverBox:
    left: int
    top: int
    width: int
    height: int
    tag: str = ""
    element_id: str = ""
    class_name: str = ""
    text: str = ""


def normalize_viewport_size(
    width: int | float | None,
    height: int | float | None,
    *,
    default_width: int = 1280,
    default_height: int = 720,
) -> tuple[int, int]:
    try:
        resolved_width = int(width) if width is not None else default_width
    except (TypeError, ValueError):
        resolved_width = default_width
    try:
        resolved_height = int(height) if height is not None else default_height
    except (TypeError, ValueError):
        resolved_height = default_height

    return max(320, resolved_width), max(240, resolved_height)


def normalize_capture_coordinates(
    x: float | int,
    y: float | int,
    source_width: int,
    source_height: int,
) -> CaptureCoordinates:
    width, height = normalize_viewport_size(source_width, source_height)

    try:
        raw_x = int(round(float(x)))
    except (TypeError, ValueError):
        raw_x = 0
    try:
        raw_y = int(round(float(y)))
    except (TypeError, ValueError):
        raw_y = 0

    clamped_x = min(max(raw_x, 0), width - 1)
    clamped_y = min(max(raw_y, 0), height - 1)
    return CaptureCoordinates(
        x=clamped_x,
        y=clamped_y,
        source_width=width,
        source_height=height,
    )


def map_coordinates_to_viewport(
    point: CaptureCoordinates,
    target_width: int,
    target_height: int,
) -> tuple[int, int]:
    width, height = normalize_viewport_size(target_width, target_height)
    scale_x = width / max(point.source_width, 1)
    scale_y = height / max(point.source_height, 1)
    mapped_x = min(max(int(round(point.x * scale_x)), 0), width - 1)
    mapped_y = min(max(int(round(point.y * scale_y)), 0), height - 1)
    return mapped_x, mapped_y


def classify_probe_payload(raw: Mapping[str, Any] | None) -> PointProbe:
    if not isinstance(raw, Mapping):
        return PointProbe(status="none", tag="", cross_origin_iframe=False)

    status = str(raw.get("status", "") or "").strip().lower() or "none"
    tag = str(raw.get("tag", "") or "").strip().lower()
    cross_origin_iframe = bool(raw.get("cross_origin_iframe", False))
    return PointProbe(
        status=status,
        tag=tag,
        cross_origin_iframe=cross_origin_iframe,
    )


def should_sync_navigation(current_url: str, target_url: str) -> bool:
    current = (current_url or "").strip()
    target = (target_url or "").strip()
    if not target:
        return False
    return current != target


def map_hover_box_to_overlay(
    raw_box: Mapping[str, Any] | None,
    viewport_width: int,
    viewport_height: int,
) -> HoverBox | None:
    if not isinstance(raw_box, Mapping):
        return None
    width = max(1, int(viewport_width))
    height = max(1, int(viewport_height))
    try:
        left = int(round(float(raw_box.get("left", 0))))
        top = int(round(float(raw_box.get("top", 0))))
        box_width = int(round(float(raw_box.get("width", 0))))
        box_height = int(round(float(raw_box.get("height", 0))))
    except (TypeError, ValueError):
        return None

    if box_width <= 0 or box_height <= 0:
        return None

    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    max_width = width - left
    max_height = height - top
    box_width = max(1, min(box_width, max_width))
    box_height = max(1, min(box_height, max_height))

    return HoverBox(
        left=left,
        top=top,
        width=box_width,
        height=box_height,
        tag=str(raw_box.get("tag", "") or ""),
        element_id=str(raw_box.get("id", "") or ""),
        class_name=str(raw_box.get("class_name", "") or ""),
        text=str(raw_box.get("text", "") or ""),
    )


def select_refined_target_index(nodes: Sequence[Mapping[str, Any]]) -> RefinedTargetChoice:
    if not nodes:
        return RefinedTargetChoice(index=0, reason="fallback:no-nodes", score=0.0)

    top = nodes[0]
    top_is_wrapper = _is_generic_wrapper(top)

    best_index = _to_int(top.get("index"), default=0)
    best_score = float("-inf")
    best_reason = "fallback:first-node"

    best_actionable_index = best_index
    best_actionable_score = float("-inf")
    best_actionable_reason = ""

    for node in nodes:
        index = _to_int(node.get("index"), default=0)
        score, reasons = _score_refinement_candidate(node)
        if score > best_score:
            best_score = score
            best_index = index
            best_reason = ",".join(reasons) or "fallback:best-score"

        is_actionable = _as_bool(node.get("actionable")) or bool(str(node.get("clickable_ancestor") or "").strip())
        if is_actionable and score > best_actionable_score:
            best_actionable_score = score
            best_actionable_index = index
            best_actionable_reason = ",".join(reasons) or "actionable"

    if top_is_wrapper and best_actionable_score > (best_score - 8):
        return RefinedTargetChoice(
            index=best_actionable_index,
            reason=f"wrapper-refine:{best_actionable_reason}",
            score=best_actionable_score,
        )
    return RefinedTargetChoice(index=best_index, reason=best_reason, score=best_score)


def select_raw_and_refined_indices(nodes: Sequence[Mapping[str, Any]]) -> RawRefinedChoice:
    if not nodes:
        return RawRefinedChoice(raw_index=0, refined_index=0, refined_reason="fallback:no-nodes", refined_score=0.0)
    raw_index = _to_int(nodes[0].get("index"), default=0)
    refined = select_refined_target_index(nodes)
    return RawRefinedChoice(
        raw_index=raw_index,
        refined_index=refined.index,
        refined_reason=refined.reason,
        refined_score=refined.score,
    )


def _score_refinement_candidate(node: Mapping[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    idx = _to_int(node.get("index"), default=0)
    score += max(0.0, 32.0 - (idx * 3.0))
    reasons.append(f"stack-index:{idx}")

    if _as_bool(node.get("visible")) and not _as_bool(node.get("aria_hidden")):
        score += 30.0
        reasons.append("visible")
    else:
        score -= 140.0
        reasons.append("hidden")

    if _as_bool(node.get("rect_contains")):
        score += 24.0
        reasons.append("point-contained")

    tag = str(node.get("tag") or "").strip().lower()
    role = str(node.get("role") or "").strip().lower()
    if tag in {"a", "button", "input", "label", "summary"}:
        score += 42.0
        reasons.append(f"clickable-tag:{tag}")
    if role in {"button", "link", "menuitem", "tab"}:
        score += 24.0
        reasons.append(f"clickable-role:{role}")

    if _as_bool(node.get("actionable")):
        score += 50.0
        reasons.append("actionable")
    if _as_bool(node.get("has_onclick")):
        score += 18.0
        reasons.append("onclick")
    if _to_int(node.get("tab_index"), default=-1) >= 0:
        score += 12.0
        reasons.append("tabindex")
    if str(node.get("clickable_ancestor") or "").strip():
        score += 24.0
        reasons.append("clickable-ancestor")

    if _has_strong_attributes(node):
        score += 48.0
        reasons.append("strong-attrs")

    text = str(node.get("text") or "").strip()
    if 2 <= len(text) <= 90:
        score += 32.0
        reasons.append("meaningful-text")
    elif len(text) > 150:
        score -= 12.0
        reasons.append("long-text")

    if _is_generic_wrapper(node):
        score -= 92.0
        reasons.append("generic-wrapper")

    return score, reasons


def _has_strong_attributes(node: Mapping[str, Any]) -> bool:
    keys = (
        "id",
        "name",
        "aria_label",
        "data_testid",
        "data_test",
        "data_qa",
        "data_cy",
    )
    for key in keys:
        if str(node.get(key) or "").strip():
            return True
    return _as_bool(node.get("strong_attrs"))


def _is_generic_wrapper(node: Mapping[str, Any]) -> bool:
    if _as_bool(node.get("generic_wrapper")):
        return True
    tag = str(node.get("tag") or "").strip().lower()
    if tag not in {"div", "section", "article"}:
        return False
    marker = f"{node.get('id') or ''} {node.get('class_name') or ''}".lower()
    tokens = ("header", "container", "content", "wrapper", "modal", "shell", "panel", "overlay")
    return any(token in marker for token in tokens)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "y"}
    return False


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
