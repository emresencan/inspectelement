from __future__ import annotations

import re
from typing import Any

from .locator_generator import normalize_classes
from .models import ElementSummary, LocatorCandidate
from .scoring import score_candidates
from .table_root_detection import detect_table_root_candidates

EMBEDDED_INSPECTOR_BOOTSTRAP_SCRIPT = r"""
(() => {
  function escapeCssString(value) {
    return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function xpathLiteral(value) {
    const text = String(value || '');
    if (!text.includes("'")) {
      return `'${text}'`;
    }
    if (!text.includes('"')) {
      return `"${text}"`;
    }
    const pieces = text.split("'");
    return "concat(" + pieces.map((piece) => `'${piece}'`).join(", \"'\", ") + ")";
  }

  function normalizeText(value, limit = 200) {
    const normalized = String(value || '').replace(/\s+/g, ' ').trim();
    if (!normalized) {
      return '';
    }
    return normalized.slice(0, limit);
  }

  function countCss(selector) {
    try {
      return document.querySelectorAll(selector).length;
    } catch (_) {
      return 0;
    }
  }

  function countXpath(xpath) {
    try {
      const value = document.evaluate(
        `count(${xpath})`,
        document,
        null,
        XPathResult.NUMBER_TYPE,
        null
      ).numberValue;
      return Math.max(0, Math.round(value));
    } catch (_) {
      return 0;
    }
  }

  function buildPath(el) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      const tag = (current.tagName || '').toLowerCase();
      if (!tag) break;
      if (current.id) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      let nth = 1;
      let sibling = current;
      while ((sibling = sibling.previousElementSibling)) {
        if ((sibling.tagName || '').toLowerCase() === tag) {
          nth += 1;
        }
      }
      parts.unshift(`${tag}:nth-of-type(${nth})`);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function addCandidate(list, seen, locatorType, locator, rule, uniquenessCount, metadata) {
    const normalizedType = String(locatorType || '').trim();
    const normalizedLocator = String(locator || '').trim();
    if (!normalizedType || !normalizedLocator) {
      return;
    }
    const key = `${normalizedType}||${normalizedLocator}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    list.push({
      locator_type: normalizedType,
      locator: normalizedLocator,
      rule: String(rule || 'manual'),
      uniqueness_count: Number.isFinite(uniquenessCount) ? Number(uniquenessCount) : 0,
      metadata: metadata || {},
    });
  }

  function buildCandidates(el, summary) {
    const list = [];
    const seen = new Set();
    const tag = summary.tag || 'div';

    if (summary.id) {
      const idValue = String(summary.id);
      const cssById = `#${CSS.escape(idValue)}`;
      const xpathById = `//*[@id=${xpathLiteral(idValue)}]`;
      addCandidate(list, seen, 'CSS', cssById, 'stable_attr:id', countCss(cssById), {});
      addCandidate(list, seen, 'XPath', xpathById, 'stable_attr:id', countXpath(xpathById), {});
      addCandidate(
        list,
        seen,
        'Selenium',
        `By.ID(\"${idValue.replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\")`,
        'stable_attr:id',
        countCss(cssById),
        { selector_kind: 'id', selector_value: idValue }
      );
    }

    const stableAttrs = ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder'];
    for (const attr of stableAttrs) {
      const value = summary.attributes[attr];
      if (!value) {
        continue;
      }
      const css = `${tag}[${attr}="${escapeCssString(value)}"]`;
      const xpath = `//*[@${attr}=${xpathLiteral(value)}]`;
      const cssCount = countCss(css);
      addCandidate(list, seen, 'CSS', css, `stable_attr:${attr}`, cssCount, {});
      addCandidate(list, seen, 'XPath', xpath, `stable_attr:${attr}`, countXpath(xpath), {});
      addCandidate(
        list,
        seen,
        'Selenium',
        `By.CSS_SELECTOR(\"${css.replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\")`,
        `stable_attr:${attr}`,
        cssCount,
        { selector_kind: 'css', selector_value: css }
      );
      if (attr === 'name') {
        addCandidate(
          list,
          seen,
          'Selenium',
          `By.NAME(\"${String(value).replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\")`,
          'stable_attr:name',
          countXpath(`//*[@name=${xpathLiteral(value)}]`),
          { selector_kind: 'name', selector_value: String(value) }
        );
      }
      if (attr === 'data-testid') {
        addCandidate(
          list,
          seen,
          'Playwright',
          `page.get_by_test_id(\"${String(value).replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\")`,
          'stable_attr:data-testid',
          cssCount,
          { playwright_kind: 'test_id', value: String(value) }
        );
      }
      if (attr === 'aria-label') {
        addCandidate(
          list,
          seen,
          'Playwright',
          `page.get_by_label(\"${String(value).replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\", exact=True)`,
          'stable_attr:aria-label',
          countXpath(`//*[@aria-label=${xpathLiteral(value)}]`),
          { playwright_kind: 'label', value: String(value) }
        );
      }
      if (attr === 'placeholder') {
        addCandidate(
          list,
          seen,
          'Playwright',
          `page.get_by_placeholder(\"${String(value).replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\", exact=True)`,
          'placeholder',
          countXpath(`//*[@placeholder=${xpathLiteral(value)}]`),
          { playwright_kind: 'placeholder', value: String(value) }
        );
      }
    }

    if (summary.classes.length) {
      const token = summary.classes.find((item) => /^[A-Za-z_-][A-Za-z0-9_-]*$/.test(item));
      if (token) {
        const css = `${tag}.${token}`;
        const xpath = `//${tag}[contains(concat(' ', normalize-space(@class), ' '), ' ${token} ')]`;
        addCandidate(list, seen, 'CSS', css, 'meaningful_class', countCss(css), {});
        addCandidate(list, seen, 'XPath', xpath, 'meaningful_class', countXpath(xpath), {});
      }
    }

    const shortText = String(summary.text || '').replace(/\s+/g, ' ').trim();
    if (shortText) {
      const exactXpath = `//${tag}[normalize-space(.)=${xpathLiteral(shortText.slice(0, 80))}]`;
      const containsText = shortText.slice(0, 40);
      const containsXpath = `//${tag}[contains(normalize-space(.), ${xpathLiteral(containsText)})]`;
      addCandidate(list, seen, 'XPath', exactXpath, 'xpath_text', countXpath(exactXpath), {});
      addCandidate(list, seen, 'XPath', containsXpath, 'text_role', countXpath(containsXpath), {});
    }

    const fallbackPath = buildPath(el);
    if (fallbackPath) {
      addCandidate(list, seen, 'CSS', fallbackPath, 'nth_fallback', countCss(fallbackPath), { uses_nth: true });
      addCandidate(
        list,
        seen,
        'Selenium',
        `By.CSS_SELECTOR(\"${fallbackPath.replace(/\\/g, '\\\\').replace(/\"/g, '\\\"')}\")`,
        'nth_fallback',
        countCss(fallbackPath),
        { selector_kind: 'css', selector_value: fallbackPath, uses_nth: true }
      );
    }

    return list.slice(0, 6);
  }

  function buildSummary(el) {
    const attrs = {};
    for (const attr of Array.from(el.attributes || [])) {
      attrs[attr.name] = attr.value;
    }

    const ancestry = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && ancestry.length < 14) {
      let nth = 1;
      let sibling = current;
      while ((sibling = sibling.previousElementSibling)) {
        if ((sibling.tagName || '').toLowerCase() === (current.tagName || '').toLowerCase()) {
          nth += 1;
        }
      }
      ancestry.push({
        tag: (current.tagName || '').toLowerCase(),
        id: current.id || '',
        role: current.getAttribute('role') || '',
        class: current.className || '',
        nth: String(nth),
        'data-testid': current.getAttribute('data-testid') || '',
        'data-test': current.getAttribute('data-test') || '',
        'data-qa': current.getAttribute('data-qa') || '',
      });
      current = current.parentElement;
    }

    const classes = Array.from(el.classList || []).filter(Boolean);
    const valueText = normalizeText(el.value || '', 200);
    const textSnippet = normalizeText(el.innerText || el.textContent || '', 200) || valueText;
    const labels = el.labels ? Array.from(el.labels) : [];
    const labelText = labels.length ? normalizeText(labels[0].innerText || labels[0].textContent || '', 200) : '';
    const outerHtml = normalizeText(el.outerHTML || '', 320);

    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || null,
      classes,
      name: el.getAttribute('name') || null,
      role: el.getAttribute('role') || null,
      text: textSnippet || null,
      placeholder: el.getAttribute('placeholder') || null,
      aria_label: el.getAttribute('aria-label') || null,
      label_text: labelText || null,
      outer_html: outerHtml,
      attributes: attrs,
      ancestry,
    };
  }

  function capturePayloadForPoint(rawX, rawY, fromDevicePixels) {
    const dpr = Number(window.devicePixelRatio || 1) || 1;
    const visualScale = Number(window.visualViewport && window.visualViewport.scale ? window.visualViewport.scale : 1) || 1;
    let viewportX = Number(rawX);
    let viewportY = Number(rawY);
    if (!Number.isFinite(viewportX) || !Number.isFinite(viewportY)) {
      return {
        ok: false,
        error: 'INVALID_COORDINATES',
        warning: 'Inspect click coordinates are invalid.',
      };
    }

    if (fromDevicePixels) {
      viewportX = viewportX / dpr;
      viewportY = viewportY / dpr;
      if (visualScale > 0 && visualScale !== 1) {
        viewportX = viewportX / visualScale;
        viewportY = viewportY / visualScale;
      }
    }

    const maxX = Math.max(0, (window.innerWidth || 1) - 1);
    const maxY = Math.max(0, (window.innerHeight || 1) - 1);
    viewportX = Math.min(Math.max(0, viewportX), maxX);
    viewportY = Math.min(Math.max(0, viewportY), maxY);

    const element = document.elementFromPoint(viewportX, viewportY);
    if (!element) {
      return {
        ok: false,
        error: 'ELEMENT_NOT_FOUND',
        warning: 'Inspect could not resolve an element at this point.',
        click: {
          x: Number(rawX),
          y: Number(rawY),
          viewportX,
          viewportY,
          dpr,
          visualScale,
        },
      };
    }

    const tag = (element.tagName || '').toLowerCase();
    if (tag === 'iframe') {
      return {
        ok: false,
        error: 'IFRAME_UNAVAILABLE',
        warning: 'Cross-origin iframe content cannot be inspected from embedded mode.',
        click: {
          x: Number(rawX),
          y: Number(rawY),
          viewportX,
          viewportY,
          dpr,
          visualScale,
        },
      };
    }

    const summary = buildSummary(element);
    const candidates = buildCandidates(element, summary);
    return {
      ok: true,
      summary,
      candidates,
      click: {
        x: Number(rawX),
        y: Number(rawY),
        viewportX,
        viewportY,
        dpr,
        visualScale,
      },
    };
  }

  function ensureOverlay(state) {
    if (state.overlay) {
      return state.overlay;
    }
    const overlay = document.createElement('div');
    overlay.id = '__inspectelement_overlay';
    overlay.style.position = 'fixed';
    overlay.style.pointerEvents = 'none';
    overlay.style.zIndex = '2147483647';
    overlay.style.border = '2px solid #06b6d4';
    overlay.style.background = 'rgba(6, 182, 212, 0.12)';
    overlay.style.borderRadius = '2px';
    overlay.style.display = 'none';
    document.documentElement.appendChild(overlay);
    state.overlay = overlay;
    return overlay;
  }

  function hideOverlay(state) {
    if (!state.overlay) {
      return;
    }
    state.overlay.style.display = 'none';
    state.highlighted = null;
  }

  function positionOverlay(state, el) {
    const overlay = ensureOverlay(state);
    if (!el || el === overlay || !el.getBoundingClientRect) {
      hideOverlay(state);
      return;
    }
    const rect = el.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      hideOverlay(state);
      return;
    }
    overlay.style.display = 'block';
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    state.highlighted = el;
  }

  function installInspector() {
    if (window.__inspectelementEmbeddedInstalled) {
      if (window.__inspectelementSetEnabled) {
        window.__inspectelementSetEnabled(!!window.__inspectelementDesiredEnabled);
      }
      return;
    }

    const state = {
      enabled: false,
      overlay: null,
      highlighted: null,
      onMove: null,
      onClick: null,
      onScroll: null,
    };

    function attachListeners() {
      if (state.onMove) {
        return;
      }

      state.onMove = (event) => {
        if (!state.enabled) {
          return;
        }
        if (event.target === state.overlay) {
          return;
        }
        positionOverlay(state, event.target);
      };

      state.onClick = (event) => {
        if (!state.enabled) {
          return;
        }
        if (event.target === state.overlay) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();

        const payload = capturePayloadForPoint(event.clientX, event.clientY, false);
        if (window.__inspectBridge && window.__inspectBridge.report && payload && payload.ok) {
          window.__inspectBridge.report({
            summary: payload.summary,
            candidates: payload.candidates,
            click: payload.click,
          });
        } else if (window.__inspectBridge && window.__inspectBridge.log && payload && payload.warning) {
          window.__inspectBridge.log(`Embedded inspect warning: ${payload.warning}`);
        }
        return false;
      };

      state.onScroll = () => {
        if (!state.enabled || !state.highlighted) {
          return;
        }
        positionOverlay(state, state.highlighted);
      };

      document.addEventListener('mousemove', state.onMove, true);
      document.addEventListener('click', state.onClick, true);
      window.addEventListener('scroll', state.onScroll, true);
    }

    function detachListeners() {
      if (!state.onMove) {
        return;
      }
      document.removeEventListener('mousemove', state.onMove, true);
      document.removeEventListener('click', state.onClick, true);
      window.removeEventListener('scroll', state.onScroll, true);
      state.onMove = null;
      state.onClick = null;
      state.onScroll = null;
      hideOverlay(state);
    }

    window.__inspectelementSetEnabled = (enabled) => {
      state.enabled = !!enabled;
      ensureOverlay(state);
      if (state.enabled) {
        attachListeners();
      } else {
        detachListeners();
      }
      return state.enabled;
    };

    window.__inspectelementCaptureFromPoint = (x, y, fromDevicePixels = true) => {
      return capturePayloadForPoint(x, y, !!fromDevicePixels);
    };

    window.__inspectelementEmbeddedInstalled = true;
    if (window.__inspectBridge && window.__inspectBridge.log) {
      window.__inspectBridge.log('JS inspector injected successfully');
    }
    window.__inspectelementSetEnabled(!!window.__inspectelementDesiredEnabled);
  }

  function ensureBridgeAndInstall() {
    installInspector();

    if (window.__inspectBridge) {
      return;
    }

    if (typeof QWebChannel === 'undefined') {
      if (!window.__inspectelementQWebChannelLoading) {
        window.__inspectelementQWebChannelLoading = true;
        const script = document.createElement('script');
        script.src = 'qrc:///qtwebchannel/qwebchannel.js';
        script.async = true;
        script.onload = () => {
          window.__inspectelementQWebChannelLoading = false;
          ensureBridgeAndInstall();
        };
        script.onerror = () => {
          window.__inspectelementQWebChannelLoading = false;
        };
        document.documentElement.appendChild(script);
      }
      return;
    }

    if (typeof qt === 'undefined' || !qt.webChannelTransport) {
      return;
    }

    if (window.__inspectelementQWebChannelReady) {
      return;
    }
    window.__inspectelementQWebChannelReady = true;

    new QWebChannel(qt.webChannelTransport, (channel) => {
      window.__inspectBridge = channel.objects.inspectBridge;
    });
  }

  window.__inspectelementDesiredEnabled = !!window.__inspectelementDesiredEnabled;
  ensureBridgeAndInstall();
})();
"""


