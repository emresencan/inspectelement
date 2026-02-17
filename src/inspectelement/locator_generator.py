from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from playwright.sync_api import ElementHandle, Page

from .models import ElementSummary, LocatorCandidate
from .scoring import score_candidates
from .selector_rules import (
    AttributeStability,
    ROOT_ID_BLOCKLIST_LOWER,
    analyze_attribute_stability,
    build_strategy_key,
    is_blocked_root_id,
    is_dynamic_class_token,
    is_dynamic_id_value,
    is_forbidden_locator,
    is_stable_attribute_value,
    normalize_space,
)
from .validation import count_locator_matches, validate_locator_candidate

_DYNAMIC_ID_TOKEN_PATTERNS = (
    re.compile(r"^jdt_\d+$", re.IGNORECASE),
    re.compile(r"^j_idt\d+$", re.IGNORECASE),
    re.compile(r"^\d+$"),
)

PROMOTABLE_STABLE_ATTRS = (
    "data-testid",
    "data-test",
    "data-qa",
    "data-cy",
    "data-e2e",
    "id",
    "name",
    "aria-label",
)


@dataclass(slots=True)
class CandidateDraft:
    locator_type: str
    locator: str
    rule: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DomAnalyzer:
    summary: ElementSummary

    @property
    def tag(self) -> str:
        raw = (self.summary.tag or "").strip().lower()
        return raw or "*"

    def attr(self, key: str) -> str | None:
        raw = self.summary.attributes.get(key)
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None

    def normalized_text_sources(self) -> list[tuple[str, str]]:
        raw_sources: list[tuple[str, str | None]] = [
            ("text", self.summary.text),
            ("aria_label", self.summary.aria_label or self.attr("aria-label")),
            ("title", self.summary.title or self.attr("title")),
            ("placeholder", self.summary.placeholder or self.attr("placeholder")),
            ("value", self.summary.value_text or self.attr("value")),
            ("label", self.summary.label_text),
            ("aria_labelledby", self.summary.aria_labelledby_text),
        ]

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, value in raw_sources:
            text = normalize_space(value, limit=120)
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append((source, text))
        return deduped


