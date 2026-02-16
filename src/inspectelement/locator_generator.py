from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from playwright.sync_api import ElementHandle, Page

from .models import ElementSummary, LocatorCandidate
from .selector_rules import ROOT_ID_BLOCKLIST_LOWER, is_blocked_root_id
from .scoring import score_candidates

_DYNAMIC_CLASS_PATTERNS = [
    re.compile(r"^css-[a-z0-9_-]{4,}$", re.IGNORECASE),
    re.compile(r"^jss\d+$", re.IGNORECASE),
    re.compile(r"^sc-[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE),
    re.compile(r"^[a-z]+__[a-z]+___[a-z0-9]{5,}$", re.IGNORECASE),
    re.compile(r"^_?[a-z]{1,3}[0-9a-f]{6,}$", re.IGNORECASE),
]

STABLE_ATTRS = ("data-testid", "data-test", "data-qa", "data-cy", "aria-label", "name", "id")
PROMOTABLE_STABLE_ATTRS = ("data-testid", "data-test", "data-qa", "data-cy", "id", "name", "aria-label")
_DYNAMIC_ID_TOKEN_PATTERNS = (
    re.compile(r"^jdt_\d+$", re.IGNORECASE),
    re.compile(r"^j_idt\d+$", re.IGNORECASE),
    re.compile(r"^\d+$"),
)


@dataclass(slots=True)
class CandidateDraft:
    locator_type: str
    locator: str
    rule: str
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_classes(raw: Sequence[str] | str | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        items = raw.split()
    else:
        items = [item for item in raw if isinstance(item, str)]

    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        clean = item.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def is_dynamic_class(class_name: str) -> bool:
    value = class_name.strip()
    if not value:
        return True
    for pattern in _DYNAMIC_CLASS_PATTERNS:
        if pattern.match(value):
            return True
    if len(value) > 18 and re.search(r"\d", value):
        return True
    if value.count("-") >= 3 and re.search(r"\d", value):
        return True
    return False


def is_dynamic_id(id_value: str) -> bool:
    value = id_value.strip()
    if not value or ":" not in value:
        return False
    if re.search(r":\d+:", value):
        return True

    tokens = [token for token in value.split(":") if token]
    for token in tokens:
        if any(pattern.fullmatch(token) for pattern in _DYNAMIC_ID_TOKEN_PATTERNS):
            return True
    return False


def extract_dynamic_id_prefix_suffix(id_value: str) -> tuple[str, str] | None:
    value = id_value.strip()
    if not is_dynamic_id(value):
        return None

    tokens = value.split(":")
    dynamic_indexes = [
        index
        for index, token in enumerate(tokens)
        if any(pattern.fullmatch(token) for pattern in _DYNAMIC_ID_TOKEN_PATTERNS)
    ]
    if not dynamic_indexes:
        return None

    last_dynamic_index = dynamic_indexes[-1]
    if last_dynamic_index <= 0:
        return None

    prefix = ":".join(tokens[:last_dynamic_index]) + ":"
    if not tokens[-1]:
        return None
    suffix = f":{tokens[-1]}"
    return prefix, suffix


def build_dynamic_id_partial_locators(id_value: str) -> tuple[str, str] | None:
    parts = extract_dynamic_id_prefix_suffix(id_value)
    if not parts:
        return None

    prefix, suffix = parts
    css = f'[id^="{_escape_css_string(prefix)}"][id$="{_escape_css_string(suffix)}"]'
    xpath = (
        "//*["
        f"starts-with(@id,{_xpath_literal(prefix)}) "
        "and "
        f"substring(@id, string-length(@id) - string-length({_xpath_literal(suffix)}) + 1) = {_xpath_literal(suffix)}"
        "]"
    )
    return css, xpath


def _escape_css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_css_identifier(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        if char.isalnum() or char in ("-", "_"):
            escaped.append(char)
        else:
            escaped.append(f"\\{ord(char):x} ")
    return "".join(escaped)


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    pieces = value.split("'")
    quoted = [f"'{piece}'" for piece in pieces]
    return "concat(" + ", \"'\", ".join(quoted) + ")"


def _short_text(value: str | None, limit: int = 80) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        return None
    return compact[:limit]


def _add_unique(drafts: list[CandidateDraft], draft: CandidateDraft, seen: set[tuple[str, str]]) -> None:
    key = (draft.locator_type, draft.locator)
    if key in seen:
        return
    seen.add(key)
    drafts.append(draft)


def _count_matches(page: Page, draft: CandidateDraft) -> int:
    try:
        if draft.locator_type == "CSS":
            return len(page.query_selector_all(draft.locator))
        if draft.locator_type == "XPath":
            return page.locator(f"xpath={draft.locator}").count()
        if draft.locator_type == "Selenium":
            selector_kind = draft.metadata.get("selector_kind", "css")
            selector_value = draft.metadata.get("selector_value", "")
            if selector_kind == "xpath":
                return page.locator(f"xpath={selector_value}").count()
            if selector_kind == "id":
                css = f'[id="{_escape_css_string(selector_value)}"]'
                return len(page.query_selector_all(css))
            if selector_kind == "name":
                css = f'[name="{_escape_css_string(selector_value)}"]'
                return len(page.query_selector_all(css))
            return len(page.query_selector_all(selector_value))
        if draft.locator_type == "Playwright":
            kind = draft.metadata.get("playwright_kind")
            if kind == "test_id":
                return page.get_by_test_id(draft.metadata["value"]).count()
            if kind == "label":
                return page.get_by_label(draft.metadata["value"], exact=True).count()
            if kind == "placeholder":
                return page.get_by_placeholder(draft.metadata["value"], exact=True).count()
            if kind == "role_name":
                return page.get_by_role(draft.metadata["role"], name=draft.metadata["name"], exact=True).count()
            if kind == "locator_has_text":
                return page.locator(draft.metadata["tag"], has_text=draft.metadata["text"]).count()
    except Exception:
        return 0
    return 0


def _nearest_stable_ancestor(element: ElementHandle) -> dict[str, str] | None:
    return element.evaluate(
        """
        (el) => {
          const attrs = ['data-testid', 'data-test', 'data-qa', 'data-cy', 'aria-label', 'name'];
          let current = el.parentElement;
          while (current) {
            for (const attr of attrs) {
              const value = current.getAttribute(attr);
              if (value) {
                return {
                  tag: current.tagName.toLowerCase(),
                  attr,
                  value,
                };
              }
            }
            current = current.parentElement;
          }
          return null;
        }
        """
    )


def _nth_fallback_path(element: ElementHandle) -> str:
    return element.evaluate(
        """
        (el) => {
          const parts = [];
          let current = el;
          while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 7) {
            const tag = current.tagName.toLowerCase();
            if (current.id) {
              parts.unshift(`#${CSS.escape(current.id)}`);
              break;
            }
            let nth = 1;
            let sibling = current;
            while ((sibling = sibling.previousElementSibling)) {
              if (sibling.tagName.toLowerCase() === tag) {
                nth += 1;
              }
            }
            parts.unshift(`${tag}:nth-of-type(${nth})`);
            current = current.parentElement;
          }
          return parts.join(' > ');
        }
        """
    )


def _stable_attr_css(tag: str, attr: str, value: str) -> str:
    if attr == "id":
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", value):
            return f"#{_escape_css_identifier(value)}"
        return f'{tag}[id="{_escape_css_string(value)}"]'
    return f'{tag}[{attr}="{_escape_css_string(value)}"]'


def _is_blocked_id(tag: str, value: str) -> bool:
    normalized_tag = tag.strip().lower()
    if normalized_tag in {"html", "body"}:
        return True
    if is_blocked_root_id(value):
        return True
    return False


def _build_stable_attr_drafts(tag: str, attr: str, value: str) -> list[CandidateDraft]:
    if attr == "id" and _is_blocked_id(tag, value):
        return []

    css = _stable_attr_css(tag, attr, value)
    drafts: list[CandidateDraft] = []

    def add_css(value_css: str) -> None:
        drafts.append(CandidateDraft(locator_type="CSS", locator=value_css, rule=f"stable_attr:{attr}"))
        drafts.append(
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{value_css}")',
                rule=f"stable_attr:{attr}",
                metadata={"selector_kind": "css", "selector_value": value_css},
            )
        )

    add_css(css)
    if attr == "id":
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", value):
            id_selector = f"#{_escape_css_identifier(value)}"
            tag_id_selector = f"{tag}#{_escape_css_identifier(value)}"
            if id_selector != css:
                add_css(id_selector)
            if tag_id_selector != css and tag_id_selector != id_selector:
                add_css(tag_id_selector)
    if attr == "name":
        add_css(f'[name="{_escape_css_string(value)}"]')
        if tag == "input":
            add_css(f'input[name="{_escape_css_string(value)}"]')
    drafts.append(
        CandidateDraft(
            locator_type="XPath",
            locator=f"//*[@{attr}={_xpath_literal(value)}]",
            rule=f"stable_attr:{attr}",
        )
    )
    if attr in {"data-testid", "data-test", "data-qa", "data-cy"}:
        bare_css = f'[{attr}="{_escape_css_string(value)}"]'
        add_css(bare_css)
    if attr == "id":
        drafts.append(
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.ID("{value}")',
                rule="stable_attr:id",
                metadata={"selector_kind": "id", "selector_value": value},
            )
        )
    if attr == "name":
        drafts.append(
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.NAME("{value}")',
                rule="stable_attr:name",
                metadata={"selector_kind": "name", "selector_value": value},
            )
        )
    if attr in {"data-testid", "data-test", "data-qa", "data-cy"}:
        drafts.append(
            CandidateDraft(
                locator_type="Playwright",
                locator=f'page.get_by_test_id("{value}")',
                rule=f"stable_attr:{attr}",
                metadata={"playwright_kind": "test_id", "value": value},
            )
        )
    if attr == "aria-label":
        drafts.append(
            CandidateDraft(
                locator_type="Playwright",
                locator=f'page.get_by_label("{value}", exact=True)',
                rule="stable_attr:aria-label",
                metadata={"playwright_kind": "label", "value": value},
            )
        )
    return drafts


