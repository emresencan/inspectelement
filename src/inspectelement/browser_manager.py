from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from .dom_extractor import extract_element_summary
from .injector import disable_overlay, ensure_injected
from .learning_store import LearningStore
from .locator_generator import generate_locator_candidates
from .models import ElementSummary, LocatorCandidate, PageContext
from .runtime_checks import (
    _is_missing_browser_error,
    build_id_selector_candidates,
    payload_matches_observed_element,
)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

CaptureCallback = Callable[[ElementSummary, list[LocatorCandidate]], None]
StatusCallback = Callable[[str], None]
PageInfoCallback = Callable[[str, str], None]


class BrowserManager:
    def __init__(
        self,
        on_capture: CaptureCallback,
        on_status: StatusCallback,
        on_page_info: PageInfoCallback,
        learning_store: LearningStore | None = None,
    ) -> None:
        self._on_capture = on_capture
        self._on_status = on_status
        self._on_page_info = on_page_info
        self.learning_store = learning_store or LearningStore()

        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._inspect_enabled = False
        self._running = True

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._state_lock = threading.Lock()
        self._last_summary: ElementSummary | None = None
        self._page_context: PageContext | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def launch(self, url: str) -> None:
        self._commands.put(("launch", url.strip()))

    def set_inspect_mode(self, enabled: bool) -> None:
        self._commands.put(("inspect", bool(enabled)))

    def reset_learning(self) -> None:
        self._commands.put(("reset_learning", None))

    def record_feedback(self, candidate: LocatorCandidate, was_good: bool) -> bool:
        with self._state_lock:
            page_context = self._page_context
            summary = self._last_summary

        if not page_context or not summary:
            return False

        self.learning_store.record_feedback(page_context, summary, candidate, was_good)
        return True

    def shutdown(self) -> None:
        if not self._started:
            return
        self._commands.put(("shutdown", None))
        self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self._on_status(f"Playwright is not available: {exc}")
            return

        try:
            with sync_playwright() as playwright:
                self._playwright = playwright
                self._event_loop()
        except Exception as exc:
            self._on_status(f"Browser worker crashed: {exc}")
        finally:
            self._cleanup()

    def _event_loop(self) -> None:
        while self._running:
            try:
                command, payload = self._commands.get(timeout=0.1)
                self._handle_command(command, payload)
            except queue.Empty:
                self._pump_events()
            except Exception as exc:
                self._on_status(f"Command error: {exc}")

    def _handle_command(self, command: str, payload: Any) -> None:
        if command == "shutdown":
            self._running = False
            return
        if command == "launch":
            self._handle_launch(str(payload))
            return
        if command == "inspect":
            self._handle_inspect_mode(bool(payload))
            return
        if command == "reset_learning":
            self.learning_store.reset()
            self._on_status("Learning store reset.")

    def _handle_launch(self, raw_url: str) -> None:
        if not self._playwright:
            self._on_status("Playwright is not available.")
            return

        url = self._normalize_url(raw_url)
        if not url:
            self._on_status("Please enter a URL.")
            return

        self._close_page_and_context()

        if not self._browser:
            try:
                self._browser = self._playwright.chromium.launch(headless=False)
            except Exception as exc:
                if _is_missing_browser_error(exc):
                    self._on_status("Chromium not installed. Run: python -m playwright install chromium")
                    return
                self._on_status(f"Failed to launch Chromium: {exc}")
                return

        self._context = self._browser.new_context(viewport=None)
        self._page = self._context.new_page()
        self._page.expose_binding("__inspectelementReport", self._on_capture_from_js)
        self._page.on("domcontentloaded", lambda: self._on_dom_content_loaded())

        self._on_status(f"Launching: {url}")
        self._page.goto(url, wait_until="domcontentloaded")
        self._update_page_context(self._page)
        ensure_injected(self._page, self._inspect_enabled)
        self._on_page_info(self._page.title(), self._page.url)
        self._on_status("Browser launched.")

    def _handle_inspect_mode(self, enabled: bool) -> None:
        self._inspect_enabled = enabled
        if not self._page:
            self._on_status("Launch a page first.")
            return

        ensure_injected(self._page, enabled)
        if not enabled:
            disable_overlay(self._page)
        state = "ON" if enabled else "OFF"
        self._on_status(f"Inspect mode {state}.")

    def _on_dom_content_loaded(self) -> None:
        if not self._page:
            return
        try:
            ensure_injected(self._page, self._inspect_enabled)
            self._update_page_context(self._page)
            self._on_page_info(self._page.title(), self._page.url)
        except Exception as exc:
            self._on_status(f"Overlay injection failed: {exc}")

    def _on_capture_from_js(self, _source: Any, payload: dict[str, Any]) -> None:
        if not self._page:
            return

        capture_id = payload.get("captureId")
        if not capture_id:
            return

        element = None
        capture_selector = f'[data-inspectelement-capture="{capture_id}"]'
        element = self._page.query_selector(capture_selector)

        if not element:
            payload_id = payload.get("id")
            for id_selector in build_id_selector_candidates(payload_id):
                try:
                    element = self._page.query_selector(id_selector)
                except Exception:
                    element = None
                if element:
                    break

        if not element:
            path = payload.get("path")
            if isinstance(path, str) and path:
                try:
                    element = self._page.query_selector(path)
                except Exception:
                    element = None
        if not element:
            self._on_status("Captured element no longer available.")
            return

        summary = extract_element_summary(element)
        observed = {
            "tag": summary.tag,
            "text": summary.text,
            "aria_label": summary.aria_label,
            "placeholder": summary.placeholder,
            "name": summary.name,
        }
        if not payload_matches_observed_element(payload, observed):
            self._on_status("Captured element could not be re-identified (DOM changed).")
            try:
                element.evaluate("(el) => el.removeAttribute('data-inspectelement-capture')")
            except Exception:
                pass
            return

        weights = self.learning_store.get_rule_weights()
        candidates = generate_locator_candidates(self._page, element, summary, learning_weights=weights, limit=5)

        try:
            element.evaluate("(el) => el.removeAttribute('data-inspectelement-capture')")
        except Exception:
            pass

        with self._state_lock:
            self._last_summary = summary
        if self._page:
            self._update_page_context(self._page)

        self._on_capture(summary, candidates)

    def _update_page_context(self, page: Page) -> None:
        parsed = urlparse(page.url)
        context = PageContext(url=page.url, hostname=parsed.hostname or "", page_title=page.title())
        with self._state_lock:
            self._page_context = context

    def _pump_events(self) -> None:
        if not self._page:
            return
        try:
            self._page.wait_for_timeout(50)
        except Exception:
            pass

    def _close_page_and_context(self) -> None:
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
        self._page = None

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None

    def _cleanup(self) -> None:
        self._close_page_and_context()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    @staticmethod
    def _normalize_url(raw_url: str) -> str:
        if not raw_url:
            return ""
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        return f"https://{raw_url}"