class CandidateFactory:
    def __init__(self, page: Page, element: ElementHandle, analyzer: DomAnalyzer) -> None:
        self.page = page
        self.element = element
        self.analyzer = analyzer
        self._drafts: list[CandidateDraft] = []
        self._seen: set[tuple[str, str]] = set()

    def generate(self) -> list[CandidateDraft]:
        self._add_promoted_clickable_ancestor()
        self._add_id_strategy()
        self._add_data_attr_strategies()
        self._add_name_strategy()
        self._add_accessibility_strategies()
        self._add_placeholder_and_label_strategies()
        self._add_class_and_ancestor_fallbacks()
        self._add_last_resort_dynamic_id_partial()
        self._add_text_xpath_strategy()

        if not any(_strategy_type_from_draft(item) != "text_xpath" for item in self._drafts):
            self._add_nth_fallback()

        return list(self._drafts)

    def _add_promoted_clickable_ancestor(self) -> None:
        promoted = _build_promoted_clickable_ancestor_drafts(self.page, self.element)
        if not promoted:
            return
        for draft in promoted:
            _add_unique(self._drafts, draft, self._seen)

    def _add_id_strategy(self) -> None:
        id_value = self.analyzer.attr("id") or (self.analyzer.summary.id or "").strip() or None
        if not id_value:
            return
        analysis = analyze_attribute_stability("id", id_value)
        if analysis.stable:
            drafts = _build_stable_attr_drafts(self.analyzer.tag, "id", id_value, stability=analysis)
        else:
            drafts = self._add_prefix_salvage_strategy("id", id_value, analysis, rule="stable_attr:id_prefix")
        for draft in drafts:
            _add_unique(self._drafts, draft, self._seen)

    def _add_data_attr_strategies(self) -> None:
        for attr in ("data-testid", "data-test", "data-qa", "data-cy", "data-e2e"):
            raw = self.analyzer.attr(attr)
            if not raw:
                continue
            analysis = analyze_attribute_stability(attr, raw)
            if analysis.stable:
                drafts = _build_stable_attr_drafts(self.analyzer.tag, attr, raw, stability=analysis)
            else:
                drafts = self._add_prefix_salvage_strategy(attr, raw, analysis, rule=f"stable_attr:{attr}_prefix")
            for draft in drafts:
                _add_unique(self._drafts, draft, self._seen)

    def _add_name_strategy(self) -> None:
        value = self.analyzer.attr("name") or (self.analyzer.summary.name or "").strip() or None
        if not value:
            return
        analysis = analyze_attribute_stability("name", value)
        if analysis.stable:
            drafts = _build_stable_attr_drafts(self.analyzer.tag, "name", value, stability=analysis)
        else:
            drafts = self._add_prefix_salvage_strategy("name", value, analysis, rule="stable_attr:name_prefix")
        for draft in drafts:
            _add_unique(self._drafts, draft, self._seen)

    def _add_accessibility_strategies(self) -> None:
        tag = self.analyzer.tag
        aria_label = self.analyzer.attr("aria-label") or self.analyzer.summary.aria_label
        role = self.analyzer.attr("role") or self.analyzer.summary.role
        title = self.analyzer.attr("title") or self.analyzer.summary.title

        if aria_label and is_stable_attribute_value("aria-label", aria_label):
            if role and is_stable_attribute_value("role", role):
                css = f'[role="{_escape_css_string(role)}"][aria-label="{_escape_css_string(aria_label)}"]'
                _add_unique(
                    self._drafts,
                    CandidateDraft(
                        locator_type="CSS",
                        locator=css,
                        rule="stable_attr:aria-label",
                        metadata={
                            "strategy_type": "accessibility",
                            "strategy_key": build_strategy_key("accessibility", attr="role+aria-label", value=f"{role}|{aria_label}"),
                            "source_attr": "aria-label",
                            "source_value": aria_label,
                            "generic_penalty": 0.0,
                        },
                    ),
                    self._seen,
                )
                if self.analyzer.normalized_text_sources():
                    role_text = self.analyzer.normalized_text_sources()[0][1]
                    xpath = (
                        f"//*[@role={_xpath_literal(role)}]"
                        f"//*[self::{tag}][contains(normalize-space(), {_xpath_literal(role_text)})]"
                    )
                    _add_unique(
                        self._drafts,
                        CandidateDraft(
                            locator_type="XPath",
                            locator=xpath,
                            rule="stable_attr:role",
                            metadata={
                                "strategy_type": "accessibility",
                                "strategy_key": build_strategy_key("accessibility", attr="role+text", value=f"{role}|{role_text}"),
                                "source_attr": "role",
                                "source_value": role,
                                "generic_penalty": 4.0,
                            },
                        ),
                        self._seen,
                    )
            else:
                css = f'{tag}[aria-label="{_escape_css_string(aria_label)}"]'
                _add_unique(
                    self._drafts,
                    CandidateDraft(
                        locator_type="CSS",
                        locator=css,
                        rule="stable_attr:aria-label",
                        metadata={
                            "strategy_type": "accessibility",
                            "strategy_key": build_strategy_key("accessibility", attr="aria-label", value=aria_label),
                            "source_attr": "aria-label",
                            "source_value": aria_label,
                            "generic_penalty": 0.0,
                        },
                    ),
                    self._seen,
                )

        if title and is_stable_attribute_value("title", title):
            css = f'{tag}[title="{_escape_css_string(title)}"]'
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="CSS",
                    locator=css,
                    rule="stable_attr:title",
                    metadata={
                        "strategy_type": "accessibility",
                        "strategy_key": build_strategy_key("accessibility", attr="title", value=title),
                        "source_attr": "title",
                        "source_value": title,
                        "generic_penalty": 0.0,
                    },
                ),
                self._seen,
            )

    def _add_placeholder_and_label_strategies(self) -> None:
        tag = self.analyzer.tag
        placeholder = self.analyzer.attr("placeholder") or self.analyzer.summary.placeholder
        if placeholder and tag in {"input", "textarea", "select"}:
            css = f'{tag}[placeholder="{_escape_css_string(placeholder)}"]'
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="CSS",
                    locator=css,
                    rule="placeholder",
                    metadata={
                        "strategy_type": "placeholder",
                        "strategy_key": build_strategy_key("placeholder", attr="placeholder", value=placeholder),
                        "source_attr": "placeholder",
                        "source_value": placeholder,
                    },
                ),
                self._seen,
            )

        aria_labelledby = self.analyzer.attr("aria-labelledby")
        if aria_labelledby:
            css = f'{tag}[aria-labelledby="{_escape_css_string(aria_labelledby)}"]'
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="CSS",
                    locator=css,
                    rule="label_assoc",
                    metadata={
                        "strategy_type": "label_relation",
                        "strategy_key": build_strategy_key("label_relation", attr="aria-labelledby", value=aria_labelledby),
                        "source_attr": "aria-labelledby",
                        "source_value": aria_labelledby,
                    },
                ),
                self._seen,
            )

        label_text = normalize_space(self.analyzer.summary.label_text, limit=100)
        if label_text and tag in {"input", "textarea", "select"}:
            xpath = (
                "//label[normalize-space()="
                f"{_xpath_literal(label_text)}"
                "]/following::*[self::input or self::textarea or self::select][1]"
            )
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="XPath",
                    locator=xpath,
                    rule="label_assoc",
                    metadata={
                        "strategy_type": "label_relation",
                        "strategy_key": build_strategy_key("label_relation", attr="label", value=label_text),
                        "source_attr": "label",
                        "source_value": label_text,
                    },
                ),
                self._seen,
            )

    def _add_class_and_ancestor_fallbacks(self) -> None:
        tag = self.analyzer.tag
        meaningful_classes = [
            cls for cls in self.analyzer.summary.classes if cls and not is_dynamic_class(cls)
        ]
        if meaningful_classes:
            css = f"{tag}.{_escape_css_identifier(meaningful_classes[0])}"
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="CSS",
                    locator=css,
                    rule="meaningful_class",
                    metadata={
                        "strategy_type": "class",
                        "strategy_key": build_strategy_key("class", attr="class", value=meaningful_classes[0]),
                        "dynamic_class_count": max(0, len(self.analyzer.summary.classes) - len(meaningful_classes)),
                        "generic_penalty": 8.0,
                    },
                ),
                self._seen,
            )

        ancestor = _nearest_stable_ancestor(self.element)
        if ancestor:
            contextual_xpath = self._build_contextual_xpath(
                tag=tag,
                ancestor_attr=ancestor["attr"],
                ancestor_value=ancestor["value"],
                class_hint=meaningful_classes[0] if meaningful_classes else None,
            )
            _add_unique(
                self._drafts,
                CandidateDraft(
                    locator_type="XPath",
                    locator=contextual_xpath,
                    rule="ancestor",
                    metadata={
                        "strategy_type": "ancestor",
                        "strategy_key": build_strategy_key("ancestor", attr=ancestor["attr"], value=ancestor["value"]),
                        "source_attr": ancestor["attr"],
                        "source_value": ancestor["value"],
                        "generic_penalty": 6.0,
                    },
                ),
                self._seen,
            )

    def _add_last_resort_dynamic_id_partial(self) -> None:
        id_value = self.analyzer.attr("id") or (self.analyzer.summary.id or "").strip() or None
        if not id_value or not is_dynamic_id(id_value):
            return

        parts = build_dynamic_id_partial_locators(id_value)
        if not parts:
            return

        css, _xpath = parts
        _add_unique(
            self._drafts,
            CandidateDraft(
                locator_type="CSS",
                locator=css,
                rule="stable_attr:id_partial",
                metadata={
                    "strategy_type": "fallback",
                    "strategy_key": build_strategy_key("fallback", attr="id_partial", value=id_value),
                    "source_attr": "id",
                    "source_value": id_value,
                    "generic_penalty": 10.0,
                },
            ),
            self._seen,
        )

    def _add_nth_fallback(self) -> None:
        fallback = _nth_fallback_path(self.element)
        if not fallback:
            return
        _add_unique(
            self._drafts,
            CandidateDraft(
                locator_type="CSS",
                locator=fallback,
                rule="nth_fallback",
                metadata={
                    "strategy_type": "fallback",
                    "strategy_key": build_strategy_key("fallback", attr="nth", value=fallback),
                    "uses_nth": True,
                    "generic_penalty": 18.0,
                },
            ),
            self._seen,
        )

    def _add_text_xpath_strategy(self) -> None:
        text_draft = _build_text_xpath_draft(self.page, self.analyzer)
        _add_unique(self._drafts, text_draft, self._seen)

    def _add_prefix_salvage_strategy(
        self,
        attr: str,
        value: str,
        analysis: AttributeStability,
        *,
        rule: str,
    ) -> list[CandidateDraft]:
        if not analysis.salvage_prefix:
            return []
        prefix = analysis.salvage_prefix
        css = f'[{attr}^="{_escape_css_string(prefix)}"]'
        strategy_group = "id" if attr == "id" else ("name" if attr == "name" else "data_attr")
        return [
            CandidateDraft(
                locator_type="CSS",
                locator=css,
                rule=rule,
                metadata={
                    "strategy_type": strategy_group,
                    "strategy_key": build_strategy_key(strategy_group, attr=f"{attr}_prefix", value=prefix),
                    "source_attr": attr,
                    "source_value": value,
                    "prefix_salvaged": True,
                    "allow_salvage": True,
                    "salvage_prefix": prefix,
                    "salvage_penalty": analysis.salvage_penalty,
                    "stability_score": analysis.score,
                    "stability_entropy": analysis.entropy,
                    "stability_digit_ratio": analysis.digit_ratio,
                    "dynamic_detected": analysis.dynamic,
                    "generic_penalty": 10.0,
                },
            )
        ]

    def _build_contextual_xpath(
        self,
        *,
        tag: str,
        ancestor_attr: str,
        ancestor_value: str,
        class_hint: str | None = None,
    ) -> str:
        anchor = f"//*[@{ancestor_attr}={_xpath_literal(ancestor_value)}]"
        if class_hint:
            return (
                f"{anchor}//{tag}[contains(concat(' ', normalize-space(@class), ' '), "
                f"{_xpath_literal(f' {class_hint} ')})]"
            )
        text_sources = self.analyzer.normalized_text_sources()
        if text_sources:
            text = text_sources[0][1]
            return f"{anchor}//{tag}[contains(normalize-space(), {_xpath_literal(text)})]"
        return f"{anchor}//{tag}"


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
    return is_dynamic_class_token(class_name)