def _find_clickable_ancestor_snapshot(element: ElementHandle) -> dict[str, Any] | None:
    return element.evaluate(
        """
        (el) => {
          const attrs = ['data-testid', 'data-test', 'data-qa', 'data-cy', 'id', 'name', 'aria-label'];
          let current = el;
          while (current && current.nodeType === Node.ELEMENT_NODE) {
            const tag = current.tagName.toLowerCase();
            const role = (current.getAttribute('role') || '').toLowerCase();
            const inputType = (current.getAttribute('type') || '').toLowerCase();
            const clickableInput = tag === 'input' && ['button', 'submit', 'reset'].includes(inputType);
            const clickableRole = ['button', 'tab', 'link'].includes(role);
            const clickable = tag === 'a' || tag === 'button' || clickableInput || clickableRole;
            if (clickable) {
              const found = {};
              for (const attr of attrs) {
                const value = current.getAttribute(attr);
                if (value) {
                  found[attr] = value;
                }
              }
              return { tag, role, inputType, attrs: found };
            }
            current = current.parentElement;
          }
          return null;
        }
        """
    )


def _is_clickable_ancestor_snapshot(snapshot: dict[str, Any]) -> bool:
    tag = str(snapshot.get("tag") or "").strip().lower()
    role = str(snapshot.get("role") or "").strip().lower()
    input_type = str(snapshot.get("inputType") or "").strip().lower()
    if tag in {"a", "button"}:
        return True
    if tag == "input" and input_type in {"button", "submit", "reset"}:
        return True
    return role in {"button", "tab", "link"}


