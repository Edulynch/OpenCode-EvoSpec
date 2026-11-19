"""Regression tests for the identity of the executable used by R3-B tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evospec.services import ServiceError, _runtime_digest


class R3BRuntimeDigestTests(unittest.TestCase):
    # Every test receives its own disposable executable path in setUp.
    executable: Path = Path()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="evospec-r3b-runtime-")
        self.addCleanup(temporary.cleanup)
        self.executable = Path(temporary.name) / "test-runtime.exe"
        self.executable.write_bytes(b"trusted local executable bytes")
        # Unix requires an executable bit; the identity check does not run the file.
        self.executable.chmod(0o700)

    def _digest_with_windows_stats(
        self,
        *,
        opened_changes: dict[str, int] | None = None,
        after_changes: dict[str, int] | None = None,
        final_changes: dict[str, int] | None = None,
        windows: bool = True,
    ) -> str:
        """Model the different Windows path/handle ctime interpretations."""
        original_lstat = Path.lstat
        original_fstat = os.fstat
        path = self.executable
        fstat_calls = 0

        def wrapped(info: os.stat_result, *, path_stat: bool, changes: dict[str, int] | None = None) -> SimpleNamespace:
            fields = (
                "st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
                "st_mtime_ns", "st_ctime_ns", "st_file_attributes",
            )
            values = {name: getattr(info, name, 0) for name in fields}
            # Windows 3.13 exposes creation time consistently as birthtime,
            # even when path and descriptor ctime represent different concepts.
            values["st_birthtime_ns"] = 123456789
            values["st_ctime_ns"] = 100 if path_stat else 200
            values.update(changes or {})
            return SimpleNamespace(**values)

        def lstat(candidate: Path) -> os.stat_result | SimpleNamespace:
            info = original_lstat(candidate)
            return wrapped(info, path_stat=True, changes=final_changes) if candidate == path else info

        def fstat(descriptor: int) -> SimpleNamespace:
            nonlocal fstat_calls
            fstat_calls += 1
            changes = after_changes if fstat_calls == 2 else opened_changes
            return wrapped(original_fstat(descriptor), path_stat=False, changes=changes)

        # This isolated fixture reproduces the exact st_ctime_ns disagreement
        # without modifying a real Python runtime or a native qualification root.
        with (
            patch("evospec.services._WINDOWS_STAT_CTIME_DIFFERENCE", windows),
            patch("pathlib.Path.lstat", side_effect=lstat, autospec=True),
            patch("evospec.services.os.fstat", side_effect=fstat),
        ):
            return _runtime_digest(str(path.resolve()))

    def test_windows_path_and_handle_ctime_difference_does_not_change_runtime(self) -> None:
        self.assertEqual(
            self._digest_with_windows_stats(),
            sha256(self.executable.read_bytes()).hexdigest(),
        )

    def test_real_runtime_size_drift_still_fails_closed(self) -> None:
        changed_size = self.executable.stat().st_size + 1
        with self.assertRaisesRegex(ServiceError, "runtime executable changed"):
            self._digest_with_windows_stats(opened_changes={"st_size": changed_size})

    def test_changed_runtime_birthtime_still_fails_closed(self) -> None:
        with self.assertRaisesRegex(ServiceError, "runtime executable changed"):
            self._digest_with_windows_stats(final_changes={"st_birthtime_ns": 999})

    def test_runtime_metadata_change_while_handle_is_open_fails_closed(self) -> None:
        # Two handle reads must agree even when Windows path/handle ctime differs.
        with self.assertRaisesRegex(ServiceError, "runtime executable changed"):
            self._digest_with_windows_stats(after_changes={"st_ctime_ns": 300})

    def test_non_windows_path_and_handle_ctime_difference_fails_closed(self) -> None:
        # The Windows exception must not relax identity checks on Unix.
        with self.assertRaisesRegex(ServiceError, "runtime executable changed"):
            self._digest_with_windows_stats(windows=False)


if __name__ == "__main__":
    unittest.main()
