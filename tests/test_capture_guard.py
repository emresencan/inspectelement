import pytest

from inspectelement.capture_guard import CaptureGuard


def test_capture_guard_resets_busy_after_exception() -> None:
    guard = CaptureGuard()
    assert guard.begin() is True

    with pytest.raises(RuntimeError):
        guard.run_and_finish(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert guard.busy is False


def test_capture_guard_allows_multiple_sequential_clicks() -> None:
    class MockBrowserManager:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        def capture(self, x: int, y: int) -> None:
            self.calls.append((x, y))

    guard = CaptureGuard()
    manager = MockBrowserManager()
    clicks = [(10, 20), (30, 40), (50, 60)]

    for x, y in clicks:
        assert guard.begin() is True
        guard.run_and_finish(lambda x=x, y=y: manager.capture(x, y))

    assert manager.calls == clicks
    assert guard.busy is False
