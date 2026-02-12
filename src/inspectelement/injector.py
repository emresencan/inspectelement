from __future__ import annotations

from playwright.sync_api import Page

INJECT_SCRIPT = r"""
(() => {
  if (window.__inspectelementInstalled) {
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

  function ensureOverlay() {
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

  function hideOverlay() {
    const overlay = ensureOverlay();
    overlay.style.display = 'none';
    state.highlighted = null;
  }

  function positionOverlay(el) {
    const overlay = ensureOverlay();
    if (!el || el === overlay || !el.getBoundingClientRect) {
      hideOverlay();
      return;
    }
    const rect = el.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      hideOverlay();
      return;
    }

    overlay.style.display = 'block';
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    state.highlighted = el;
  }

  function buildPath(el) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
      const tag = current.tagName.toLowerCase();
      if (!tag) break;
      if (current.id) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      let part = tag;
      let sibling = current;
      let index = 1;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName.toLowerCase() === tag) {
          index += 1;
        }
      }
      part += `:nth-of-type(${index})`;
      parts.unshift(part);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function attachListeners() {
    if (state.onMove) {
      return;
    }

    state.onMove = (event) => {
      if (!state.enabled) return;
      if (event.target === state.overlay) return;
      positionOverlay(event.target);
    };

    state.onClick = (event) => {
      if (!state.enabled) return;
      if (event.target === state.overlay) return;

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();

      const el = event.target;
      const captureId = `ie-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      el.setAttribute('data-inspectelement-capture', captureId);

      const payload = {
        captureId,
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || '').trim().slice(0, 200),
        id: el.id || null,
        classList: Array.from(el.classList || []),
        name: el.getAttribute('name') || null,
        role: el.getAttribute('role') || null,
        ariaLabel: el.getAttribute('aria-label') || null,
        placeholder: el.getAttribute('placeholder') || null,
        inputType: el.getAttribute('type') || null,
        href: el.getAttribute('href') || null,
        path: buildPath(el),
      };

      if (typeof window.__inspectelementReport === 'function') {
        window.__inspectelementReport(payload);
      }
    };

    state.onScroll = () => {
      if (!state.enabled) return;
      if (state.highlighted) {
        positionOverlay(state.highlighted);
      }
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
    hideOverlay();
  }

  window.__inspectelementSetEnabled = (enabled) => {
    state.enabled = !!enabled;
    ensureOverlay();
    if (state.enabled) {
      attachListeners();
      return;
    }
    detachListeners();
  };

  window.__inspectelementRemove = () => {
    state.enabled = false;
    detachListeners();
    if (state.overlay && state.overlay.parentNode) {
      state.overlay.parentNode.removeChild(state.overlay);
    }
    state.overlay = null;
    state.highlighted = null;
  };

  window.__inspectelementInstalled = true;
})();
"""


def ensure_injected(page: Page, enabled: bool) -> None:
    for frame in page.frames:
        try:
            frame.evaluate(INJECT_SCRIPT)
            frame.evaluate("(isEnabled) => window.__inspectelementSetEnabled(!!isEnabled)", enabled)
        except Exception:
            continue


def disable_overlay(page: Page) -> None:
    for frame in page.frames:
        try:
            frame.evaluate(
                """
                () => {
                    if (window.__inspectelementSetEnabled) {
                        window.__inspectelementSetEnabled(false);
                    }
                }
                """
            )
        except Exception:
            continue
