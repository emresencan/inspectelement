from __future__ import annotations

_MISSING_BROWSER_ERROR_HINTS = (
    "executable doesn't exist",
    "executable does not exist",
    "download new browsers",
    "playwright install",
    "could not find browser",
    "failed to launch chromium because executable",
)


def _is_missing_browser_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _MISSING_BROWSER_ERROR_HINTS)
