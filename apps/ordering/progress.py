"""The checkpoints of one attempt to place an order, shown to the host as a checklist.

Each step has a label, the reason it exists, a status (pending, running, done, failed)
and a detail (what happened, or why it failed). The list is saved after every change so
the host can watch it, and stays on the order afterwards to explain a failure.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from apps.ordering.providers.base import HostActionRequired

logger = logging.getLogger(__name__)


class Progress:
    def __init__(self, save: Callable[[list[dict]], None] | None = None):
        self.steps: list[dict] = []
        self._save = save

    def plan(self, steps: list[tuple[str, str, str]]) -> None:
        """Announce every step up front, so the host sees what is coming."""
        self.steps = [
            {"key": key, "label": label, "why": why, "status": "pending", "detail": ""}
            for key, label, why in steps
        ]
        self._flush()

    def start(self, key: str) -> None:
        self._set(key, "running")

    def done(self, key: str, detail: str = "") -> None:
        self._set(key, "done", detail)

    def fail(self, key: str, detail: str) -> None:
        self._set(key, "failed", detail)

    @contextmanager
    def step(self, key: str) -> Iterator[None]:
        """Run a step: running now, done when it ends, failed (with the reason) if it
        raises. The error still propagates."""
        self.start(key)
        try:
            yield
        except HostActionRequired as problem:
            self.fail(key, str(problem))
            raise
        except Exception as error:
            first_line = (str(error).strip().splitlines() or [""])[0]
            self.fail(key, f"{type(error).__name__}: {first_line}"[:300])
            raise
        if self._find(key)["status"] == "running":
            self.done(key)

    def _find(self, key: str) -> dict:
        return next(step for step in self.steps if step["key"] == key)

    def _set(self, key: str, status: str, detail: str = "") -> None:
        step = self._find(key)
        step["status"], step["detail"] = status, detail
        self._flush()

    def _flush(self) -> None:
        if not self._save:
            return
        try:
            self._save(self.steps)
        except Exception:
            # The checklist is only a display: failing to save it must never hide what
            # the bot was actually doing or why it failed.
            logger.exception("Couldn't save the order bot's steps")
