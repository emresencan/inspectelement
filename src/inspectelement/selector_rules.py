from __future__ import annotations

import re
from dataclasses import dataclass
from math import log2
from typing import Mapping

ROOT_ID_BLOCKLIST = {"__next", "root", "app", "__nuxt", "gatsby-focus-wrapper"}
ROOT_ID_BLOCKLIST_LOWER = {item.lower() for item in ROOT_ID_BLOCKLIST}

TEST_ATTR_PRIORITY = (
    "data-testid",
    "data-test",
    "data-qa",
    "data-cy",
    "data-e2e",
)

ACCESSIBILITY_ATTRS = ("aria-label", "role", "title")
GENERIC_ATTR_NAMES = {"class", "style", "onclick"}

_DYNAMIC_VALUE_PATTERNS = (
    re.compile(r"^[0-9]{4,}$"),
    re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE),
    re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE),
    re.compile(r".*\d{4,}.*"),
)

_FRAMEWORK_TOKEN_PATTERNS = (
    re.compile(r"(^|[-_:])(mui|css|ng|react|vue|ember|svelte|jdt|j_idt|sc)([-_:]|$)", re.IGNORECASE),
    re.compile(r"^ant-[a-z0-9_-]+$", re.IGNORECASE),
)

_PREFIX_SALVAGE_PATTERNS = (
    re.compile(r"^([A-Za-z][A-Za-z0-9:_-]{2,}?[_:-])\d{3,}$"),
    re.compile(r"^([A-Za-z][A-Za-z0-9:_-]{2,}?[_:-])[a-f0-9]{6,}$", re.IGNORECASE),
    re.compile(r"^([A-Za-z][A-Za-z0-9:_-]{2,}?:)(?:j_idt|jdt_)\d+(?::.*)?$", re.IGNORECASE),
)