def _build_promoted_clickable_ancestor_drafts(page: Page, element: ElementHandle) -> list[CandidateDraft] | None:
    snapshot = _find_clickable_ancestor_snapshot(element)
    if not snapshot:
        return None
    if not _is_clickable_ancestor_snapshot(snapshot):
        return None

    tag = str(snapshot.get("tag") or "").strip().lower()
    attrs = snapshot.get("attrs") or {}
    if not tag or not isinstance(attrs, dict):
        return None

    for attr in PROMOTABLE_STABLE_ATTRS:
        value = attrs.get(attr)
        if not value or not isinstance(value, str):
            continue
        if attr == "id":
            if is_dynamic_id(value) or _is_blocked_id(tag, value):
                continue

        css = _stable_attr_css(tag, attr, value)
        try:
            if len(page.query_selector_all(css)) != 1:
                continue
        except Exception:
            continue
        drafts = _build_stable_attr_drafts(tag, attr, value)
        if attr == "id":
            compact_css = f"#{_escape_css_identifier(value)}"
            filtered: list[CandidateDraft] = []
            for draft in drafts:
                if draft.locator_type == "CSS" and draft.locator != compact_css:
                    continue
                if (
                    draft.locator_type == "Selenium"
                    and draft.metadata.get("selector_kind") == "css"
                    and str(draft.metadata.get("selector_value", "")) != compact_css
                ):
                    continue
                filtered.append(draft)
            return filtered
        if attr in {"data-testid", "data-test", "data-qa", "data-cy"}:
            filtered: list[CandidateDraft] = []
            for draft in drafts:
                if draft.locator_type == "CSS" and draft.locator.startswith("["):
                    continue
                if draft.locator_type == "Selenium" and str(draft.metadata.get("selector_value", "")).startswith("["):
                    continue
                filtered.append(draft)
            return filtered
        return drafts
    return None


def extract_css_parent_if_descendant(locator: str) -> str | None:
    in_quote: str | None = None
    bracket_depth = 0
    paren_depth = 0
    split_index = -1

    for index, char in enumerate(locator):
        if in_quote:
            if char == in_quote and locator[index - 1] != "\\":
                in_quote = None
            continue
        if char in {"'", '"'}:
            in_quote = char
            continue
        if char == "[":
            bracket_depth += 1
            continue
        if char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            continue
        if char == "(":
            paren_depth += 1
            continue
        if char == ")":
            paren_depth = max(0, paren_depth - 1)
            continue
        if bracket_depth > 0 or paren_depth > 0 or not char.isspace():
            continue

        left = index - 1
        while left >= 0 and locator[left].isspace():
            left -= 1
        right = index + 1
        while right < len(locator) and locator[right].isspace():
            right += 1
        if left < 0 or right >= len(locator):
            continue
        if locator[left] in {">", "+", "~", ","} or locator[right] in {">", "+", "~", ","}:
            continue
        split_index = index

    if split_index < 0:
        return None
    parent = locator[:split_index].rstrip()
    child = locator[split_index:].strip()
    if not parent or not child:
        return None
    return parent


def _prune_descendant_css_locator(page: Page, locator: str) -> str:
    parent = extract_css_parent_if_descendant(locator)
    if not parent:
        return locator
    parent_lower = parent.strip().lower()
    if parent_lower in {"html", "body"}:
        return locator
    if parent.startswith("#"):
        parent_id = parent[1:].strip().lower()
        if parent_id in ROOT_ID_BLOCKLIST_LOWER:
            return locator
    try:
        if len(page.query_selector_all(parent)) == 1:
            return parent
    except Exception:
        return locator
    return locator


