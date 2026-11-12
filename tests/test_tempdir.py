from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from tests import _tempdir


class TemporaryDirectoryTests(unittest.TestCase):
    def test_cleanup_calls_wrapped_directory_once_when_successful(self) -> None:
        # Given: a wrapped public temporary directory
        directory = Mock()

        # When: cleanup succeeds
        with patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory):
            _tempdir.TemporaryDirectory().cleanup()

        # Then: cleanup is delegated exactly once
        directory.cleanup.assert_called_once_with()

    def test_cleanup_retries_transient_windows_145(self) -> None:
        # Given: Windows cleanup that fails once with WinError 145
        directory = Mock()
        error = OSError()
        error.winerror = 145
        directory.cleanup.side_effect = (error, None)
        sleep = Mock()

        # When: cleanup recovers on the first retry
        with (
            patch("tests._tempdir.IS_WINDOWS", True),
            patch("tests._tempdir._sleep", sleep),
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
        ):
            _tempdir.TemporaryDirectory().cleanup()

        # Then: it retries after the first bounded delay
        self.assertEqual(directory.cleanup.call_count, 2)
        sleep.assert_called_once_with(0.05)

    def test_finalizer_retries_transient_windows_145(self) -> None:
        # Given: automatic Windows cleanup that fails once with WinError 145
        directory = Mock()
        error = OSError()
        error.winerror = 145
        directory.cleanup.side_effect = (error, None)
        sleep = Mock()

        # When: the wrapper-owned finalizer runs
        with (
            patch("tests._tempdir.IS_WINDOWS", True),
            patch("tests._tempdir._sleep", sleep),
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
        ):
            temporary = _tempdir.TemporaryDirectory()
            temporary._finalizer()

        # Then: it uses the explicit cleanup retry policy without sleeping
        self.assertEqual(directory.cleanup.call_count, 2)
        sleep.assert_called_once_with(0.05)
        directory._finalizer.detach.assert_called_once_with()

    def test_explicit_cleanup_disables_automatic_cleanup(self) -> None:
        # Given: a wrapped directory with its automatic cleanup still pending
        directory = Mock()

        # When: explicit cleanup completes before the finalizer is invoked
        with patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory):
            temporary = _tempdir.TemporaryDirectory()
            temporary.cleanup()
            temporary._finalizer()

        # Then: cleanup occurs exactly once
        directory.cleanup.assert_called_once_with()

    def test_context_exit_disables_automatic_cleanup(self) -> None:
        # Given: a wrapped directory used as a context manager
        directory = Mock()

        # When: the context exits before the finalizer is invoked
        with patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory):
            temporary = _tempdir.TemporaryDirectory()
            with temporary:
                pass
            temporary._finalizer()

        # Then: context cleanup occurs exactly once
        directory.cleanup.assert_called_once_with()

    def test_cleanup_propagates_permanent_windows_145_after_exact_retries(self) -> None:
        # Given: Windows cleanup that always fails with WinError 145
        directory = Mock()
        error = OSError()
        error.winerror = 145
        directory.cleanup.side_effect = error
        sleep = Mock()

        # When: cleanup exhausts the bounded retry policy
        with (
            patch("tests._tempdir.IS_WINDOWS", True),
            patch("tests._tempdir._sleep", sleep),
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
            self.assertRaises(OSError) as raised,
        ):
            _tempdir.TemporaryDirectory().cleanup()

        # Then: the final original failure propagates after exactly three delays
        self.assertIs(raised.exception, error)
        self.assertEqual(directory.cleanup.call_count, 4)
        self.assertEqual(sleep.call_args_list, [((0.05,), {}), ((0.1,), {}), ((0.2,), {})])

    def test_cleanup_propagates_non_145_without_retry(self) -> None:
        # Given: Windows cleanup that fails with a different error
        directory = Mock()
        error = OSError()
        error.winerror = 32
        directory.cleanup.side_effect = error
        sleep = Mock()

        # When: cleanup fails
        with (
            patch("tests._tempdir.IS_WINDOWS", True),
            patch("tests._tempdir._sleep", sleep),
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
            self.assertRaises(OSError) as raised,
        ):
            _tempdir.TemporaryDirectory().cleanup()

        # Then: it propagates without retrying
        self.assertIs(raised.exception, error)
        directory.cleanup.assert_called_once_with()
        sleep.assert_not_called()

    def test_cleanup_propagates_windows_145_without_retry_on_non_windows(self) -> None:
        # Given: non-Windows cleanup that fails with WinError 145
        directory = Mock()
        error = OSError()
        error.winerror = 145
        directory.cleanup.side_effect = error
        sleep = Mock()

        # When: cleanup fails
        with (
            patch("tests._tempdir.IS_WINDOWS", False),
            patch("tests._tempdir._sleep", sleep),
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
            self.assertRaises(OSError) as raised,
        ):
            _tempdir.TemporaryDirectory().cleanup()

        # Then: no Windows-specific policy runs
        self.assertIs(raised.exception, error)
        directory.cleanup.assert_called_once_with()
        sleep.assert_not_called()

    def test_context_body_exception_persists_after_successful_cleanup(self) -> None:
        # Given: a wrapped directory that cleans up successfully
        directory = Mock()

        # When: the context body fails
        with (
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
            self.assertRaisesRegex(RuntimeError, "body failure"),
        ):
            with _tempdir.TemporaryDirectory() as path:
                self.assertIs(path, directory.name)
                raise RuntimeError("body failure")

        # Then: cleanup ran without replacing the body failure
        directory.cleanup.assert_called_once_with()

    def test_context_body_exception_remains_primary_when_cleanup_fails(self) -> None:
        # Given: a wrapped directory whose cleanup fails
        directory = Mock()
        directory.cleanup.side_effect = OSError("cleanup failure")

        # When: the context body also fails
        with (
            patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory),
            self.assertRaisesRegex(RuntimeError, "body failure"),
        ):
            with _tempdir.TemporaryDirectory():
                raise RuntimeError("body failure")

        # Then: cleanup was attempted but did not replace the body exception
        directory.cleanup.assert_called_once_with()

    def test_constructor_arguments_and_enter_path_are_preserved(self) -> None:
        # Given: constructor arguments and a wrapped directory path
        directory = Mock()

        # When: the wrapper is constructed and entered
        with patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory) as factory:
            with _tempdir.TemporaryDirectory(".suffix", "prefix-", "root") as path:
                pass

        # Then: public constructor arguments and the entered path are unchanged
        factory.assert_called_once_with(
            suffix=".suffix",
            prefix="prefix-",
            dir="root",
            ignore_cleanup_errors=False,
        )
        self.assertIs(path, directory.name)

    def test_name_delegates_to_wrapped_directory(self) -> None:
        # Given: a wrapped public temporary directory
        directory = Mock()

        # When: its public name is requested
        with patch("tests._tempdir.tempfile.TemporaryDirectory", return_value=directory):
            name = _tempdir.TemporaryDirectory().name

        # Then: the wrapped directory name is exposed
        self.assertIs(name, directory.name)
