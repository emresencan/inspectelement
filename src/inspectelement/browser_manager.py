from __future__ import annotations

from dataclasses import replace
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from .dom_extractor import extract_element_summary
from .hybrid_capture import (
    classify_probe_payload,
    map_coordinates_to_viewport,
    map_hover_box_to_overlay,
    normalize_capture_coordinates,
    normalize_viewport_size,
    select_raw_and_refined_indices,
    should_sync_navigation,
)
from .learning_store import LearningStore
from .locator_generator import generate_locator_candidates
from .models import ElementSummary, LocatorCandidate, PageContext
from .override_logic import build_override_candidate, inject_override_candidate
from .runtime_checks import _is_missing_browser_error
from .selector_rules import is_obvious_root_container_locator

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, ElementHandle, Page, Playwright

CaptureCallback = Callable[[ElementSummary, list[LocatorCandidate]], None]
StatusCallback = Callable[[str], None]
PageInfoCallback = Callable[[str, str], None]
HoverBoxCallback = Callable[[dict[str, Any] | None], None]


class BrowserManager:
    def __init__(
        self,
        on_capture: CaptureCallback,
        on_status: StatusCallback,
        on_page_info: PageInfoCallback,
        on_hover_box: HoverBoxCallback | None = None,
        learning_store: LearningStore | None = None,
    ) -> None:
        self._on_capture = on_capture
        self._on_status = on_status
        self._on_page_info = on_page_info
        self._on_hover_box = on_hover_box or (lambda _box: None)
        self.learning_store = learning_store or LearningStore()
        self.logger = logging.getLogger("inspectelement.ui")

        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._inspect_enabled = False
        self._running = True
        self._capture_busy = False
        self._hover_busy = False

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._debug_browser: Browser | None = None
        self._debug_context: BrowserContext | None = None
        self._debug_page: Page | None = None

        self._state_lock = threading.Lock()
        self._last_summary: ElementSummary | None = None
        self._page_context: PageContext | None = None
        self._current_url: str = ""
        self._launch_viewport: tuple[int, int] = (1280, 720)
        self._last_synced_source_url: str = ""
        self._last_synced_scroll: tuple[int, int] = (0, 0)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def launch(self, url: str, viewport: tuple[int, int] | None = None) -> None:
        self._commands.put(("launch", {"url": url.strip(), "viewport": viewport}))

    def navigate(self, url: str) -> None:
        self._commands.put(("navigate", url.strip()))

    def sync_viewport(self, viewport: tuple[int, int] | None) -> None:
        self._commands.put(("sync_viewport", viewport))

    def capture_at_coordinates(
        self,
        x: float,
        y: float,
        viewport: tuple[int, int] | None = None,
        *,
        scroll: tuple[int, int] | None = None,
        source_url: str | None = None,
        source_dpr: float | None = None,
    ) -> None:
        payload = {
            "x": x,
            "y": y,
            "viewport_width": viewport[0] if viewport else None,
            "viewport_height": viewport[1] if viewport else None,
            "scroll_x": scroll[0] if scroll else None,
            "scroll_y": scroll[1] if scroll else None,
            "source_url": (source_url or "").strip() or None,
            "device_pixel_ratio": source_dpr,
        }
        self._commands.put(("capture_coordinates", payload))

    def probe_hover_at_coordinates(
        self,
        x: float,
        y: float,
        viewport: tuple[int, int] | None = None,
        *,
        scroll: tuple[int, int] | None = None,
        source_url: str | None = None,
        source_dpr: float | None = None,
    ) -> None:
        payload = {
            "x": x,
            "y": y,
            "viewport_width": viewport[0] if viewport else None,
            "viewport_height": viewport[1] if viewport else None,
            "scroll_x": scroll[0] if scroll else None,
            "scroll_y": scroll[1] if scroll else None,
            "source_url": (source_url or "").strip() or None,
            "device_pixel_ratio": source_dpr,
        }
        self._commands.put(("hover_coordinates", payload))

    def open_managed_inspector(self) -> None:
        self._commands.put(("open_managed_inspector", None))

    def set_inspect_mode(self, enabled: bool) -> None:
        self._commands.put(("inspect", bool(enabled)))

    def reset_learning(self) -> None:
        self._commands.put(("reset_learning", None))

    def clear_overrides(self) -> None:
        self._commands.put(("clear_overrides", None))

    def record_feedback(self, candidate: LocatorCandidate, was_good: bool) -> bool:
        return self._record_feedback_internal(candidate, was_good, locator_override=None, save_override=False)

    def record_feedback_with_edited_locator(self, candidate: LocatorCandidate, locator_text: str) -> tuple[bool, str]:
        return self._record_feedback_internal(
            candidate,
            True,
            locator_override=locator_text,
            save_override=True,
        )

    def _record_feedback_internal(
        self,
        candidate: LocatorCandidate,
        was_good: bool,
        locator_override: str | None,
        save_override: bool,
    ) -> tuple[bool, str] | bool:
        with self._state_lock:
            page_context = self._page_context
            summary = self._last_summary

        if not page_context or not summary:
            return (False, "Capture an element before sending feedback.") if save_override else False

        locator_text = (locator_override or candidate.locator).strip()
        if not locator_text:
            return (False, "Edited locator is empty.") if save_override else False

        if save_override and is_obvious_root_container_locator(locator_text):
            return False, "Root container locators cannot be saved as overrides."

        feedback_candidate = replace(candidate, locator=locator_text)
        self.learning_store.record_feedback(page_context, summary, feedback_candidate, was_good)

        if save_override:
            self.learning_store.save_override(
                page_context.hostname,
                summary.signature(),
                feedback_candidate.locator_type,
                locator_text,
            )
            return True, "Edited locator saved as preferred override."
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
            if isinstance(payload, dict):
                viewport = payload.get("viewport")
                parsed_viewport = viewport if isinstance(viewport, tuple) else None
                self._handle_launch(str(payload.get("url", "")), parsed_viewport)
            else:
                self._handle_launch(str(payload), None)
            return
        if command == "navigate":
            self._handle_navigate(str(payload))
            return
        if command == "sync_viewport":
            parsed_viewport = payload if isinstance(payload, tuple) else None
            self._handle_sync_viewport(parsed_viewport)
            return
        if command == "inspect":
            self._handle_inspect_mode(bool(payload))
            return
        if command == "capture_coordinates":
            if isinstance(payload, dict):
                self._handle_capture_coordinates(payload)
            return
        if command == "hover_coordinates":
            if isinstance(payload, dict):
                self._handle_hover_coordinates(payload)
            return
        if command == "open_managed_inspector":
            self._handle_open_managed_inspector()
            return
        if command == "reset_learning":
            self.learning_store.reset()
            self._on_status("Learning store reset.")
            return
        if command == "clear_overrides":
            self.learning_store.clear_overrides()
            self._on_status("Overrides cleared.")

    def _handle_launch(self, raw_url: str, viewport: tuple[int, int] | None) -> None:
        if not self._playwright:
            self._on_status("Playwright is not available.")
            return

        url = self._normalize_url(raw_url)
        if not url:
            self._on_status("Please enter a URL.")
            return

        self._close_page_and_context()
        if not self._ensure_browser():
            return

        viewport_width, viewport_height = normalize_viewport_size(
            viewport[0] if viewport else None,
            viewport[1] if viewport else None,
        )
        self._launch_viewport = (viewport_width, viewport_height)

        try:
            self._context = (
                self._browser.new_context(viewport={"width": viewport_width, "height": viewport_height})
                if self._browser
                else None
            )
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                self._on_status(f"Failed to create browser context: {exc}")
                return
            self._on_status("Managed browser was closed. Relaunching...")
            self._browser = None
            if not self._ensure_browser():
                return
            self._context = (
                self._browser.new_context(viewport={"width": viewport_width, "height": viewport_height})
                if self._browser
                else None
            )

        if not self._context:
            self._on_status("Failed to create browser context.")
            return

        self._page = self._context.new_page()
        self._page.on("domcontentloaded", lambda: self._on_dom_content_loaded())
        self._on_status(f"Launching managed Chromium: {url}")
        self._page.goto(url, wait_until="domcontentloaded")
        self._current_url = self._page.url
        self._last_synced_source_url = self._current_url
        self._last_synced_scroll = (0, 0)
        self._update_page_context(self._page)
        self._on_page_info(self._page.title(), self._page.url)
        self._on_status("Managed Chromium synced with embedded browser.")

    def _handle_navigate(self, raw_url: str) -> None:
        if not self._page:
            return
        url = self._normalize_url(raw_url)
        if not should_sync_navigation(self._page.url, url):
            return
        try:
            self._page.goto(url, wait_until="domcontentloaded")
            self._current_url = self._page.url
            self._last_synced_source_url = self._current_url
            self._last_synced_scroll = (0, 0)
            self._update_page_context(self._page)
            self._on_page_info(self._page.title(), self._page.url)
        except Exception as exc:
            self._on_status(f"Managed browser navigation sync failed: {exc}")

    def _handle_sync_viewport(self, viewport: tuple[int, int] | None) -> None:
        if not self._page or not viewport:
            return
        width, height = normalize_viewport_size(viewport[0], viewport[1])
        if self._launch_viewport == (width, height):
            return
        try:
            self._page.set_viewport_size({"width": width, "height": height})
            self._launch_viewport = (width, height)
        except Exception:
            return

    def _handle_inspect_mode(self, enabled: bool) -> None:
        self._inspect_enabled = enabled
        if not self._page:
            self._on_status("Launch a page first.")
            return
        state = "ON" if enabled else "OFF"
        self._on_status(f"Hybrid inspect mode {state}.")

    def _handle_capture_coordinates(self, payload: dict[str, Any]) -> None:
        if not self._page:
            self._on_status("Managed Chromium is not ready. Launch URL first.")
            return
        if not self._inspect_enabled:
            return
        if self._capture_busy:
            self.logger.info(
                "Capture skipped because busy: x=%s y=%s",
                payload.get("x"),
                payload.get("y"),
            )
            self._on_status("Capture skipped because previous capture is still running.")
            return

        self._capture_busy = True
        self.logger.info(
            "Capture started: x=%s y=%s viewport=%sx%s",
            payload.get("x"),
            payload.get("y"),
            payload.get("viewport_width"),
            payload.get("viewport_height"),
        )
        try:
            self._execute_capture_coordinates(payload)
        except Exception as exc:
            self.logger.exception("Capture failed unexpectedly", exc_info=exc)
            self._on_status(f"Capture failed unexpectedly: {exc}")
        finally:
            self._capture_busy = False
            self.logger.info("Capture ended.")

    def _handle_hover_coordinates(self, payload: dict[str, Any]) -> None:
        if not self._page or not self._inspect_enabled:
            return
        if self._hover_busy:
            return
        self._hover_busy = True
        self.logger.info(
            "Hover probe started: x=%s y=%s viewport=%sx%s",
            payload.get("x"),
            payload.get("y"),
            payload.get("viewport_width"),
            payload.get("viewport_height"),
        )
        try:
            self._sync_capture_source(payload)
            mapped_x, mapped_y, target_width, target_height = self._map_payload_coordinates_to_managed(payload)
            hover_payload = self._resolve_hover_probe_payload(mapped_x, mapped_y, target_width, target_height)
            if hover_payload and isinstance(hover_payload.get("raw"), dict):
                raw_box = hover_payload.get("raw", {})
                source_dpr = self._to_float(payload.get("device_pixel_ratio"), default=1.0)
                self.logger.info(
                    "Hover rect received: x=%s y=%s dpr=%.2f rawTag=%s rawId=%s rawClasses=%s",
                    mapped_x,
                    mapped_y,
                    source_dpr,
                    raw_box.get("tag", "-"),
                    raw_box.get("id", "-"),
                    raw_box.get("class_name", "-"),
                )
                self._on_hover_box(hover_payload)
            else:
                self._on_hover_box(None)
        except Exception as exc:
            self.logger.exception("Hover probe failed", exc_info=exc)
            self._on_hover_box(None)
        finally:
            self.logger.info("Hover probe ended.")
            self._hover_busy = False

    def _execute_capture_coordinates(self, payload: dict[str, Any]) -> None:
        self._sync_capture_source(payload)
        mapped_x, mapped_y, _, _ = self._map_payload_coordinates_to_managed(payload)

        probe = self._probe_point(mapped_x, mapped_y)
        if probe.status != "ok":
            if probe.cross_origin_iframe:
                self._on_status(
                    "Cross-origin iframe detected at clicked point. Open in Managed Chromium Inspector for fallback."
                )
            else:
                self._on_status("No element found at clicked point. Try again or open Managed Chromium Inspector.")
            return

        element, refinement = self._resolve_element_from_point(mapped_x, mapped_y)
        if refinement:
            raw = refinement.get("raw") or {}
            selected = refinement.get("selected") or {}
            raw_label = self._compact_target_label(raw)
            selected_label = self._compact_target_label(selected)
            self.logger.info(
                "Target refine raw=%s refined=%s reason=%s",
                raw_label,
                selected_label,
                refinement.get("reason", "-"),
            )
        if not element:
            self._on_status("Coordinate capture could not resolve element handle.")
            return

        summary = extract_element_summary(element)
        if refinement:
            summary.raw_target = self._to_summary_target(refinement.get("raw"))
            summary.refined_target = self._to_summary_target(refinement.get("selected"))
        weights = self.learning_store.get_rule_weights()
        candidates = generate_locator_candidates(self._page, element, summary, learning_weights=weights, limit=15)
        page_context = self._build_page_context(self._page)
        override = self.learning_store.get_override(page_context.hostname, summary.signature())
        if override and not is_obvious_root_container_locator(override.locator):
            override_uniqueness = self._count_override_uniqueness(override.locator_type, override.locator)
            override_candidate = build_override_candidate(
                override,
                uniqueness_count=override_uniqueness,
                learning_weights=weights,
            )
            candidates = inject_override_candidate(candidates, override_candidate, limit=15)

        candidates = self._validate_candidates_for_display(candidates)
        self._log_candidate_type_breakdown(candidates)

        with self._state_lock:
            self._last_summary = summary
        self._update_page_context(self._page)
        self._on_capture(summary, candidates)

    def _map_payload_coordinates_to_managed(self, payload: dict[str, Any]) -> tuple[int, int, int, int]:
        source_width = payload.get("viewport_width", self._launch_viewport[0])
        source_height = payload.get("viewport_height", self._launch_viewport[1])
        normalized_source_width, normalized_source_height = normalize_viewport_size(source_width, source_height)
        if (normalized_source_width, normalized_source_height) != self._launch_viewport:
            self._handle_sync_viewport((normalized_source_width, normalized_source_height))
            source_width = normalized_source_width
            source_height = normalized_source_height

        coordinates = normalize_capture_coordinates(
            payload.get("x", 0),
            payload.get("y", 0),
            int(source_width),
            int(source_height),
        )
        target_width, target_height = self._read_managed_viewport()
        mapped_x, mapped_y = map_coordinates_to_viewport(coordinates, target_width, target_height)
        source_dpr = self._to_float(payload.get("device_pixel_ratio"), default=1.0)
        target_dpr = self._read_managed_device_pixel_ratio()
        if source_dpr > 0 and target_dpr > 0 and abs(source_dpr - target_dpr) > 0.05:
            ratio = source_dpr / target_dpr
            mapped_x = int(round(mapped_x * ratio))
            mapped_y = int(round(mapped_y * ratio))
            mapped_x = max(0, min(mapped_x, target_width - 1))
            mapped_y = max(0, min(mapped_y, target_height - 1))
        return mapped_x, mapped_y, target_width, target_height

    def _resolve_hover_probe_payload(
        self,
        x: int,
        y: int,
        target_width: int,
        target_height: int,
    ) -> dict[str, Any] | None:
        if not self._page:
            return None
        nodes = self._collect_refinement_nodes(x, y)
        if not nodes:
            return None
        choice = select_raw_and_refined_indices(nodes)
        raw_node = next((node for node in nodes if self._to_int(node.get("index"), -1) == choice.raw_index), nodes[0])
        refined_node = next(
            (node for node in nodes if self._to_int(node.get("index"), -1) == choice.refined_index),
            raw_node,
        )
        raw_box = map_hover_box_to_overlay(raw_node, target_width, target_height)
        if not raw_box:
            return None
        refined_box = map_hover_box_to_overlay(refined_node, target_width, target_height)
        payload: dict[str, Any] = {
            "raw": {
                "left": raw_box.left,
                "top": raw_box.top,
                "width": raw_box.width,
                "height": raw_box.height,
                "tag": raw_box.tag,
                "id": raw_box.element_id,
                "class_name": raw_box.class_name,
                "text": raw_box.text,
            },
            "refined": None,
            "refined_reason": choice.refined_reason,
            "refined_score": choice.refined_score,
        }
        if refined_box:
            payload["refined"] = {
                "left": refined_box.left,
                "top": refined_box.top,
                "width": refined_box.width,
                "height": refined_box.height,
                "tag": refined_box.tag,
                "id": refined_box.element_id,
                "class_name": refined_box.class_name,
                "text": refined_box.text,
            }
        return payload

    def _validate_candidates_for_display(self, candidates: list[LocatorCandidate]) -> list[LocatorCandidate]:
        validated: list[LocatorCandidate] = []
        for candidate in candidates:
            is_valid, reason = self._is_candidate_valid_for_default(candidate)
            metadata = dict(candidate.metadata)
            metadata["display_valid"] = is_valid
            if reason:
                metadata["display_validation_reason"] = reason
            validated.append(replace(candidate, metadata=metadata))
        return validated

    def _is_candidate_valid_for_default(self, candidate: LocatorCandidate) -> tuple[bool, str]:
        if candidate.uniqueness_count != 1:
            if (
                candidate.rule == "xpath_text_clickable_union"
                and bool(candidate.metadata.get("uses_index"))
                and candidate.uniqueness_count > 1
            ):
                return True, ""
            return False, "not-unique"
        if not self._page:
            return True, ""
        try:
            visible = self._is_candidate_visible(candidate)
        except Exception:
            visible = True
        if not visible and candidate.locator_type in {"CSS", "XPath"}:
            return False, "not-visible"
        return True, ""

    def _is_candidate_visible(self, candidate: LocatorCandidate) -> bool:
        if not self._page:
            return True
        if candidate.locator_type == "CSS":
            locator = self._page.locator(candidate.locator).first
            return bool(locator.is_visible(timeout=200))
        if candidate.locator_type == "XPath":
            locator = self._page.locator(f"xpath={candidate.locator}").first
            return bool(locator.is_visible(timeout=200))
        if candidate.locator_type == "Selenium":
            selector_kind = str(candidate.metadata.get("selector_kind", "") or "")
            selector_value = str(candidate.metadata.get("selector_value", "") or "")
            if selector_kind == "id" and selector_value:
                locator = self._page.locator(f'[id="{selector_value}"]').first
                return bool(locator.is_visible(timeout=200))
            if selector_kind == "name" and selector_value:
                locator = self._page.locator(f'[name="{selector_value}"]').first
                return bool(locator.is_visible(timeout=200))
            if selector_kind == "xpath" and selector_value:
                locator = self._page.locator(f"xpath={selector_value}").first
                return bool(locator.is_visible(timeout=200))
            if selector_kind == "css" and selector_value:
                locator = self._page.locator(selector_value).first
                return bool(locator.is_visible(timeout=200))
        return True

    def _log_candidate_type_breakdown(self, candidates: list[LocatorCandidate]) -> None:
        counts = {"ID": 0, "NAME": 0, "CSS": 0, "XPath": 0, "Playwright": 0}
        for candidate in candidates:
            if candidate.locator_type == "Selenium" and candidate.locator.startswith("By.ID("):
                counts["ID"] += 1
            elif candidate.locator_type == "Selenium" and candidate.locator.startswith("By.NAME("):
                counts["NAME"] += 1
            elif candidate.locator_type == "CSS":
                counts["CSS"] += 1
            elif candidate.locator_type == "XPath":
                counts["XPath"] += 1
            elif candidate.locator_type == "Playwright":
                counts["Playwright"] += 1
        self.logger.info(
            "Candidate breakdown: ID=%s NAME=%s CSS=%s XPath=%s Playwright=%s total=%s",
            counts["ID"],
            counts["NAME"],
            counts["CSS"],
            counts["XPath"],
            counts["Playwright"],
            len(candidates),
        )

    def _sync_capture_source(self, payload: dict[str, Any]) -> None:
        if not self._page:
            return
        source_url = str(payload.get("source_url", "") or "").strip()
        if source_url and source_url != self._last_synced_source_url and should_sync_navigation(self._page.url, source_url):
            try:
                self._page.goto(source_url, wait_until="domcontentloaded")
                self._current_url = self._page.url
                self._update_page_context(self._page)
                self._on_page_info(self._page.title(), self._page.url)
                self._last_synced_source_url = source_url
            except Exception as exc:
                self._on_status(f"Managed browser navigation sync failed: {exc}")
        elif source_url:
            self._last_synced_source_url = source_url

        try:
            scroll_x = int(payload.get("scroll_x", 0) or 0)
            scroll_y = int(payload.get("scroll_y", 0) or 0)
        except (TypeError, ValueError):
            scroll_x = 0
            scroll_y = 0
        if scroll_x < 0:
            scroll_x = 0
        if scroll_y < 0:
            scroll_y = 0
        if self._last_synced_scroll == (scroll_x, scroll_y):
            return
        try:
            self._page.evaluate(
                "({x, y}) => { window.scrollTo(x, y); return { sx: window.scrollX, sy: window.scrollY }; }",
                {"x": scroll_x, "y": scroll_y},
            )
            self._last_synced_scroll = (scroll_x, scroll_y)
        except Exception:
            # Scroll sync is best-effort; capture should still proceed.
            pass

    def _handle_open_managed_inspector(self) -> None:
        if not self._playwright:
            self._on_status("Playwright is not available.")
            return

        debug_url = self._page.url if self._page else self._current_url
        if not debug_url:
            self._on_status("Launch a page before opening Managed Chromium Inspector.")
            return

        self._close_debug_session()
        try:
            self._debug_browser = self._playwright.chromium.launch(headless=False)
            self._debug_context = self._debug_browser.new_context(viewport=None) if self._debug_browser else None
            self._debug_page = self._debug_context.new_page() if self._debug_context else None
            if self._debug_page:
                self._debug_page.goto(debug_url, wait_until="domcontentloaded")
            self._on_status("Managed Chromium Inspector opened.")
        except Exception as exc:
            self._on_status(f"Could not open Managed Chromium Inspector: {exc}")

    def _probe_point(self, x: int, y: int):
        if not self._page:
            return classify_probe_payload(None)
        raw = self._page.evaluate(
            """
            ({x, y}) => {
              const el = document.elementFromPoint(x, y);
              if (!el) {
                return { status: "none" };
              }
              const tag = (el.tagName || "").toLowerCase();
              if (tag === "iframe") {
                let crossOriginIframe = false;
                try {
                  const doc = el.contentDocument;
                  crossOriginIframe = !doc;
                } catch (_error) {
                  crossOriginIframe = true;
                }
                return {
                  status: crossOriginIframe ? "cross_origin_iframe" : "iframe",
                  tag,
                  cross_origin_iframe: crossOriginIframe,
                };
              }
              return { status: "ok", tag };
            }
            """,
            {"x": x, "y": y},
        )
        return classify_probe_payload(raw if isinstance(raw, dict) else None)

    def _resolve_element_from_point(self, x: int, y: int) -> tuple[ElementHandle | None, dict[str, Any] | None]:
        if not self._page:
            return None, None
        nodes = self._collect_refinement_nodes(x, y)
        choice = select_raw_and_refined_indices(nodes)
        raw = next((node for node in nodes if self._to_int(node.get("index"), -1) == choice.raw_index), {})
        selected = next((node for node in nodes if self._to_int(node.get("index"), -1) == choice.refined_index), raw)
        handle = self._page.evaluate_handle(
            """
            ({x, y, preferredIndex}) => {
              const clickableSelector =
                "a,button,input,label,summary,[role='button'],[role='link'],[role='menuitem'],[onclick],[tabindex]";
              const wrapperPattern = /(header|container|modal|content|wrapper|shell|panel|overlay)/i;
              const blockerPattern = /(cookie|consent|gdpr|onetrust|evidon|epaas)/i;
              const isVisible = (el) => {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                if (Number.parseFloat(style.opacity || "1") === 0) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 1 && rect.height > 1;
              };
              const isAriaHidden = (el) => {
                let current = el;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                  if (current.getAttribute && current.getAttribute("aria-hidden") === "true") return true;
                  current = current.parentElement;
                }
                return false;
              };
              const isActionable = (el) => {
                if (!el || typeof el.matches !== "function") return false;
                return el.matches(clickableSelector);
              };
              const hasStrongAttrs = (el) =>
                Boolean(
                  el.id ||
                    el.getAttribute("name") ||
                    el.getAttribute("data-testid") ||
                    el.getAttribute("data-test") ||
                    el.getAttribute("data-qa") ||
                    el.getAttribute("data-cy") ||
                    el.getAttribute("aria-label")
                );
              const textValue = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ") : "");
              const elements = typeof document.elementsFromPoint === "function"
                ? document.elementsFromPoint(x, y)
                : [document.elementFromPoint(x, y)].filter(Boolean);
              if (!elements.length) return null;

              let chosen = elements[Math.max(0, Math.min(preferredIndex, elements.length - 1))] || elements[0];
              if (chosen && !isActionable(chosen) && chosen.closest) {
                const ancestor = chosen.closest(clickableSelector);
                if (ancestor) chosen = ancestor;
              }

              const marker = `${chosen.id || ""} ${typeof chosen.className === "string" ? chosen.className : ""}`.toLowerCase();
              const wrapperLike = wrapperPattern.test(marker) || blockerPattern.test(marker);
              if (wrapperLike) {
                const descendants = Array.from(chosen.querySelectorAll(clickableSelector)).slice(0, 100);
                let best = null;
                let bestScore = -1e9;
                for (const node of descendants) {
                  if (!isVisible(node) || isAriaHidden(node)) continue;
                  const rect = node.getBoundingClientRect();
                  const contains = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
                  let score = contains ? 80 : 0;
                  const t = textValue(node);
                  if (t.length >= 2) score += 26;
                  if (hasStrongAttrs(node)) score += 42;
                  if (isActionable(node)) score += 40;
                  const cx = (rect.left + rect.right) / 2;
                  const cy = (rect.top + rect.bottom) / 2;
                  const dist = Math.hypot(cx - x, cy - y);
                  score -= Math.min(36, dist / 18);
                  if (score > bestScore) {
                    best = node;
                    bestScore = score;
                  }
                }
                if (best) chosen = best;
              }

              if (chosen && !isVisible(chosen)) {
                const visibleFallback = elements.find((node) => node && isVisible(node) && !isAriaHidden(node));
                if (visibleFallback) chosen = visibleFallback;
              }
              if (chosen && isAriaHidden(chosen)) {
                const visibleFallback = elements.find((node) => node && isVisible(node) && !isAriaHidden(node));
                if (visibleFallback) chosen = visibleFallback;
              }
              if (chosen && !isActionable(chosen) && chosen.closest) {
                const ancestor = chosen.closest(clickableSelector);
                if (ancestor && isVisible(ancestor) && !isAriaHidden(ancestor)) chosen = ancestor;
              }
              return chosen;
            }
            """,
            {"x": x, "y": y, "preferredIndex": choice.refined_index},
        )
        element = handle.as_element()
        if element is None:
            try:
                handle.dispose()
            except Exception:
                pass
            return None, {
                "raw": raw,
                "selected": selected,
                "reason": choice.refined_reason,
                "score": choice.refined_score,
            }
        return element, {
            "raw": raw,
            "selected": selected,
            "reason": choice.refined_reason,
            "score": choice.refined_score,
        }

    def _collect_refinement_nodes(self, x: int, y: int) -> list[dict[str, Any]]:
        if not self._page:
            return []
        raw = self._page.evaluate(
            """
            ({x, y}) => {
              const clickableSelector =
                "a,button,input,label,summary,[role='button'],[role='link'],[role='menuitem'],[onclick],[tabindex]";
              const wrapperPattern = /(header|container|modal|content|wrapper|shell|panel|overlay)/i;
              const isVisible = (el) => {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                if (Number.parseFloat(style.opacity || "1") === 0) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 1 && rect.height > 1;
              };
              const isAriaHidden = (el) => {
                let current = el;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                  if (current.getAttribute && current.getAttribute("aria-hidden") === "true") return true;
                  current = current.parentElement;
                }
                return false;
              };
              const textValue = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ") : "");
              const elements = typeof document.elementsFromPoint === "function"
                ? document.elementsFromPoint(x, y)
                : [document.elementFromPoint(x, y)].filter(Boolean);
              return elements.slice(0, 20).map((el, index) => {
                if (!el) return null;
                const className = typeof el.className === "string" ? el.className : "";
                const role = el.getAttribute("role") || "";
                const rect = el.getBoundingClientRect();
                const contains = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
                const clickableAncestor = el.closest ? el.closest(clickableSelector) : null;
                return {
                  index,
                  tag: (el.tagName || "").toLowerCase(),
                  id: el.id || "",
                  class_name: className,
                  role,
                  name: el.getAttribute("name") || "",
                  aria_label: el.getAttribute("aria-label") || "",
                  data_testid: el.getAttribute("data-testid") || "",
                  data_test: el.getAttribute("data-test") || "",
                  data_qa: el.getAttribute("data-qa") || "",
                  data_cy: el.getAttribute("data-cy") || "",
                  text: textValue(el).slice(0, 160),
                  left: rect.left,
                  top: rect.top,
                  width: rect.width,
                  height: rect.height,
                  visible: isVisible(el),
                  aria_hidden: isAriaHidden(el),
                  actionable: typeof el.matches === "function" ? el.matches(clickableSelector) : false,
                  has_onclick: Boolean(el.getAttribute("onclick")),
                  tab_index: Number.isFinite(el.tabIndex) ? el.tabIndex : -1,
                  rect_contains: contains,
                  strong_attrs: Boolean(
                    el.id ||
                      el.getAttribute("name") ||
                      el.getAttribute("data-testid") ||
                      el.getAttribute("data-test") ||
                      el.getAttribute("data-qa") ||
                      el.getAttribute("data-cy") ||
                      el.getAttribute("aria-label")
                  ),
                  generic_wrapper: wrapperPattern.test(`${el.id || ""} ${className}`.toLowerCase()),
                  clickable_ancestor: clickableAncestor ? (clickableAncestor.tagName || "").toLowerCase() : "",
                };
              }).filter(Boolean);
            }
            """,
            {"x": x, "y": y},
        )
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _read_managed_viewport(self) -> tuple[int, int]:
        if not self._page:
            return self._launch_viewport
        try:
            viewport = self._page.evaluate(
                "() => ({ width: window.innerWidth || 1280, height: window.innerHeight || 720 })"
            )
            if isinstance(viewport, dict):
                return normalize_viewport_size(viewport.get("width"), viewport.get("height"))
        except Exception:
            pass
        return self._launch_viewport

    def _read_managed_device_pixel_ratio(self) -> float:
        if not self._page:
            return 1.0
        try:
            value = self._page.evaluate("() => window.devicePixelRatio || 1")
            return self._to_float(value, default=1.0)
        except Exception:
            return 1.0

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_summary_target(raw: Any) -> dict[str, str] | None:
        if not isinstance(raw, dict):
            return None
        tag = str(raw.get("tag", "") or "").strip().lower()
        element_id = str(raw.get("id", "") or "").strip()
        class_name = str(raw.get("class_name", "") or "").strip()
        text = str(raw.get("text", "") or "").strip()
        if not any((tag, element_id, class_name, text)):
            return None
        return {
            "tag": tag,
            "id": element_id,
            "class_name": class_name,
            "text": text,
        }

    @staticmethod
    def _compact_target_label(raw: Any) -> str:
        target = BrowserManager._to_summary_target(raw)
        if not target:
            return "<none>"
        tag = target.get("tag", "").strip() or "?"
        element_id = target.get("id", "").strip()
        class_name = target.get("class_name", "").strip()
        first_class = class_name.split()[0] if class_name else ""
        suffix = ""
        if element_id:
            suffix += f"#{element_id}"
        if first_class:
            suffix += f".{first_class}"
        return f"<{tag}{suffix}>"

    def _on_dom_content_loaded(self) -> None:
        if not self._page:
            return
        try:
            self._current_url = self._page.url
            self._update_page_context(self._page)
            self._on_page_info(self._page.title(), self._page.url)
        except Exception as exc:
            self._on_status(f"Managed page sync failed: {exc}")

    def _update_page_context(self, page: Page) -> None:
        context = self._build_page_context(page)
        with self._state_lock:
            self._page_context = context

    @staticmethod
    def _build_page_context(page: Page) -> PageContext:
        parsed = urlparse(page.url)
        return PageContext(url=page.url, hostname=parsed.hostname or "", page_title=page.title())

    def _count_override_uniqueness(self, locator_type: str, locator: str) -> int:
        if not self._page:
            return 0
        try:
            if locator_type == "CSS":
                return len(self._page.query_selector_all(locator))
            if locator_type == "XPath":
                return self._page.locator(f"xpath={locator}").count()
            if locator_type == "Selenium":
                lowered = locator.strip()
                css_match = None
                xpath_match = None
                import re

                css_match = re.match(r'By\\.CSS_SELECTOR\\([\"\\\'](.+)[\"\\\']\\)', lowered)
                xpath_match = re.match(r'By\\.XPATH\\([\"\\\'](.+)[\"\\\']\\)', lowered)
                if css_match:
                    return len(self._page.query_selector_all(css_match.group(1)))
                if xpath_match:
                    return self._page.locator(f"xpath={xpath_match.group(1)}").count()
                return 1
            return 1
        except Exception:
            return 0

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
        self._last_synced_source_url = ""
        self._last_synced_scroll = (0, 0)

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None

    def _close_debug_session(self) -> None:
        if self._debug_page:
            try:
                self._debug_page.close()
            except Exception:
                pass
        self._debug_page = None
        if self._debug_context:
            try:
                self._debug_context.close()
            except Exception:
                pass
        self._debug_context = None
        if self._debug_browser:
            try:
                self._debug_browser.close()
            except Exception:
                pass
        self._debug_browser = None

    def _cleanup(self) -> None:
        self._close_debug_session()
        self._close_page_and_context()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    def _ensure_browser(self) -> bool:
        if self._browser and self._is_browser_connected():
            return True
        self._browser = None
        try:
            self._browser = self._playwright.chromium.launch(headless=True) if self._playwright else None
            return self._browser is not None
        except Exception as exc:
            if _is_missing_browser_error(exc):
                self._on_status("Chromium not installed. Run: python -m playwright install chromium")
                return False
            self._on_status(f"Failed to launch managed Chromium: {exc}")
            return False

    def _is_browser_connected(self) -> bool:
        if not self._browser:
            return False
        try:
            return bool(self._browser.is_connected())
        except Exception:
            return False

    @staticmethod
    def _is_closed_target_error(exc: Exception) -> bool:
        message = str(exc).lower()
        hints = (
            "has been closed",
            "target page, context or browser has been closed",
            "browser has been closed",
            "target closed",
        )
        return any(hint in message for hint in hints)

    @staticmethod
    def _normalize_url(raw_url: str) -> str:
        if not raw_url:
            return ""
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        return f"https://{raw_url}"