def _prune_descendant_css_drafts(page: Page, drafts: Iterable[CandidateDraft]) -> list[CandidateDraft]:
    pruned: list[CandidateDraft] = []
    seen: set[tuple[str, str]] = set()
    for draft in drafts:
        updated = draft
        if draft.locator_type == "CSS":
            locator = _prune_descendant_css_locator(page, draft.locator)
            if locator != draft.locator:
                metadata = dict(draft.metadata)
                metadata["descendant_pruned"] = True
                updated = CandidateDraft(
                    locator_type=draft.locator_type,
                    locator=locator,
                    rule=draft.rule,
                    metadata=metadata,
                )

        key = (updated.locator_type, updated.locator)
        if key in seen:
            continue
        seen.add(key)
        pruned.append(updated)
    return pruned


def _ensure_xpath_text_in_results(
    scored: list[LocatorCandidate],
    summary: ElementSummary,
    limit: int,
) -> list[LocatorCandidate]:
    if limit <= 0:
        return []

    top = scored[:limit]
    if not _short_text(summary.text):
        return top

    has_xpath_text = any(
        candidate.rule in {"xpath_text", "xpath_text_exact"} and candidate.locator_type == "XPath"
        for candidate in top
    )
    if has_xpath_text:
        return top

    best_xpath_text = next(
        (
            candidate
            for candidate in scored
            if candidate.rule in {"xpath_text", "xpath_text_exact"} and candidate.locator_type == "XPath"
        ),
        None,
    )
    if not best_xpath_text:
        return top
    if not top:
        return [best_xpath_text]

    return top[:-1] + [best_xpath_text]


def _xpath_tag(tag: str | None) -> str:
    cleaned = (tag or "").strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]*", cleaned):
        return cleaned
    return "*"


def _has_ant_modal_context(summary: ElementSummary) -> bool:
    if "ant-modal" in (summary.outer_html or "").lower():
        return True
    attr_class = (summary.attributes.get("class") or "").lower()
    if "ant-modal" in attr_class:
        return True
    for node in summary.ancestry:
        class_name = str(node.get("class", "") or "").lower()
        aria_hidden = str(node.get("aria-hidden", "") or "").strip().lower()
        style = str(node.get("style", "") or "").strip().lower()
        if "ant-modal" in class_name and aria_hidden != "true" and "display:none" not in style:
            return True
    return False


def _build_attribute_fallback_drafts(
    summary: ElementSummary,
    tag: str,
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> None:
    for attr in ("placeholder", "title", "role", "type", "href", "aria-labelledby", "alt"):
        value = (summary.attributes.get(attr) or "").strip()
        if not value:
            continue
        if attr == "href" and tag != "a":
            continue

        css = f'{tag}[{attr}="{_escape_css_string(value)}"]'
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="CSS",
                locator=css,
                rule=f"attr:{attr}",
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{css}")',
                rule=f"attr:{attr}",
                metadata={"selector_kind": "css", "selector_value": css},
            ),
            seen,
        )
        if attr in {"role", "type"}:
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="XPath",
                    locator=f"//{_xpath_tag(tag)}[@{attr}={_xpath_literal(value)}]",
                    rule=f"attr:{attr}",
                ),
                seen,
            )


def _build_role_label_drafts(
    summary: ElementSummary,
    tag: str,
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> None:
    role_value = (summary.attributes.get("role") or summary.role or "").strip()
    aria_label = (summary.attributes.get("aria-label") or summary.aria_label or "").strip()
    if role_value and aria_label:
        css = f'{tag}[role="{_escape_css_string(role_value)}"][aria-label="{_escape_css_string(aria_label)}"]'
        _add_unique(
            drafts,
            CandidateDraft(locator_type="CSS", locator=css, rule="attr:role"),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{css}")',
                rule="attr:role",
                metadata={"selector_kind": "css", "selector_value": css},
            ),
            seen,
        )


def _is_wrapper_token_value(value: str) -> bool:
    lowered = value.lower()
    tokens = ("modal", "modals", "container", "content", "wrapper", "header", "shell", "overlay", "layout")
    return any(token in lowered for token in tokens)


def _build_icon_css_drafts(
    summary: ElementSummary,
    tag: str,
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> None:
    if tag not in {"svg", "path", "use", "i", "span"}:
        return
    icon_tokens = [token for token in summary.classes if not is_dynamic_class(token)]
    icon_token = icon_tokens[0] if icon_tokens else ""
    for node in summary.ancestry[1:]:
        ancestor_tag = _xpath_tag(str(node.get("tag", "") or ""))
        if ancestor_tag not in {"button", "a"}:
            continue
        class_name = str(node.get("class", "") or "")
        ancestor_tokens = [token for token in normalize_classes(class_name) if not is_dynamic_class(token)]
        if not ancestor_tokens:
            continue
        ancestor_token = ancestor_tokens[0]
        if icon_token:
            css = f"{ancestor_tag}.{_escape_css_identifier(ancestor_token)} {tag}.{_escape_css_identifier(icon_token)}"
        else:
            css = f"{ancestor_tag}.{_escape_css_identifier(ancestor_token)} {tag}"
        _add_unique(
            drafts,
            CandidateDraft(locator_type="CSS", locator=css, rule="meaningful_class"),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{css}")',
                rule="meaningful_class",
                metadata={"selector_kind": "css", "selector_value": css},
            ),
            seen,
        )
        return


