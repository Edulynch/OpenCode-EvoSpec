from __future__ import annotations

import hashlib
import os
import sys
import unittest
from unittest.mock import patch

import evospec.request_scoped_objective_browser as browser_verifier
from evospec.request_scoped_objective_browser import (
    BrowserObjectiveError,
    verify_personal_tasks,
)

_BROWSER_TESTS_ENABLED = os.environ.get("EVOSPEC_RUN_BROWSER_TESTS") == "1"
_COMPLETE_CONTROLS = """
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.evoAction = "complete-task";
      row.append(checkbox);
"""


def task_app(*, completion: bool = True, extra_script: str = "") -> bytes:
    completion_setup = _COMPLETE_CONTROLS if completion else ""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Tasks</title></head>
<body>
  <form id="task-form">
    <input data-evo-action="task-input">
    <button type="submit" data-evo-action="add-task">Add</button>
  </form>
  <ul id="tasks"></ul>
  <script>
    document.querySelector("#task-form").addEventListener("submit", event => {{
      event.preventDefault();
      const input = document.querySelector('[data-evo-action="task-input"]');
      const row = document.createElement("li");
      row.dataset.evoTask = "";
      const text = document.createElement("span");
      text.textContent = input.value;
      row.append(text);
      {completion_setup}
      document.querySelector("#tasks").append(row);
      input.value = "";
      {extra_script}
    }});
  </script>
