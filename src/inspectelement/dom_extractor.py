from __future__ import annotations

from playwright.sync_api import ElementHandle

from .locator_generator import normalize_classes
from .models import ElementSummary
from .table_root_detection import detect_table_root_candidates


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
          const liveValue = (typeof el.value === 'string') ? el.value : '';
          const normalizedInnerText = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 240);
          const normalizedTextContent = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 240);
          const text = (normalizedInnerText || normalizedTextContent || liveValue || '').trim();
          const labels = el.labels ? Array.from(el.labels) : [];
          const labelText = labels.length ? (labels[0].innerText || labels[0].textContent || '').trim().replace(/\s+/g, ' ') : null;
          const previousSiblingText = el.previousElementSibling
            ? (el.previousElementSibling.innerText || el.previousElementSibling.textContent || '').trim().replace(/\s+/g, ' ')
            : '';
          const outerHtml = (el.outerHTML || '').slice(0, 1500);

          const tagName = (el.tagName || '').toLowerCase();
          if (liveValue) attrs.value = liveValue;
          const hrefValue = (el.getAttribute('href') || (el.href || '') || '').trim();
          if (hrefValue) attrs.href = hrefValue;
          const titleValue = (el.getAttribute('title') || '').trim();
          if (titleValue) attrs.title = titleValue;
          const typeValue = (el.getAttribute('type') || '').trim();
          if (typeValue) attrs.type = typeValue;
          if (explicitRole) attrs.role = explicitRole;
          if (el.getAttribute('data-testid')) attrs['data-testid'] = el.getAttribute('data-testid');
          if (el.getAttribute('data-test')) attrs['data-test'] = el.getAttribute('data-test');
          if (el.getAttribute('data-qa')) attrs['data-qa'] = el.getAttribute('data-qa');
          if (el.getAttribute('data-cy')) attrs['data-cy'] = el.getAttribute('data-cy');
          if (el.getAttribute('placeholder')) attrs.placeholder = el.getAttribute('placeholder');
          if (el.getAttribute('aria-label')) attrs['aria-label'] = el.getAttribute('aria-label');
          if (el.getAttribute('aria-labelledby')) attrs['aria-labelledby'] = el.getAttribute('aria-labelledby');
          if (normalizedTextContent) attrs.__text_content = normalizedTextContent;
          attrs.__tag = tagName;
          if (previousSiblingText) attrs.__prev_sibling_text = previousSiblingText;

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
              style: current.getAttribute('style') || '',
              'aria-hidden': current.getAttribute('aria-hidden') || '',
              nth: String(nth),
              'data-testid': current.getAttribute('data-testid') || '',
              'data-test': current.getAttribute('data-test') || '',
              'data-qa': current.getAttribute('data-qa') || '',
              'data-cy': current.getAttribute('data-cy') || '',
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
            outer_html: outerHtml || null,
            sibling_label_text: previousSiblingText || null,
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
        outer_html=payload.get("outer_html"),
        sibling_label_text=payload.get("sibling_label_text"),
        attributes={str(k): str(v) for k, v in payload.get("attributes", {}).items()},
        ancestry=ancestry,
        table_root=table_root,
        table_roots=table_roots,
    )