def build_element_summary_from_payload(summary_payload: dict[str, Any]) -> ElementSummary:
    tag = str(summary_payload.get("tag") or "unknown").strip().lower() or "unknown"
    raw_ancestry = summary_payload.get("ancestry")
    ancestry: list[dict[str, str]] = []
    if isinstance(raw_ancestry, list):
        for item in raw_ancestry:
            if not isinstance(item, dict):
                continue
            normalized = {str(key): str(value) for key, value in item.items() if value is not None}
            ancestry.append(normalized)

    table_root_candidates = detect_table_root_candidates(ancestry)
    table_root = None
    table_roots: list[dict[str, str]] = []
    for candidate in table_root_candidates:
        candidate_payload = {
            "selector_type": candidate.selector_type,
            "selector_value": candidate.selector_value,
            "reason": candidate.reason,
            "tag": candidate.tag,
            "locator_name_hint": candidate.locator_name_hint,
            "stable": "true" if candidate.stable else "false",
            "warning": candidate.warning or "",
        }
        table_roots.append(candidate_payload)
        if table_root is None:
            table_root = dict(candidate_payload)

    attributes: dict[str, str] = {}
    raw_attrs = summary_payload.get("attributes")
    if isinstance(raw_attrs, dict):
        attributes = {str(key): str(value) for key, value in raw_attrs.items() if value is not None}

    return ElementSummary(
        tag=tag,
        id=_as_optional_text(summary_payload.get("id")),
        classes=normalize_classes(summary_payload.get("classes") or []),
        name=_as_optional_text(summary_payload.get("name")),
        role=_as_optional_text(summary_payload.get("role")),
        text=_as_optional_text(summary_payload.get("text")),
        placeholder=_as_optional_text(summary_payload.get("placeholder")),
        aria_label=_as_optional_text(summary_payload.get("aria_label")),
        label_text=_as_optional_text(summary_payload.get("label_text")),
        attributes=attributes,
        ancestry=ancestry,
        table_root=table_root,
        table_roots=table_roots,
    )


