from __future__ import annotations

from playwright.sync_api import ElementHandle

from .locator_generator import normalize_classes
from .models import ElementSummary


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
          const labelText = labels.length ? (labels[0].innerText || labels[0].textContent || '').trim().replace(/\s+/g, ' ') : null;

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
            attributes: attrs,
          };
        }
        """
    )

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
        attributes={str(k): str(v) for k, v in payload.get("attributes", {}).items()},
    )