def is_dynamic_id(id_value: str) -> bool:
    value = id_value.strip()
    if not value:
        return True
    if is_dynamic_id_value(value):
        return True
    if ":" not in value:
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
    compact = normalize_space(value, limit=limit)
    return compact or None


def _add_unique(drafts: list[CandidateDraft], draft: CandidateDraft, seen: set[tuple[str, str]]) -> None:
    key = (draft.locator_type, draft.locator)
    if key in seen:
        return
    seen.add(key)
    drafts.append(draft)


def _count_matches(page: Page, draft: CandidateDraft) -> int:
    return count_locator_matches(page, draft.locator_type, draft.locator, draft.metadata)


def _extract_dom_snapshot(page: Page) -> dict[str, Any]:
    try:
        payload = page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll('*'));
              const tagHistogram = {};
              const attrHistogram = {};
              let textNodeCount = 0;

              for (const node of nodes) {
                const tag = (node.tagName || '').toLowerCase();
                if (tag) {
                  tagHistogram[tag] = (tagHistogram[tag] || 0) + 1;
                }
                for (const attr of Array.from(node.attributes || [])) {
                  attrHistogram[attr.name] = (attrHistogram[attr.name] || 0) + 1;
                }
                const text = (node.innerText || node.textContent || '').trim();
                if (text) {
                  textNodeCount += 1;
                }
              }

              return {
                node_count: nodes.length,
                text_node_count: textNodeCount,
                title: document.title || '',
                url: location.href || '',
                tag_histogram: tagHistogram,
                attr_histogram: attrHistogram,
              };
            }
            """
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {
        "node_count": 0,
        "text_node_count": 0,
        "title": "",
        "url": "",
        "tag_histogram": {},
        "attr_histogram": {},
    }


def _nearest_stable_ancestor(element: ElementHandle) -> dict[str, str] | None:
    return element.evaluate(
        """
        (el) => {
          const attrs = ['data-testid', 'data-test', 'data-qa', 'data-cy', 'data-e2e', 'aria-label', 'name'];
          let current = el.parentElement;
          let hops = 0;
          while (current && hops < 2) {
            const tag = (current.tagName || '').toLowerCase();
            if (!tag || tag === 'html' || tag === 'body') {
              current = current.parentElement;
              hops += 1;
              continue;
            }
            for (const attr of attrs) {
              const value = current.getAttribute(attr);
              if (value) {
                return {
                  tag,
                  attr,
                  value,
                };
              }
            }
            current = current.parentElement;
            hops += 1;
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
          while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
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

    if attr in {"data-testid", "data-test", "data-qa", "data-cy", "data-e2e"}:
        return f'[{attr}="{_escape_css_string(value)}"]'

    return f'{tag}[{attr}="{_escape_css_string(value)}"]'


def _is_blocked_id(tag: str, value: str) -> bool:
    normalized_tag = tag.strip().lower()
    if normalized_tag in {"html", "body"}:
        return True
    if is_blocked_root_id(value):
        return True
    return False


def _build_stable_attr_drafts(
    tag: str,
    attr: str,
    value: str,
    *,
    stability: AttributeStability | None = None,
) -> list[CandidateDraft]:
    attr_lower = attr.strip().lower()
    cleaned = value.strip()
    if not cleaned:
        return []
    analysis = stability or analyze_attribute_stability(attr_lower, cleaned)

    if attr_lower == "id":
        if _is_blocked_id(tag, cleaned) or not analysis.stable:
            return []
        return [
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.id("{cleaned}")',
                rule="stable_attr:id",
                metadata={
                    "strategy_type": "id",
                    "strategy_key": build_strategy_key("id", attr="id", value=cleaned),
                    "selector_kind": "id",
                    "selector_value": cleaned,
                    "source_attr": "id",
                    "source_value": cleaned,
                    "stability_score": analysis.score,
                    "stability_entropy": analysis.entropy,
                    "stability_digit_ratio": analysis.digit_ratio,
                    "dynamic_detected": analysis.dynamic,
                    "generic_penalty": 0.0,
                },
            )
        ]

    if attr_lower == "name":
        if not analysis.stable:
            return []
        return [
            CandidateDraft(
                locator_type="Selenium",
                locator=f'By.name("{cleaned}")',
                rule="stable_attr:name",
                metadata={
                    "strategy_type": "name",
                    "strategy_key": build_strategy_key("name", attr="name", value=cleaned),
                    "selector_kind": "name",
                    "selector_value": cleaned,
                    "source_attr": "name",
                    "source_value": cleaned,
                    "stability_score": analysis.score,
                    "stability_entropy": analysis.entropy,
                    "stability_digit_ratio": analysis.digit_ratio,
                    "dynamic_detected": analysis.dynamic,
                    "generic_penalty": 0.0,
                },
            )
        ]

    strategy_type = "data_attr" if attr_lower.startswith("data-") else "accessibility"
    if attr_lower == "placeholder":
        strategy_type = "placeholder"

    css = _stable_attr_css(tag, attr_lower, cleaned)
    return [
        CandidateDraft(
            locator_type="CSS",
            locator=css,
            rule=f"stable_attr:{attr_lower}",
            metadata={
                "strategy_type": strategy_type,
                "strategy_key": build_strategy_key(strategy_type, attr=attr_lower, value=cleaned),
                "source_attr": attr_lower,
                "source_value": cleaned,
                "stability_score": analysis.score,
                "stability_entropy": analysis.entropy,
                "stability_digit_ratio": analysis.digit_ratio,
                "dynamic_detected": analysis.dynamic,
                "generic_penalty": 0.0,
            },
        )
    ]


def _find_clickable_ancestor_snapshot(element: ElementHandle) -> dict[str, Any] | None:
    return element.evaluate(
        """
        (el) => {
          const attrs = ['data-testid', 'data-test', 'data-qa', 'data-cy', 'data-e2e', 'id', 'name', 'aria-label'];
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
        if attr == "id" and (_is_blocked_id(tag, value) or is_dynamic_id(value)):
            continue
        analysis = analyze_attribute_stability(attr, value)
        if not analysis.stable:
            continue

        css = _stable_attr_css(tag, attr, value)
        try:
            if len(page.query_selector_all(css)) != 1:
                continue
        except Exception:
            continue
        return _build_stable_attr_drafts(tag, attr, value, stability=analysis)

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

    cap = max(1, min(5, int(limit)))
    if not scored:
        return []

    top = scored[:cap]

    text_rows = [candidate for candidate in top if _is_text_xpath_candidate(candidate)]
    if not text_rows:
        best_text = next((candidate for candidate in scored if _is_text_xpath_candidate(candidate)), None)
        if best_text:
            if len(top) < cap:
                top.append(best_text)
            else:
                top[-1] = best_text
            text_rows = [best_text]

    if len(text_rows) > 1:
        keeper = text_rows[0]
        compact: list[LocatorCandidate] = [item for item in top if not _is_text_xpath_candidate(item)]
        compact.append(keeper)
        seen: set[tuple[str, str]] = {(item.locator_type, item.locator) for item in compact}
        for candidate in scored:
            key = (candidate.locator_type, candidate.locator)
            if key in seen:
                continue
            if len(compact) >= cap:
                break
            if _is_text_xpath_candidate(candidate):
                continue
            compact.append(candidate)
            seen.add(key)
        top = compact[:cap]

    # If no text source exists at all this list may not contain one; generator should always provide one.
    return top[:cap]


def _is_text_xpath_candidate(candidate: LocatorCandidate) -> bool:
    if candidate.locator_type != "XPath":
        return False
    strategy = str(candidate.metadata.get("strategy_type") or candidate.strategy_type or "").strip().lower()
    if strategy == "text_xpath":
        return True
    return candidate.rule in {"xpath_text", "text_xpath"}


def _dedupe_semantic_candidates(candidates: list[LocatorCandidate]) -> list[LocatorCandidate]:
    deduped: list[LocatorCandidate] = []
    seen_strategy: set[str] = set()

    has_id = any(
        (str(candidate.metadata.get("strategy_type") or candidate.strategy_type).strip().lower() == "id")
        for candidate in candidates
    )

    for candidate in candidates:
        strategy = str(candidate.metadata.get("strategy_type") or candidate.strategy_type or "fallback").strip().lower()
        strategy_key = str(candidate.metadata.get("strategy_key") or "").strip().lower()

        if has_id and strategy != "id":
            source_attr = str(candidate.metadata.get("source_attr") or "").strip().lower()
            locator_lower = candidate.locator.lower()
            if source_attr == "id" or "@id" in locator_lower or "[id=" in locator_lower or "#" in locator_lower:
                continue

        logical_key = strategy_key or f"{strategy}:{candidate.locator_type}:{candidate.locator}"
        if logical_key in seen_strategy:
            continue

        seen_strategy.add(logical_key)
        deduped.append(candidate)

    return deduped


def _build_text_xpath_draft(page: Page, analyzer: DomAnalyzer) -> CandidateDraft:
    tag = analyzer.tag
    text_sources = analyzer.normalized_text_sources()

    if not text_sources:
        fallback_xpath = f"//{tag}[normalize-space()!='']"
        return CandidateDraft(
            locator_type="XPath",
            locator=fallback_xpath,
            rule="xpath_text",
            metadata={
                "strategy_type": "text_xpath",
                "strategy_key": build_strategy_key("text_xpath"),
                "source_attr": "text",
                "source_value": "",
                "generic_penalty": 14.0,
            },
        )

    source, value = text_sources[0]

    if source == "text":
        xpath = _best_visible_text_xpath(page, tag, value)
    elif source in {"aria_label", "title", "placeholder", "value"}:
        xpath = _best_attribute_text_xpath(page, tag, source, value)
    else:
        xpath = _best_contains_text_xpath(page, tag, value)

    return CandidateDraft(
        locator_type="XPath",
        locator=xpath,
        rule="xpath_text",
        metadata={
            "strategy_type": "text_xpath",
            "strategy_key": build_strategy_key("text_xpath", attr=source, value=value),
            "source_attr": source,
            "source_value": value,
            "generic_penalty": 2.0 if "contains(" in xpath else 0.0,
        },
    )


def _best_visible_text_xpath(page: Page, tag: str, value: str) -> str:
    literal = _xpath_literal(value)
    candidates = [f"//{tag}[normalize-space()={literal}]"]

    if tag in {"button", "a", "span"}:
        candidates.append(
            f"//*[self::button or self::a or self::span][normalize-space()={literal}]"
        )

    for candidate in candidates:
        if count_locator_matches(page, "XPath", candidate, None) == 1:
            return candidate

    contains_candidate = _best_contains_text_xpath(page, tag, value)
    if count_locator_matches(page, "XPath", contains_candidate, None) == 1:
        return contains_candidate

    return candidates[0]


def _best_attribute_text_xpath(page: Page, tag: str, source: str, value: str) -> str:
    attr_map = {
        "aria_label": "aria-label",
        "title": "title",
        "placeholder": "placeholder",
        "value": "value",
    }
    attr = attr_map.get(source, "aria-label")
    literal = _xpath_literal(value)

    candidates = [
        f"//{tag}[@{attr}={literal}]",
        f"//*[@{attr}={literal}]",
        f"//*[contains(normalize-space(@{attr}), {literal})]",
    ]

    for candidate in candidates:
        if count_locator_matches(page, "XPath", candidate, None) == 1:
            return candidate

    return candidates[0]


def _best_contains_text_xpath(page: Page, tag: str, value: str) -> str:
    snippets = _text_snippets(value)
    for snippet in snippets:
        literal = _xpath_literal(snippet)
        candidate = f"//{tag}[contains(normalize-space(), {literal})]"
        if count_locator_matches(page, "XPath", candidate, None) == 1:
            return candidate

    fallback = snippets[0] if snippets else value
    return f"//{tag}[contains(normalize-space(), {_xpath_literal(fallback)})]"


def _text_snippets(value: str) -> list[str]:
    text = normalize_space(value, limit=120)
    if not text:
        return []

    words = [word for word in text.split(" ") if word]
    if not words:
        return []

    snippets: list[str] = []
    # shorter unique text is generally more resilient than full long strings.
    if len(words) >= 2:
        snippets.append(" ".join(words[:2]))
        snippets.append(" ".join(words[-2:]))
    snippets.append(" ".join(words[: min(4, len(words))]))
    snippets.append(text[: min(40, len(text))])

    deduped: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        cleaned = normalize_space(snippet, limit=60)
        if len(cleaned) < 2:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped or [text]


def _strategy_type_from_draft(draft: CandidateDraft) -> str:
    return str(draft.metadata.get("strategy_type") or "fallback").strip().lower()


def _validate_drafts(page: Page, drafts: Iterable[CandidateDraft], snapshot: Mapping[str, Any]) -> list[LocatorCandidate]:
    candidates: list[LocatorCandidate] = []
    node_count = int(snapshot.get("node_count", 0) or 0)
    text_node_count = int(snapshot.get("text_node_count", 0) or 0)
    for draft in drafts:
        check = validate_locator_candidate(page, draft.locator_type, draft.locator, draft.metadata)
        metadata = dict(draft.metadata)
        metadata["stable"] = bool(check.stable)
        metadata["validation_message"] = check.message
        metadata["snapshot_node_count"] = node_count
        metadata["snapshot_text_node_count"] = text_node_count
        metadata["output_type"] = _output_type(metadata, draft.locator_type)

        candidates.append(
            LocatorCandidate(
                locator_type=draft.locator_type,  # type: ignore[arg-type]
                locator=draft.locator,
                rule=draft.rule,
                uniqueness_count=max(0, int(check.match_count)),
                metadata=metadata,
            )
        )
    return candidates


def _passes_quality_gate(candidate: LocatorCandidate) -> bool:
    strategy = str(candidate.metadata.get("strategy_type") or candidate.strategy_type or "").strip().lower()
    if strategy == "text_xpath":
        return True

    if is_forbidden_locator(candidate.locator, candidate.locator_type):
        return False

    if candidate.metadata.get("stable") is False:
        return False
    node_count = int(candidate.metadata.get("snapshot_node_count", 0) or 0)
    if node_count > 0 and candidate.uniqueness_count > max(1, int(node_count * 0.01)):
        return False

    return candidate.uniqueness_count == 1


def _build_candidate_drafts(element: ElementHandle, summary: ElementSummary, page: Page) -> list[CandidateDraft]:
    factory = CandidateFactory(page=page, element=element, analyzer=DomAnalyzer(summary=summary))
    drafts = factory.generate()
    return _prune_descendant_css_drafts(page, drafts)


def generate_locator_candidates(
    page: Page,
    element: ElementHandle,
    summary: ElementSummary,
    learning_weights: dict[str, float] | None = None,
    limit: int = 5,
) -> list[LocatorCandidate]:
    cap = max(1, min(5, int(limit)))
    snapshot = _extract_dom_snapshot(page)

    drafts = _build_candidate_drafts(element, summary, page)
    validated = _validate_drafts(page, drafts, snapshot)

    filtered = [candidate for candidate in validated if _passes_quality_gate(candidate)]

    if not any(_is_text_xpath_candidate(candidate) for candidate in filtered):
        filtered.extend(candidate for candidate in validated if _is_text_xpath_candidate(candidate))

    if not filtered:
        filtered = validated

    scored = score_candidates(filtered, learning_weights)
    deduped = _dedupe_semantic_candidates(scored)
    diversified = _enforce_strategy_diversity(deduped, cap)
    final_rows = _ensure_xpath_text_in_results(diversified, summary, cap)
    return final_rows[:cap]


def _enforce_strategy_diversity(candidates: list[LocatorCandidate], limit: int) -> list[LocatorCandidate]:
    cap = max(1, min(5, int(limit)))
    if not candidates:
        return []

    text_candidate = next((item for item in candidates if _is_text_xpath_candidate(item)), None)

    selected: list[LocatorCandidate] = []
    used_philosophy: set[str] = set()
    for candidate in candidates:
        if _is_text_xpath_candidate(candidate):
            continue
        strategy = str(candidate.metadata.get("strategy_type") or candidate.strategy_type or "fallback").strip().lower()
        philosophy = strategy or "fallback"
        if philosophy in used_philosophy:
            continue
        selected.append(candidate)
        used_philosophy.add(philosophy)
        if len(selected) >= cap - 1:
            break

    if text_candidate:
        selected.append(text_candidate)

    selected.sort(
        key=lambda item: (
            -float(item.score),
            -float(item.breakdown.stability if item.breakdown else 0.0),
            -float(item.metadata.get("simplicity", 0.0)),
            len(item.locator),
        )
    )

    # Keep exactly one text XPath.
    compact: list[LocatorCandidate] = []
    seen_text = False
    for candidate in selected:
        if _is_text_xpath_candidate(candidate):
            if seen_text:
                continue
            seen_text = True
        compact.append(candidate)

    if not seen_text and text_candidate:
        if len(compact) >= cap:
            compact[-1] = text_candidate
        else:
            compact.append(text_candidate)
    return compact[:cap]


def _output_type(metadata: Mapping[str, Any], locator_type: str) -> str:
    strategy = str(metadata.get("strategy_type") or "").strip().lower()
    if strategy == "id":
        return "id"
    if strategy == "text_xpath":
        return "text_xpath"
    normalized = locator_type.strip().lower()
    if normalized == "css":
        return "css"
    if normalized == "xpath":
        return "xpath"
    if normalized == "playwright":
        return "playwright"
    if normalized == "selenium":
        return "selenium"
    return normalized or "css"
