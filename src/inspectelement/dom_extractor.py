from __future__ import annotations

from typing import Any, TYPE_CHECKING

from playwright.sync_api import ElementHandle

from .locator_generator import normalize_classes
from .models import DomSnapshot, ElementSummary
from .table_root_detection import detect_table_root_candidates

if TYPE_CHECKING:
    from playwright.sync_api import Page


def extract_element_summary(element: ElementHandle) -> ElementSummary:
    payload = element.evaluate(
        """
        (el) => {
          const attrs = {};
          for (const attr of el.attributes) {
            attrs[attr.name] = attr.value;
          }

          const tag = el.tagName.toLowerCase();
          const explicitRole = el.getAttribute('role');
          let inferredRole = null;
          if (!explicitRole) {
            if (tag === 'button') inferredRole = 'button';
            if (tag === 'a' && el.getAttribute('href')) inferredRole = 'link';
            if (tag === 'input') {
              const inputType = (el.getAttribute('type') || 'text').toLowerCase();
              if (['button', 'submit', 'reset'].includes(inputType)) inferredRole = 'button';
              if (['checkbox'].includes(inputType)) inferredRole = 'checkbox';
              if (['radio'].includes(inputType)) inferredRole = 'radio';
              if (['search', 'text', 'email', 'password', 'url', 'tel'].includes(inputType)) inferredRole = 'textbox';
            }
          }

          const classList = Array.from(el.classList || []);
          const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200);
          const labels = el.labels ? Array.from(el.labels) : [];
          const labelText = labels.length
            ? (labels[0].innerText || labels[0].textContent || '').trim().replace(/\s+/g, ' ')
            : null;

          const ariaLabelledBy = (el.getAttribute('aria-labelledby') || '').trim();
          let ariaLabelledByText = null;
          if (ariaLabelledBy) {
            const chunks = ariaLabelledBy
              .split(/\s+/)
              .map((id) => id && document.getElementById(id))
              .filter(Boolean)
              .map((node) => (node.innerText || node.textContent || '').trim().replace(/\s+/g, ' '))
              .filter(Boolean);
            if (chunks.length) {
              ariaLabelledByText = chunks.join(' ').slice(0, 200);
            }
          }

          const valueText = (typeof el.value === 'string' && el.value)
            ? String(el.value).trim().replace(/\s+/g, ' ').slice(0, 200)
            : ((el.getAttribute('value') || '').trim().replace(/\s+/g, ' ').slice(0, 200) || null);

          const ancestry = [];
          let current = el;
          while (current && current.nodeType === Node.ELEMENT_NODE && ancestry.length < 14) {
            let nth = 1;
            let sibling = current;
            while ((sibling = sibling.previousElementSibling)) {
              if (sibling.tagName === current.tagName) nth += 1;
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

          return {
            tag,
            id: el.id || null,
            classes: classList,
            name: el.getAttribute('name') || null,
            role: explicitRole || inferredRole,
            text: text || null,
            placeholder: el.getAttribute('placeholder') || null,
            aria_label: el.getAttribute('aria-label') || null,
            label_text: labelText || null,
            title: el.getAttribute('title') || null,
            value_text: valueText || null,
            aria_labelledby_text: ariaLabelledByText || null,
            attributes: attrs,
            ancestry,
          };
        }
        """
    )

    ancestry = [
        {str(key): str(value) for key, value in item.items() if value is not None}
        for item in payload.get("ancestry", [])
        if isinstance(item, dict)
    ]
    table_root_candidates = detect_table_root_candidates(ancestry)
    table_root = None
    table_roots: list[dict[str, str]] = []
    for candidate in table_root_candidates:
        table_roots.append(
            {
                "selector_type": candidate.selector_type,
                "selector_value": candidate.selector_value,
                "reason": candidate.reason,
                "tag": candidate.tag,
                "locator_name_hint": candidate.locator_name_hint,
                "stable": "true" if candidate.stable else "false",
                "warning": candidate.warning or "",
            }
        )
    if table_root_candidates:
        table_root_candidate = table_root_candidates[0]
        table_root = {
            "selector_type": table_root_candidate.selector_type,
            "selector_value": table_root_candidate.selector_value,
            "reason": table_root_candidate.reason,
            "tag": table_root_candidate.tag,
            "locator_name_hint": table_root_candidate.locator_name_hint,
            "stable": "true" if table_root_candidate.stable else "false",
            "warning": table_root_candidate.warning or "",
        }

    return ElementSummary(
        tag=payload.get("tag", "unknown"),
        id=payload.get("id"),
        classes=normalize_classes(payload.get("classes", [])),
        name=payload.get("name"),
        role=payload.get("role"),
        text=payload.get("text"),
        placeholder=payload.get("placeholder"),
        aria_label=payload.get("aria_label"),
        label_text=payload.get("label_text"),
        title=payload.get("title"),
        value_text=payload.get("value_text"),
        aria_labelledby_text=payload.get("aria_labelledby_text"),
        attributes={str(k): str(v) for k, v in payload.get("attributes", {}).items()},
        ancestry=ancestry,
        table_root=table_root,
        table_roots=table_roots,
    )


def extract_dom_snapshot(page: Page) -> DomSnapshot:
    payload: dict[str, Any] = page.evaluate(
        """
        () => {
          const nodes = Array.from(document.querySelectorAll('*'));
          const tagHistogram = {};
          const attributeHistogram = {};
          let textNodeCount = 0;

          for (const node of nodes) {
            const tag = (node.tagName || '').toLowerCase();
            if (tag) {
              tagHistogram[tag] = (tagHistogram[tag] || 0) + 1;
            }

            for (const attr of Array.from(node.attributes || [])) {
              attributeHistogram[attr.name] = (attributeHistogram[attr.name] || 0) + 1;
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
            attribute_histogram: attributeHistogram,
          };
        }
        """
    )

    return DomSnapshot(
        node_count=int(payload.get("node_count", 0) or 0),
        text_node_count=int(payload.get("text_node_count", 0) or 0),
        title=str(payload.get("title", "") or ""),
        url=str(payload.get("url", "") or ""),
        tag_histogram={str(k): int(v) for k, v in dict(payload.get("tag_histogram", {})).items()},
        attribute_histogram={str(k): int(v) for k, v in dict(payload.get("attribute_histogram", {})).items()},
    )