</body></html>"""
    return html.encode("utf-8")


class BrowserSuiteConfigurationTests(unittest.TestCase):
    def test_ci_cannot_silently_skip_the_required_browser_suite(self) -> None:
        if os.environ.get("EVOSPEC_REQUIRE_BROWSER_TESTS") == "1":
            self.assertEqual(_BROWSER_TESTS_ENABLED, True)
            self.assertEqual(
                os.name,
                "nt",
                "required real-browser verification is supported only on Windows",
            )


class BrowserInputContractTests(unittest.TestCase):
    def assert_not_verified(self, content: bytes, operations: tuple[str, ...]) -> None:
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, operations)
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_rejects_malformed_oversized_and_noncanonical_inputs_before_launch(
        self,
    ) -> None:
        self.assert_not_verified(b"\xff", ("create", "consult"))
        self.assert_not_verified(b"\x00", ("create", "consult"))
        self.assert_not_verified(b"x" * 262_145, ("create", "consult"))
        self.assert_not_verified(b"<html></html>", ("consult", "create"))
        self.assert_not_verified(b"<html></html>", ("create", "consult", "delete"))
        self.assert_not_verified(b"<html></html>", ("create", []))  # type: ignore[arg-type]

    def test_missing_optional_browser_is_reported_as_unavailable(self) -> None:
        with patch(
            "evospec.request_scoped_objective_browser.importlib.import_module",
            side_effect=ModuleNotFoundError,
        ):
            with self.assertRaises(BrowserObjectiveError) as raised:
                browser_verifier._load_playwright()
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
        )

    def test_missing_containment_fails_before_worker_creation(self) -> None:
        unavailable = BrowserObjectiveError(
            "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
            "test containment unavailable",
        )
        with (
            patch.object(
                browser_verifier,
                "_create_containment",
                side_effect=unavailable,
            ),
            patch.object(browser_verifier.subprocess, "Popen") as process_start,
        ):
            with self.assertRaises(BrowserObjectiveError) as raised:
                verify_personal_tasks(b"<html></html>", ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
        )
        process_start.assert_not_called()


    @unittest.skipUnless(sys.platform == "linux", "Linux browser verification is blocked")
    def test_linux_browser_proof_is_blocked_before_worker_or_receipt(self) -> None:
        with (
            patch.object(browser_verifier.subprocess, "Popen") as process_start,
            patch.object(browser_verifier, "_load_playwright") as playwright_load,
            patch.object(browser_verifier.hashlib, "sha256") as hash_result,
        ):
            with self.assertRaises(BrowserObjectiveError) as raised:
                verify_personal_tasks(b"<html></html>", ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
        )
        process_start.assert_not_called()
        playwright_load.assert_not_called()
        hash_result.assert_not_called()


@unittest.skipUnless(
    _BROWSER_TESTS_ENABLED and os.name == "nt",
    "set EVOSPEC_RUN_BROWSER_TESTS=1 on Windows to run OS containment tests",
)
class ProcessContainmentTests(unittest.TestCase):
    def test_timeout_kills_the_harmless_child_tree(self) -> None:
        outcome = browser_verifier._run_containment_tree_test()
        self.assertTrue(outcome.timed_out)
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.output, b"TREE\n")

    def test_windows_job_reports_collective_memory_limit(self) -> None:
        job = browser_verifier._WindowsJob()
        try:
            limits = job._query_extended_limits()
            self.assertIsNotNone(limits)
            assert limits is not None
            flags = int(limits.BasicLimitInformation.LimitFlags)
            self.assertEqual(
                flags & browser_verifier._JOB_OBJECT_LIMIT_JOB_MEMORY,
                browser_verifier._JOB_OBJECT_LIMIT_JOB_MEMORY,
            )
            self.assertEqual(
                flags & browser_verifier._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                browser_verifier._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            )
            self.assertEqual(
                flags
                & (
                    browser_verifier._JOB_OBJECT_LIMIT_BREAKAWAY_OK
                    | browser_verifier._JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
                ),
                0,
            )
            self.assertEqual(
                int(limits.JobMemoryLimit),
                browser_verifier._WINDOWS_JOB_MEMORY_LIMIT_BYTES,
            )
        finally:
            self.assertTrue(job.close(0.0))

    def test_worker_output_is_capped(self) -> None:
        with self.assertRaises(BrowserObjectiveError) as raised:
            browser_verifier._run_worker_output_test()
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
        )
        self.assertIn("bounded I/O", str(raised.exception))


@unittest.skipUnless(
    _BROWSER_TESTS_ENABLED and os.name == "nt",
    "set EVOSPEC_RUN_BROWSER_TESTS=1 on Windows with the browser extra and Chromium",
)
class RealBrowserObjectiveTests(unittest.TestCase):
    def test_add_and_consult_proves_two_distinct_live_rows_and_exact_bytes(self) -> None:
        content = task_app()
        self.assertEqual(
            verify_personal_tasks(content, ("create", "consult")),
            hashlib.sha256(content).hexdigest(),
        )

    def test_explicit_completion_checks_only_one_native_checkbox(self) -> None:
        content = task_app()
        self.assertEqual(
            verify_personal_tasks(content, ("create", "consult", "complete")),
            hashlib.sha256(content).hexdigest(),
        )

    def test_completion_is_optional_unless_explicit(self) -> None:
        content = task_app(completion=False)
        self.assertEqual(
            verify_personal_tasks(content, ("create", "consult")),
            hashlib.sha256(content).hexdigest(),
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult", "complete"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_static_display_without_live_add_behavior_fails_closed(self) -> None:
        content = b"""<!doctype html><html><body>
        <p data-evo-task>Already present</p>
        <input data-evo-action="task-input">
        <button data-evo-action="add-task">Add</button>
        </body></html>"""
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_row_created_during_fill_cannot_stand_in_for_add(self) -> None:
        content = b"""<!doctype html><html><body>
        <input data-evo-action="task-input">
        <button type="button" data-evo-action="add-task">Add</button>
        <ul id="tasks"></ul>
        <script>
          const input = document.querySelector('[data-evo-action="task-input"]');
          input.addEventListener("input", () => {
            const row = document.createElement("li");
            row.dataset.evoTask = "";
            row.textContent = input.value;
            document.querySelector("#tasks").append(row);
          });
        </script>
        </body></html>"""
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_task_row_observation_is_capped(self) -> None:
        rows = b"<li data-evo-task>Existing</li>" * 129
        content = (
            b"<!doctype html><html><body>"
            + rows
            + b'<input data-evo-action="task-input">'
            + b'<button data-evo-action="add-task">Add</button>'
            + b"</body></html>"
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_dropping_the_first_task_fails_consult_verification(self) -> None:
        content = task_app(
            extra_script=(
                'const rows = document.querySelectorAll("[data-evo-task]"); '
                "if (rows.length > 1) rows[0].remove();"
            )
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_completion_that_checks_both_tasks_fails_closed(self) -> None:
        content = task_app(
            extra_script=(
                'row.querySelector(\'input[type="checkbox"]\').addEventListener('
                '"change", () => document.querySelectorAll('
                '\'input[type="checkbox"]\').forEach(box => { box.checked = true; }));'
            )
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult", "complete"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_external_fetch_attempt_fails_closed(self) -> None:
        content = task_app(
            extra_script='fetch("https://outside.invalid/task").catch(() => {});'
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_popup_attempt_fails_closed(self) -> None:
        content = task_app(
            extra_script='window.open("https://outside.invalid/task", "_blank");'
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )

    def test_download_attempt_fails_closed(self) -> None:
        content = task_app(
            extra_script=(
                'const link = document.createElement("a"); '
                'link.href = "data:text/plain,task"; link.download = "task.txt"; '
                "link.click();"
            )
        )
        with self.assertRaises(BrowserObjectiveError) as raised:
            verify_personal_tasks(content, ("create", "consult"))
        self.assertEqual(
            raised.exception.code,
            "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
        )


if __name__ == "__main__":
    unittest.main()
