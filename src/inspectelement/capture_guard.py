from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class CaptureGuard:
    busy: bool = False

    def begin(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def finish(self) -> None:
        self.busy = False

    def run_and_finish(self, callback: Callable[[], T]) -> T:
        try:
            return callback()
        finally:
            self.busy = False