_DYNAMIC_CLASS_PATTERNS = (
    re.compile(r"^css-[a-z0-9_-]{4,}$", re.IGNORECASE),
    re.compile(r"^jss\d+$", re.IGNORECASE),
    re.compile(r"^sc-[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE),
    re.compile(r"^[a-z]+__[a-z]+___[a-z0-9]{5,}$", re.IGNORECASE),
)

_FORBIDDEN_LOCATOR_PATTERNS = (
    re.compile(r"^/html(/|$)", re.IGNORECASE),
    re.compile(r"^/body(/|$)", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class AttributeStability:
    attribute: str
    value: str
    stable: bool
    dynamic: bool
    score: float
    entropy: float
    digit_ratio: float
    salvage_prefix: str | None
    salvage_penalty: float
    reasons: tuple[str, ...]


def normalize_space(value: str | None, limit: int = 200) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", " ", str(value)).strip()
    return compact[:limit] if compact else ""


def shannon_entropy(value: str) -> float:
    text = value.strip()
    if not text:
        return 0.0
    total = len(text)
    frequencies: dict[str, int] = {}
    for char in text:
        frequencies[char] = frequencies.get(char, 0) + 1

    entropy = 0.0
    for count in frequencies.values():
        probability = count / total
        entropy -= probability * log2(probability)
    return entropy


def digit_ratio(value: str) -> float:
    text = value.strip()
    if not text:
        return 0.0
    digits = sum(1 for char in text if char.isdigit())
    return digits / len(text)


def has_framework_fingerprint(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _FRAMEWORK_TOKEN_PATTERNS)


def has_hash_like_pattern(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if re.fullmatch(r"[a-f0-9]{8,}", text, flags=re.IGNORECASE):
        return True
    if re.search(r"[a-f0-9]{10,}", text, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", text, flags=re.IGNORECASE):
        return True
    return False


def extract_stable_prefix(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    for pattern in _PREFIX_SALVAGE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        prefix = normalize_space(match.group(1), limit=80).strip()
        if len(prefix) < 4:
            continue
        if digit_ratio(prefix) > 0.35:
            continue
        if has_framework_fingerprint(prefix):
            continue
        return prefix
    return None


def analyze_attribute_stability(attr: str, value: str) -> AttributeStability:
    attribute = normalize_space(attr, limit=60).lower()
    normalized = normalize_space(value, limit=200)
    reasons: list[str] = []
    score = 100.0

    if not normalized:
        return AttributeStability(
            attribute=attribute,
            value=normalized,
            stable=False,
            dynamic=True,
            score=0.0,
            entropy=0.0,
            digit_ratio=0.0,
            salvage_prefix=None,
            salvage_penalty=0.0,
            reasons=("empty",),
        )

    entropy_value = shannon_entropy(normalized)
    digit_value = digit_ratio(normalized)
    prefix = extract_stable_prefix(normalized)
    prefix_penalty = 0.0

    if len(normalized) < 2:
        score -= 60
        reasons.append("too-short")
    if len(normalized) > 120:
        score -= 20
        reasons.append("too-long")
    if digit_value > 0.4:
        score -= 45
        reasons.append("digit-ratio>40%")
    elif digit_value > 0.25:
        score -= 18
        reasons.append("digit-ratio>25%")

    if entropy_value >= 4.2 and len(normalized) >= 8:
        score -= 35
        reasons.append("high-entropy")
    elif entropy_value >= 3.7 and len(normalized) >= 8:
        score -= 16
        reasons.append("medium-entropy")

    framework = has_framework_fingerprint(normalized)
    if framework:
        score -= 40
        reasons.append("framework-token")

    hash_like = has_hash_like_pattern(normalized)
    if hash_like:
        score -= 35
        reasons.append("hash-like")

    if re.search(r"[_:-]\d{3,}$", normalized):
        score -= 24
        reasons.append("numeric-drift-suffix")
    if any(pattern.match(normalized) for pattern in _DYNAMIC_VALUE_PATTERNS):
        score -= 28
        reasons.append("dynamic-pattern")
    if re.fullmatch(r"\d+", normalized):
        score -= 65
        reasons.append("numeric-only")

    if attribute == "id" and is_blocked_root_id(normalized):
        score -= 70
        reasons.append("blocked-root-id")
    if attribute in TEST_ATTR_PRIORITY:
        score += 8
        reasons.append("semantic-test-attr")
    if attribute in {"name", "aria-label", "title", "placeholder"}:
        score += 4

    dynamic = (
        digit_value > 0.4
        or entropy_value >= 4.2
        or framework
        or hash_like
        or "numeric-drift-suffix" in reasons
    )

    if dynamic and prefix:
        prefix_penalty = 14.0
        reasons.append("prefix-salvage")

    if attribute in GENERIC_ATTR_NAMES:
        score -= 30
        reasons.append("generic-attribute")

    bounded = max(0.0, min(100.0, score))
    stable = bounded >= 55 and not dynamic
    return AttributeStability(
        attribute=attribute,
        value=normalized,
        stable=stable,
        dynamic=dynamic,
        score=round(bounded, 2),
        entropy=round(entropy_value, 4),
        digit_ratio=round(digit_value, 4),
        salvage_prefix=prefix,
        salvage_penalty=prefix_penalty,
        reasons=tuple(reasons),
    )


def is_blocked_root_id(id_value: str) -> bool:
    return id_value.strip().lower() in ROOT_ID_BLOCKLIST_LOWER


def is_obvious_root_container_locator(locator: str) -> bool:
    value = locator.strip().lower()
    if not value:
        return False
    if value in {"html", "body"}:
        return True

    for root_id in ROOT_ID_BLOCKLIST_LOWER:
        if value == f"#{root_id}":
            return True
        if f'[id="{root_id}"]' in value or f"[id='{root_id}']" in value:
            return True
        if f"@id='{root_id}'" in value or f'@id="{root_id}"' in value:
            return True

    if re.search(r"(^|[\s>+~])#(?:__next|root|app|__nuxt|gatsby-focus-wrapper)(?=$|[\s>+~\[:.#])", value):
        return True
    return False


def is_dynamic_class_token(token: str) -> bool:
    value = token.strip()
    if not value:
        return True
    if any(pattern.match(value) for pattern in _DYNAMIC_CLASS_PATTERNS):
        return True
    if len(value) > 18 and re.search(r"\d", value):
        return True
    if value.count("-") >= 3 and re.search(r"\d", value):
        return True
    return False


def is_dynamic_attribute_value(value: str) -> bool:
    analysis = analyze_attribute_stability("__generic__", value)
    return analysis.dynamic


def is_dynamic_id_value(id_value: str) -> bool:
    value = id_value.strip()
    if not value:
        return True
    if is_blocked_root_id(value):
        return True
    # JSF / PrimeFaces / generated ids
    if ":" in value and re.search(r"(:\d+:|:j_idt\d+|:jdt_\d+)", value, flags=re.IGNORECASE):
        return True
    return analyze_attribute_stability("id", value).dynamic


def is_stable_attribute_value(attr: str, value: str) -> bool:
    analysis = analyze_attribute_stability(attr, value)
    return analysis.stable


def preferred_test_attributes(attributes: Mapping[str, str]) -> list[tuple[str, str]]:
    picks: list[tuple[str, str]] = []
    for attr in TEST_ATTR_PRIORITY:
        raw = str(attributes.get(attr, "")).strip()
        if not raw:
            continue
        analysis = analyze_attribute_stability(attr, raw)
        if not analysis.stable:
            continue
        picks.append((attr, analysis.value))
    return picks


def is_absolute_xpath(locator: str) -> bool:
    lowered = locator.strip().lower()
    return any(pattern.match(lowered) for pattern in _FORBIDDEN_LOCATOR_PATTERNS)


def is_index_based_xpath(locator: str) -> bool:
    lowered = locator.strip().lower()
    if "nth-of-type" in lowered:
        return True
    return bool(re.search(r"\[\d+\]", lowered))


def is_forbidden_locator(locator: str, locator_type: str) -> bool:
    text = locator.strip()
    if not text:
        return True

    lowered = text.lower()
    if is_obvious_root_container_locator(lowered):
        return True

    if locator_type == "XPath":
        if is_absolute_xpath(text):
            return True
        # hard reject deep index chains
        if lowered.count("[") >= 3 and is_index_based_xpath(text):
            return True

    if locator_type in {"CSS", "Selenium"}:
        # reject generated hash-like classes in selector body
        for token in re.findall(r"\.([A-Za-z0-9_-]+)", text):
            if is_dynamic_class_token(token):
                return True

    return False


def build_strategy_key(strategy_type: str, *, attr: str | None = None, value: str | None = None) -> str:
    base = strategy_type.strip().lower() or "unknown"
    if attr and value:
        return f"{base}:{attr.strip().lower()}:{value.strip()}"
    if attr:
        return f"{base}:{attr.strip().lower()}"
    return base
