"""Read-only persistence capability exposed to callers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .domain import State


@runtime_checkable
class StatePersistence(Protocol):
    """Recover authoritative state without granting write authority."""

    def load_state(self) -> State: ...
