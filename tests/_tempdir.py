from __future__ import annotations

import os
import tempfile
import time
import weakref
from operator import attrgetter
from types import TracebackType
from typing import Generic, Literal, TypeVar

T = TypeVar("T", bound=str)
IS_WINDOWS = os.name == "nt"
_sleep = time.sleep
_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _cleanup_temporary(temporary: tempfile.TemporaryDirectory[str]) -> None:
    for delay in (None, *_RETRY_DELAYS):
        if delay is not None:
            _sleep(delay)
        try:
            temporary.cleanup()
        except OSError as error:
            if not (IS_WINDOWS and getattr(error, "winerror", None) == 145):
                raise
            if delay == _RETRY_DELAYS[-1]:
                raise
        else:
            return


class TemporaryDirectory(Generic[T]):
    def __init__(
        self,
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | os.PathLike[str] | None = None,
        *,
        ignore_cleanup_errors: bool = False,
    ) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
        )
        # tempfile exposes no public way to replace its automatic cleanup callback.
        attrgetter("_finalizer")(self._temporary).detach()
        self._finalizer = weakref.finalize(self, _cleanup_temporary, self._temporary)

    def __enter__(self) -> str:
        return self.name

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            self.cleanup()
        except OSError:
            if exception is None:
                raise
        return False

    @property
    def name(self) -> str:
        return self._temporary.name

    def cleanup(self) -> None:
        self._finalizer.detach()
        _cleanup_temporary(self._temporary)
