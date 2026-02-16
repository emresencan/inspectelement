from __future__ import annotations

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

    return list.slice(0, 12);
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
    const textSnippet = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200);
    const labels = el.labels ? Array.from(el.labels) : [];
    const labelText = labels.length ? (labels[0].innerText || labels[0].textContent || '').replace(/\s+/g, ' ').trim() : '';
    const outerHtml = (el.outerHTML || '').replace(/\s+/g, ' ').trim().slice(0, 320);

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

        const el = event.target;
        const summary = buildSummary(el);
        const candidates = buildCandidates(el, summary);
        const payload = {
          summary,
          candidates,
        };

        if (window.__inspectBridge && window.__inspectBridge.report) {
          window.__inspectBridge.report(payload);
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

    window.__inspectelementEmbeddedInstalled = true;
    if (window.__inspectBridge && window.__inspectBridge.log) {
      window.__inspectBridge.log('JS inspector injected successfully');
    }
    window.__inspectelementSetEnabled(!!window.__inspectelementDesiredEnabled);
  }

  function ensureBridgeAndInstall() {
    if (window.__inspectBridge) {
      installInspector();
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

    new QWebChannel(qt.webChannelTransport, (channel) => {
      window.__inspectBridge = channel.objects.inspectBridge;
      installInspector();
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
    limit: int = 5,
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


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