def _build_text_xpath_drafts(
    summary: ElementSummary,
    tag: str,
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> str | None:
    short_text = _short_text(summary.text) or _short_text(summary.attributes.get("value"), 120)
    if not short_text:
        return None

    xpath_tag = _xpath_tag(tag)
    text_literal = _xpath_literal(short_text)
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=f"//{xpath_tag}[text()={text_literal}]",
            rule="xpath_text_exact",
        ),
        seen,
    )
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=f"//{xpath_tag}[normalize-space(.)={text_literal}]",
            rule="xpath_text",
        ),
        seen,
    )
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=f"(//*[self::button or self::span or self::a][normalize-space(text())={text_literal}])[1]",
            rule="xpath_text_clickable",
        ),
        seen,
    )
    if len(short_text) > 12:
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=f"//{xpath_tag}[contains(normalize-space(.), {_xpath_literal(short_text[:32])})]",
                rule="xpath_text_contains",
            ),
            seen,
        )

    if _has_ant_modal_context(summary):
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=(
                    f"(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]"
                    f"//{xpath_tag}[normalize-space(.)={text_literal}])[1]"
                ),
                rule="xpath_modal_text",
                metadata={"modal_safe": True},
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=(
                    f"(//div[contains(@class,'ant-modal') and not(contains(@style,'display:none'))]"
                    f"//{xpath_tag}[normalize-space(.)={text_literal}])[1]"
                ),
                rule="xpath_modal_text",
                metadata={"modal_safe": True},
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=(
                    "(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]"
                    f"//button[.//span[normalize-space(.)={text_literal}]])[1]"
                ),
                rule="xpath_modal_text",
                metadata={"modal_safe": True},
            ),
            seen,
        )
    if tag == "label":
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=f".//label[contains(normalize-space(.),{_xpath_literal(short_text)})]",
                rule="xpath_label_contains",
            ),
            seen,
        )
    return short_text


def _build_following_sibling_drafts(
    summary: ElementSummary,
    tag: str,
    meaningful_classes: list[str],
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> None:
    sibling_text = _short_text(summary.sibling_label_text or summary.attributes.get("__prev_sibling_text"), 120)
    if not sibling_text:
        return
    class_token = meaningful_classes[0] if meaningful_classes else ""
    if not class_token:
        return
    xpath_tag = _xpath_tag(tag)
    sibling_literal = _xpath_literal(sibling_text)
    class_literal = _xpath_literal(class_token)
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=(
                f"//{xpath_tag}[text()={sibling_literal}]"
                f"/following-sibling::{xpath_tag}[contains(@class,{class_literal})]"
            ),
            rule="xpath_following_sibling",
        ),
        seen,
    )
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=(
                f"//{xpath_tag}[normalize-space(.)={sibling_literal}]"
                "/following-sibling::*[contains(@class,"
                f"{class_literal})]"
            ),
            rule="xpath_following_sibling",
        ),
        seen,
    )
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=f"//*[normalize-space(.)={sibling_literal}]/ancestor::*[1]//input",
            rule="xpath_ancestor_context",
        ),
        seen,
    )
    _add_unique(
        drafts,
        CandidateDraft(
            locator_type="XPath",
            locator=(
                f"//{xpath_tag}[normalize-space(.)={sibling_literal}]"
                f"/following-sibling::{xpath_tag}[contains(@class,{class_literal})]"
            ),
            rule="xpath_following_sibling",
        ),
        seen,
    )


def _build_ancestor_context_drafts(
    summary: ElementSummary,
    tag: str,
    drafts: list[CandidateDraft],
    seen: set[tuple[str, str]],
) -> None:
    short_text = _short_text(summary.text) or _short_text(summary.attributes.get("value"), 120)
    if not short_text:
        return
    text_literal = _xpath_literal(short_text)
    xpath_tag = _xpath_tag(tag)
    for node in summary.ancestry[1:]:
        for attr in ("data-testid", "data-test", "data-qa", "data-cy", "id", "role"):
            value = (node.get(attr) or "").strip()
            if not value:
                continue
            if attr == "id" and is_blocked_root_id(value):
                continue
            if _is_wrapper_token_value(value):
                continue
            anchor = f"//*[@{attr}={_xpath_literal(value)}]"
            locator = f"{anchor}//{xpath_tag}[normalize-space(.)={text_literal}]"
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="XPath",
                    locator=locator,
                    rule="xpath_ancestor_context",
                    metadata={"wrapper_based": False},
                ),
                seen,
            )
            return


def _build_xpath_fallback_from_ancestry(summary: ElementSummary) -> str | None:
    if not summary.ancestry:
        return None
    parts: list[str] = []
    for node in reversed(summary.ancestry):
        tag = _xpath_tag(str(node.get("tag", "") or ""))
        nth = str(node.get("nth", "") or "").strip()
        if nth.isdigit() and int(nth) > 0:
            parts.append(f"{tag}[{nth}]")
        else:
            parts.append(tag)
    if not parts:
        return None
    return "/" + "/".join(parts)


