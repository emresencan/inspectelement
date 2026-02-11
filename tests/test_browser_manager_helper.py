from inspectelement.runtime_checks import _is_missing_browser_error


def test_is_missing_browser_error_matches_common_messages() -> None:
    errors = [
        RuntimeError("Executable doesn't exist at /path/to/chromium/chrome"),
        RuntimeError(
            "Please run the following command to download new browsers: playwright install"
        ),
        RuntimeError("Failed to launch chromium because executable does not exist"),
    ]

    for error in errors:
        assert _is_missing_browser_error(error)


def test_is_missing_browser_error_ignores_unrelated_errors() -> None:
    error = RuntimeError("net::ERR_NAME_NOT_RESOLVED")
    assert not _is_missing_browser_error(error)
