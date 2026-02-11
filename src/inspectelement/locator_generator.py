from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from playwright.sync_api import ElementHandle, Page

from .models import ElementSummary, LocatorCandidate
from .scoring import score_candidates

_DYNAMIC_CLASS_PATTERNS = [
    re.compile(r"^css-[a-z0-9_-]{4,}$", re.IGNORECASE),
    re.compile(r"^jss\d+$", re.IGNORECASE),
    re.compile(r"^sc-[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE),
    re.compile(r"^[a-z]+__[a-z]+___[a-z0-9]{5,}$", re.IGNORECASE),
    re.compile(r"^_?[a-z]{1,3}[0-9a-f]{6,}$", re.IGNORECASE),
]

STABLE_ATTRS = ("data-testid", "data-test", "data-qa", "aria-label", "name", "id")


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
          const attrs = ['data-testid', 'data-test', 'data-qa', 'aria-label', 'name', 'id'];
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


def _build_candidate_drafts(element: ElementHandle, summary: ElementSummary) -> list[CandidateDraft]:
    drafts: list[CandidateDraft] = []
    seen: set[tuple[str, str]] = set()
    tag = summary.tag

    for attr in STABLE_ATTRS:
        value = summary.attributes.get(attr)
        if not value:
            continue

        css = _stable_attr_css(tag, attr, value)
        _add_unique(
            drafts,
            CandidateDraft(locator_type="CSS", locator=css, rule=f"stable_attr:{attr}"),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=f"//*[@{attr}={_xpath_literal(value)}]",
                rule=f"stable_attr:{attr}",
            ),
            seen,
        )
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.CSS_SELECTOR("{css}")',
                rule=f"stable_attr:{attr}",
                metadata={"selector_kind": "css", "selector_value": css},
            ),
            seen,
        )

        if attr == "data-testid":
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="Playwright",
                    locator=f'page.get_by_test_id("{value}")',
                    rule="stable_attr:data-testid",
                    metadata={"playwright_kind": "test_id", "value": value},
                ),
                seen,
            )
        if attr == "aria-label":
            _add_unique(
                drafts,
                CandidateDraft(
                    locator_type="Playwright",
                    locator=f'page.get_by_label("{value}", exact=True)',
                    rule="stable_attr:aria-label",
                    metadata={"playwright_kind": "label", "value": value},
                ),
                seen,
            )

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

    short_text = _short_text(summary.text)
    if short_text and tag in {"button", "a"}:
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
        _add_unique(
            drafts,
            CandidateDraft(
                locator_type="XPath",
                locator=f"//{tag}[normalize-space()={_xpath_literal(short_text)}]",
                rule="xpath_text",
            ),
            seen,
        )

    if tag in {"input", "textarea", "select"}:
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


def generate_locator_candidates(
    page: Page,
    element: ElementHandle,
    summary: ElementSummary,
    learning_weights: dict[str, float] | None = None,
    limit: int = 5,
) -> list[LocatorCandidate]:
    drafts = _build_candidate_drafts(element, summary)
    candidates = _validate_drafts(page, drafts)
    scored = score_candidates(candidates, learning_weights)
    return scored[:limit]