def _build_clickable_union_xpath_draft(
    page: Page,
    element: ElementHandle,
    summary: ElementSummary,
) -> CandidateDraft | None:
    short_text = _short_text(summary.text) or _short_text(summary.attributes.get("value"), 120)
    if not short_text:
        return None
    text_literal = _xpath_literal(short_text)
    base = f"//*[self::button or self::a or self::span][normalize-space(.)={text_literal}]"

    try:
        count = page.locator(f"xpath={base}").count()
    except Exception:
        count = 0
    if count <= 0:
        return None

    if count == 1:
        return CandidateDraft(
            locator_type="XPath",
            locator=base,
            rule="xpath_text_clickable_union",
            metadata={"union_xpath": True},
        )

    index = 1
    try:
        result = element.evaluate(
            """
            (el, text) => {
              const normalize = (value) => (value || '').trim().replace(/\\s+/g, ' ');
              const targetText = normalize(text);
              const nodes = Array.from(document.querySelectorAll('button,a,span')).filter(
                (node) => normalize(node.innerText || node.textContent || '') === targetText
              );
              if (!nodes.length) return 1;
              const clickableAncestor = el.closest ? el.closest('button,a,span') : null;
              let resolved = clickableAncestor || el;
              for (let i = 0; i < nodes.length; i += 1) {
                const node = nodes[i];
                if (node === resolved || node.contains(resolved) || resolved.contains(node)) {
                  return i + 1;
                }
              }
              return 1;
            }
            """,
            short_text,
        )
        parsed = int(result)
        if parsed > 0:
            index = parsed
    except Exception:
        index = 1

    return CandidateDraft(
        locator_type="XPath",
        locator=f"({base})[{index}]",
        rule="xpath_text_clickable_union",
        metadata={"union_xpath": True, "uses_index": True, "xpath_index": index},
    )


def build_candidate_drafts_from_summary(summary: ElementSummary) -> list[CandidateDraft]:
    drafts: list[CandidateDraft] = []
    seen: set[tuple[str, str]] = set()
    tag = _xpath_tag(summary.tag)

    for attr in STABLE_ATTRS:
        value = summary.attributes.get(attr)
        if not value:
            continue

        if attr == "id" and is_dynamic_id(value):
            partial_locators = build_dynamic_id_partial_locators(value)
            if partial_locators:
                partial_css, partial_xpath = partial_locators
                _add_unique(
                    drafts,
                    CandidateDraft(
                        locator_type="CSS",
                        locator=partial_css,
                        rule="stable_attr:id_partial",
                    ),
                    seen,
                )
                _add_unique(
                    drafts,
                    CandidateDraft(
                        locator_type="XPath",
                        locator=partial_xpath,
                        rule="stable_attr:id_partial",
                    ),
                    seen,
                )
            continue

        for draft in _build_stable_attr_drafts(tag, attr, value):
            _add_unique(drafts, draft, seen)

    meaningful_classes = [name for name in summary.classes if not is_dynamic_class(name)]
    dynamic_count = len(summary.classes) - len(meaningful_classes)
    if meaningful_classes:
        class_selector = "".join(f".{_escape_css_identifier(name)}" for name in meaningful_classes[:2])
        css = f"{tag}{class_selector}"
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="CSS",
                locator=css,
                rule="meaningful_class",
                metadata={"dynamic_class_count": dynamic_count},
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{css}")',
                rule="meaningful_class",
                metadata={
                    "selector_kind": "css",
                    "selector_value": css,
                    "dynamic_class_count": dynamic_count,
                },
            ),
            seen,
        )
        if _has_ant_modal_context(summary) and tag == "label" and "ant-radio-wrapper" in " ".join(meaningful_classes):
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="XPath",
                    locator=(
                        "//div[contains(@class,'ant-modal') and not(contains(@style,'display:none'))]"
                        "//label[contains(@class,'ant-radio-wrapper')]"
                    ),
                    rule="xpath_modal_text",
                    metadata={"modal_safe": True},
                ),
                seen,
            )
    _build_icon_css_drafts(summary, tag, drafts, seen)

    _build_attribute_fallback_drafts(summary, tag, drafts, seen)
    _build_role_label_drafts(summary, tag, drafts, seen)
    short_text = _build_text_xpath_drafts(summary, tag, drafts, seen)
    _build_ancestor_context_drafts(summary, tag, drafts, seen)

    if short_text and tag in {"button", "a", "span"}:
        role = summary.role or ("button" if tag == "button" else "link")
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Playwright",
                locator=f'page.get_by_role("{role}", name="{short_text}", exact=True)',
                rule="text_role",
                metadata={"playwright_kind": "role_name", "role": role, "name": short_text},
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Playwright",
                locator=f'page.locator("{tag}", has_text="{short_text}")',
                rule="text_role",
                metadata={"playwright_kind": "locator_has_text", "tag": tag, "text": short_text},
            ),
            seen,
        )

    if tag in {"input", "textarea", "select"}:
        name_value = (summary.attributes.get("name") or "").strip()
        type_value = (summary.attributes.get("type") or "").strip()
        if tag == "input" and name_value:
            selector = f'input[name="{_escape_css_string(name_value)}"]'
            _add_unique(
                drafts,
                CandidateDraft(locator_type="CSS", locator=selector, rule="stable_attr:name"),
                seen,
            )
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="Selenium",
                    locator=f'By.CSS_SELECTOR("{selector}")',
                    rule="stable_attr:name",
                    metadata={"selector_kind": "css", "selector_value": selector},
                ),
                seen,
            )
            if type_value:
                combo = (
                    f'input[type="{_escape_css_string(type_value)}"]'
                    f'[name="{_escape_css_string(name_value)}"]'
                )
                _add_unique(
                    drafts,
                    CandidateDraft(locator_type="CSS", locator=combo, rule="attr:type"),
                    seen,
                )
                _add_unique(
                    drafts,
                    CandidateDraft(
                        locator_type="Selenium",
                        locator=f'By.CSS_SELECTOR("{combo}")',
                        rule="attr:type",
                        metadata={"selector_kind": "css", "selector_value": combo},
                    ),
                    seen,
                )
        if summary.placeholder:
            value = _short_text(summary.placeholder, 120)
            if value:
                _add_unique(
                    drafts,
                    CandidateDraft(
                        locator_type="Playwright",
                        locator=f'page.get_by_placeholder("{value}", exact=True)',
                        rule="placeholder",
                        metadata={"playwright_kind": "placeholder", "value": value},
                    ),
                    seen,
                )
        if summary.label_text:
            label = _short_text(summary.label_text, 120)
            if label:
                _add_unique(
                    drafts,
                    CandidateDraft(
                        locator_type="Playwright",
                        locator=f'page.get_by_label("{label}", exact=True)',
                        rule="label_assoc",
                        metadata={"playwright_kind": "label", "value": label},
                    ),
                    seen,
                )

    _build_following_sibling_drafts(summary, tag, meaningful_classes, drafts, seen)
    fallback_xpath = _build_xpath_fallback_from_ancestry(summary)
    if fallback_xpath:
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=fallback_xpath,
                rule="xpath_fallback",
                metadata={"uses_index": True, "wrapper_based": True, "risky": True},
            ),
            seen,
        )
    return drafts


