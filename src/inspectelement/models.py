from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

LocatorType = Literal["CSS", "XPath", "Playwright", "Selenium"]


@dataclass(slots=True)
class ElementSummary:
    tag: str
    id: str | None
    classes: list[str]
    name: str | None
    role: str | None
    text: str | None
    placeholder: str | None
    aria_label: str | None
    label_text: str | None
    attributes: dict[str, str] = field(default_factory=dict)

    def signature(self) -> str:
        keys = ("id", "name", "data-testid", "data-test", "data-qa", "aria-label", "type")
        pieces = [f"tag={self.tag}"]
        for key in keys:
            value = self.attributes.get(key)
            if value:
                pieces.append(f"{key}={value}")
        return "|".join(pieces)


@dataclass(slots=True)
class ScoreBreakdown:
    uniqueness: float
    stability: float
    length_penalty: float
    dynamic_penalty: float
    learning_adjustment: float
    total: float


@dataclass(slots=True)
class LocatorCandidate:
    locator_type: LocatorType
    locator: str
    rule: str
    uniqueness_count: int
    score: float = 0.0
    breakdown: ScoreBreakdown | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OverrideEntry:
    hostname: str
    element_signature: str
    locator_type: LocatorType
    locator: str
    created_at: str


@dataclass(slots=True)
class PageContext:
    url: str
    hostname: str
    page_title: str
    captured_at: datetime = field(default_factory=datetime.utcnow)