def build_locator_candidates_from_payload(
    candidate_payload: list[dict[str, Any]],
    learning_weights: dict[str, float] | None = None,
    limit: int = 6,
) -> list[LocatorCandidate]:
    drafts: list[LocatorCandidate] = []
    seen: set[tuple[str, str]] = set()
    for item in candidate_payload:
        if not isinstance(item, dict):
            continue
        locator_type = str(item.get("locator_type") or "").strip()
        locator = str(item.get("locator") or "").strip()
        rule = str(item.get("rule") or "manual").strip() or "manual"
        if locator_type not in {"CSS", "XPath", "Playwright", "Selenium"}:
            continue
        if not locator:
            continue
        key = (locator_type, locator)
        if key in seen:
            continue
        seen.add(key)

        uniqueness_raw = item.get("uniqueness_count", 0)
        try:
            uniqueness_count = int(uniqueness_raw)
        except (TypeError, ValueError):
            uniqueness_count = 0

        raw_metadata = item.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        normalized_metadata = {str(meta_key): meta_value for meta_key, meta_value in metadata.items()}

        drafts.append(
            LocatorCandidate(
                locator_type=locator_type,
                locator=locator,
                rule=rule,
                uniqueness_count=max(0, uniqueness_count),
                metadata=normalized_metadata,
            )
        )

    if not drafts:
        return []

    scored = score_candidates(drafts, learning_weights or {})
    return scored[: max(1, limit)]