def _build_candidate_drafts(page: Page, element: ElementHandle, summary: ElementSummary) -> list[CandidateDraft]:
    drafts = build_candidate_drafts_from_summary(summary)
    seen: set[tuple[str, str]] = {(draft.locator_type, draft.locator) for draft in drafts}
    tag = _xpath_tag(summary.tag)
    meaningful_classes = [name for name in summary.classes if not is_dynamic_class(name)]

    union_draft = _build_clickable_union_xpath_draft(page=page, element=element, summary=summary)
    if union_draft:
        _add_unique(drafts, union_draft, seen)

    ancestor = _nearest_stable_ancestor(element)
    if ancestor:
        ancestor_selector = _stable_attr_css(ancestor["tag"], ancestor["attr"], ancestor["value"])
        descendant = tag
        if meaningful_classes:
            descendant = f"{tag}.{_escape_css_identifier(meaningful_classes[0])}"
        css = f"{ancestor_selector} {descendant}"
        _add_unique(
            drafts,
            CandidateDraft(locator_type="CSS", locator=css, rule="ancestor"),
            seen,
        )

    fallback = _nth_fallback_path(element)
    if fallback:
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="CSS",
                locator=fallback,
                rule="nth_fallback",
                metadata={"uses_nth": True},
            ),
            seen,
        )

    return drafts


def _validate_drafts(page: Page, drafts: Iterable[CandidateDraft]) -> list[LocatorCandidate]:
    candidates: list[LocatorCandidate] = []
    for draft in drafts:
        if _should_drop_xpath_spam(draft):
            continue
        count = _count_matches(page, draft)
        candidates.append(
            LocatorCandidate(
                locator_type=draft.locator_type,  # type: ignore[arg-type]
                locator=draft.locator,
                rule=draft.rule,
                uniqueness_count=count,
                metadata=draft.metadata,
            )
        )
    return candidates


def _should_drop_xpath_spam(draft: CandidateDraft) -> bool:
    if draft.locator_type != "XPath":
        return False
    locator = draft.locator.strip()
    lowered = locator.lower()
    if draft.rule in {"xpath_fallback", "nth_fallback"}:
        return False
    if draft.metadata.get("modal_safe"):
        return False

    if lowered.startswith("//*") and not any(token in lowered for token in ("@id", "@name", "@data-", "text()", "normalize-space")):
        return True

    raw_steps = [segment for segment in locator.split("/") if segment and segment not in {"(", ")"}]
    if len(raw_steps) > 6 and draft.rule in {"xpath_ancestor_context", "xpath_text_contains"}:
        return True

    if _is_wrapper_xpath(lowered) and draft.rule in {"xpath_ancestor_context", "xpath_fallback"}:
        return True

    class_contains = re.findall(r"contains\s*\(\s*@class\s*,\s*['\"]([^'\"]+)['\"]\s*\)", locator, flags=re.IGNORECASE)
    if class_contains and all(_looks_unstable_class_token(token) for token in class_contains):
        return True
    return False


def _is_wrapper_xpath(lowered_locator: str) -> bool:
    wrapper_tokens = ("modal", "modals", "container", "content", "wrapper", "header", "layout")
    return any(token in lowered_locator for token in wrapper_tokens)


def _looks_unstable_class_token(token: str) -> bool:
    value = token.strip()
    if not value:
        return True
    if re.fullmatch(r"[a-f0-9]{8,}", value, flags=re.IGNORECASE):
        return True
    if len(value) >= 10 and re.search(r"\d", value):
        return True
    return is_dynamic_class(value)


def _select_candidates_by_priority(candidates: list[LocatorCandidate], limit: int) -> list[LocatorCandidate]:
    if limit <= 0:
        return []

    def is_a_id(candidate: LocatorCandidate) -> bool:
        return (
            candidate.locator_type == "Selenium"
            and candidate.locator.startswith("By.ID(")
            and candidate.uniqueness_count == 1
        )

    def is_b_name(candidate: LocatorCandidate) -> bool:
        return (
            candidate.locator_type == "Selenium"
            and candidate.locator.startswith("By.NAME(")
            and candidate.uniqueness_count == 1
        )

    def is_c_css(candidate: LocatorCandidate) -> bool:
        if candidate.uniqueness_count != 1:
            return False
        locator = candidate.locator.lower()
        if candidate.locator_type == "CSS":
            stable_attrs = ("data-testid", "data-test", "data-qa", "data-cy", "aria-label", "name")
            if any(attr in locator for attr in stable_attrs):
                return True
            if locator.startswith("input[") or locator.startswith("input.") or ("button." in locator and " svg" in locator):
                return True
            if locator.startswith("#") or "#" in locator:
                return True
            if re.match(r"^[a-z0-9_-]+#[a-z0-9_-]+$", locator):
                return True
        if candidate.locator_type == "Selenium" and "By.CSS_SELECTOR" in candidate.locator:
            if any(attr in locator for attr in ("data-testid", "data-test", "data-qa", "data-cy", "aria-label", "name")):
                return True
        return False

    def is_d_text_xpath(candidate: LocatorCandidate) -> bool:
        return (
            candidate.locator_type == "XPath"
            and candidate.rule in {"xpath_text_exact", "xpath_text"}
            and candidate.uniqueness_count == 1
        )

    def is_e_union_xpath(candidate: LocatorCandidate) -> bool:
        return candidate.locator_type == "XPath" and candidate.rule == "xpath_text_clickable_union"

    def is_f_modal(candidate: LocatorCandidate) -> bool:
        return candidate.locator_type == "XPath" and bool(candidate.metadata.get("modal_safe"))

    def is_g_context(candidate: LocatorCandidate) -> bool:
        return candidate.rule in {"xpath_following_sibling", "xpath_ancestor_context", "xpath_label_contains"}

    def is_h_css_generic(candidate: LocatorCandidate) -> bool:
        return (
            candidate.locator_type in {"CSS", "Selenium"}
            and candidate.uniqueness_count == 1
            and candidate.rule in {"meaningful_class", "ancestor", "attr:role", "attr:type", "attr:title", "attr:placeholder"}
        )

    def is_i_playwright(candidate: LocatorCandidate) -> bool:
        return candidate.locator_type == "Playwright" and candidate.uniqueness_count == 1

    def is_j_fallback(candidate: LocatorCandidate) -> bool:
        return candidate.rule in {"xpath_fallback", "nth_fallback"} or bool(candidate.metadata.get("risky"))

    buckets = (
        is_a_id,
        is_b_name,
        is_c_css,
        is_d_text_xpath,
        is_e_union_xpath,
        is_f_modal,
        is_g_context,
        is_h_css_generic,
        is_i_playwright,
        is_j_fallback,
    )

    selected: list[LocatorCandidate] = []
    used: set[tuple[str, str]] = set()

    def add_from_pool(pool: list[LocatorCandidate]) -> None:
        nonlocal selected
        for item in pool:
            if len(selected) >= limit:
                return
            key = (item.locator_type, item.locator)
            if key in used:
                continue
            used.add(key)
            selected.append(item)

    for predicate in buckets:
        pool = [candidate for candidate in candidates if predicate(candidate)]
        pool.sort(key=lambda item: (item.uniqueness_count != 1, -item.score, len(item.locator)))
        add_from_pool(pool)
        if len(selected) >= limit:
            return selected

    remaining = [
        candidate
        for candidate in candidates
        if (candidate.locator_type, candidate.locator) not in used
        and not (candidate.locator_type == "XPath" and candidate.rule in {"xpath_fallback", "nth_fallback"})
    ]
    remaining.sort(key=lambda item: (-item.score, len(item.locator)))
    add_from_pool(remaining)
    return selected


def generate_locator_candidates(
    page: Page,
    element: ElementHandle,
    summary: ElementSummary,
    learning_weights: dict[str, float] | None = None,
    limit: int = 15,
) -> list[LocatorCandidate]:
    promoted = _build_promoted_clickable_ancestor_drafts(page, element)
    base_drafts = _build_candidate_drafts(page, element, summary)
    if promoted:
        drafts = _prune_descendant_css_drafts(page, [*promoted, *base_drafts])
    else:
        drafts = _prune_descendant_css_drafts(page, base_drafts)

    candidates = _validate_drafts(page, drafts)
    scored = score_candidates(candidates, learning_weights)
    prioritized = _select_candidates_by_priority(scored, limit)
    return _ensure_xpath_text_in_results(prioritized, summary, limit)