def build_capture_from_point_script(x: int, y: int) -> str:
    return (
        "(() => {"
        "  if (!window.__inspectelementCaptureFromPoint) {"
        "    return {ok:false,error:'INSPECTOR_NOT_READY',warning:'Inspector is not ready on this page.'};"
        "  }"
        f"  const _x = {int(x)};"
        f"  const _y = {int(y)};"
        "  const first = window.__inspectelementCaptureFromPoint(_x, _y, false);"
        "  if (first && first.ok) { return first; }"
        "  const second = window.__inspectelementCaptureFromPoint(_x, _y, true);"
        "  if (second && second.ok) { return second; }"
        "  if (first && first.warning) { return first; }"
        "  return second || first || {ok:false,error:'CAPTURE_FAILED',warning:'Inspect capture failed.'};"
        "})();"
    )


def build_fallback_locator_payload(summary_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tag = str(summary_payload.get("tag") or "div").strip().lower() or "div"
    raw_attrs = summary_payload.get("attributes")
    attributes = {str(k): str(v) for k, v in (raw_attrs.items() if isinstance(raw_attrs, dict) else []) if v is not None}
    classes = normalize_classes(summary_payload.get("classes") or [])
    text = _normalize_space(str(summary_payload.get("text") or attributes.get("value") or ""))[:120]

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        locator_type: str,
        locator: str,
        rule: str,
        uniqueness_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (locator_type, locator)
        if not locator or key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "locator_type": locator_type,
                "locator": locator,
                "rule": rule,
                "uniqueness_count": uniqueness_count,
                "metadata": dict(metadata or {}),
            }
        )

    element_id = str(summary_payload.get("id") or attributes.get("id") or "").strip()
    if element_id:
        css_id = f"#{_escape_css_identifier(element_id)}"
        add("CSS", css_id, "stable_attr:id", 1)
        add("XPath", f"//*[@id={_xpath_literal(element_id)}]", "stable_attr:id", 1)
        add(
            "Selenium",
            f'By.id("{_escape_java_string(element_id)}")',
            "stable_attr:id",
            1,
            {"selector_kind": "id", "selector_value": element_id},
        )

    stable_attrs = ("data-testid", "data-test", "data-qa", "name", "aria-label", "placeholder", "role", "title", "href", "type")
    for attr in stable_attrs:
        value = str(attributes.get(attr) or "").strip()
        if not value:
            continue
        css = f'{tag}[{attr}="{_escape_css_string(value)}"]'
        xpath = f"//*[@{attr}={_xpath_literal(value)}]"
        add("CSS", css, f"stable_attr:{attr}", 1)
        add("XPath", xpath, f"stable_attr:{attr}", 1)
        add(
            "Selenium",
            f'By.cssSelector("{_escape_java_string(css)}")',
            f"stable_attr:{attr}",
            1,
            {"selector_kind": "css", "selector_value": css},
        )
        if attr == "name":
            add(
                "Selenium",
                f'By.name("{_escape_java_string(value)}")',
                "stable_attr:name",
                1,
                {"selector_kind": "name", "selector_value": value},
            )
        if attr == "data-testid":
            add(
                "Playwright",
                f'page.get_by_test_id("{_escape_java_string(value)}")',
                "stable_attr:data-testid",
                1,
                {"playwright_kind": "test_id", "value": value},
            )

    meaningful_classes = [name for name in classes if not _looks_dynamic_class(name)]
    if meaningful_classes:
        class_tokens = meaningful_classes[:2]
        css = f"{tag}{''.join(f'.{_escape_css_identifier(token)}' for token in class_tokens)}"
        dynamic_count = max(0, len(classes) - len(meaningful_classes))
        add("CSS", css, "meaningful_class", 2, {"dynamic_class_count": dynamic_count})
        add(
            "Selenium",
            f'By.cssSelector("{_escape_java_string(css)}")',
            "meaningful_class",
            2,
            {"selector_kind": "css", "selector_value": css, "dynamic_class_count": dynamic_count},
        )

    if text:
        text_literal = _xpath_literal(text)
        add("XPath", f"//{tag}[text()={text_literal}]", "xpath_text", 2)
        add("XPath", f"//{tag}[normalize-space(.)={text_literal}]", "xpath_text", 2)
        generic = f"//*[self::button or self::a or self::span][normalize-space(.)={text_literal}]"
        add("XPath", f"({generic})[1]", "xpath_text", 2)
        add("XPath", f"(//*[self::button or self::span or self::a][normalize-space(text())={text_literal}])[2]", "xpath_text", 2)
        if tag in {"div", "span", "label"}:
            add(
                "XPath",
                f"//{tag}[normalize-space(.)={text_literal}]/following-sibling::div[contains(@class,'list-item-value')]",
                "xpath_text",
                1,
            )

    raw_ancestry = summary_payload.get("ancestry")
    ancestry = [item for item in raw_ancestry if isinstance(item, dict)] if isinstance(raw_ancestry, list) else []
    in_ant_modal = any("ant-modal" in str(item.get("class") or "") for item in ancestry)
    if in_ant_modal:
        add(
            "XPath",
            "//div[contains(@class,'ant-modal') and not(contains(@style,'display:none'))]//label[contains(@class,'ant-radio-wrapper')]",
            "ancestor",
            1,
        )
        if text:
            add(
                "XPath",
                f"(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]//button[.//span[normalize-space(.)={_xpath_literal(text)}] and not(contains(@class,'is-hidden'))])[1]",
                "xpath_text",
                1,
            )

    return rows


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    quoted = [f"'{part}'" for part in parts]
    return "concat(" + ", \"'\", ".join(quoted) + ")"


def _escape_css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_java_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_css_identifier(value: str) -> str:
    chunks: list[str] = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            chunks.append(char)
        else:
            chunks.append(f"\\{ord(char):x} ")
    return "".join(chunks)


def _looks_dynamic_class(value: str) -> bool:
    token = value.strip()
    if not token:
        return True
    patterns = (
        r"^css-[a-z0-9_-]{4,}$",
        r"^jss\d+$",
        r"^sc-[a-z0-9]+$",
        r"^[a-f0-9]{8,}$",
        r"^[a-z]+__[a-z]+___[a-z0-9]{5,}$",
        r"^_?[a-z]{1,3}[0-9a-f]{6,}$",
    )
    return any(re.match(pattern, token, flags=re.IGNORECASE) for pattern in patterns)
