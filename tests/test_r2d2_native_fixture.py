"""Synthetic native fixtures must never require or impersonate real consent."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from evospec.agent_protocol import execute
from evospec.services import CoreService
from tests._tempdir import TemporaryDirectory
from tests.r2d2_native_fixture import CHECKPOINT, FEATURE, MARKER, TASK, build_fixture


class R2D2NativeFixtureTests(unittest.TestCase):
    def test_native_runner_has_valid_powershell_syntax_when_available(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is unavailable on this host")
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").resolve()
        # Parse the script without executing any OpenCode command or creating
        # a fixture, so CI can catch syntax errors at zero provider cost.
        literal_path = str(runner).replace("'", "''")
        syntax_check = (
            "$tokens = $null; $errors = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{literal_path}', [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        completed = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", syntax_check],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_native_runner_requires_synthetic_markers_and_never_requests_consent(self) -> None:
        # The host smoke may observe a simulated agreement, but it must never
        # ask a human or the model to authorize a real Core acceptance.
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").read_text(encoding="utf-8")
        self.assertIn("tests.r2d2_native_fixture --scenario both", runner)
        self.assertIn("EVOSPEC_TEST_FIXTURE_ONLY.txt", runner)
        self.assertIn("openai/gpt-6.1-sol#high", runner)
        self.assertIn("--agent build", runner)
        self.assertIn("--auto", runner)
        self.assertIn("opencode run --standalone", runner)
        self.assertIn("opencode run --help", runner)
        self.assertIn("Get-CanonicalContextCalls", runner)
        self.assertIn("tools\\.evospec_context", runner)
        self.assertIn("No more model sessions", runner)
        self.assertNotIn("accept-slice", runner)
        self.assertNotIn("accept-fixture.ps1", runner)
        self.assertNotIn("gh pr", runner)
        self.assertNotIn("gh issue", runner)

    def test_parked_fixture_exposes_inactive_agreement_without_read_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "parked"
            meta = build_fixture(root, "parked")
            self.assertTrue(meta["synthetic_only"])
            self.assertIn("no human acceptance", str(meta["provenance_disclaimer"]))
            self.assertIn("TEST ONLY", (root / MARKER).read_text(encoding="utf-8"))
            self.assertTrue((root / ".opencode/plugins/evospec/index.ts").is_file())

            # The only authoritative records belong to this new temp root.
            before = {
                item.relative_to(root): (item.read_bytes(), item.stat().st_mtime_ns)
                for item in (root / ".evospec").rglob("*") if item.is_file()
            }
            context = json.loads(execute(root, '{"action":"context"}'))["context"]
            self.assertIsNone(context["accepted_slice"])
            self.assertIsNone(context["work_queue"]["active"])
            record = context["work_queue"]["all"][0]["accepted_slice"]
            self.assertEqual(record["feature_title"], FEATURE)
            self.assertEqual(record["initial_task"], TASK)
            self.assertEqual(record["checkpoint"], CHECKPOINT)
            self.assertEqual(context["revision"], meta["revision"])
            after = {
                item.relative_to(root): (item.read_bytes(), item.stat().st_mtime_ns)
                for item in (root / ".evospec").rglob("*") if item.is_file()
            }
            self.assertEqual(before, after)

    def test_ambiguous_fixture_never_selects_a_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "ambiguous"
            meta = build_fixture(root, "ambiguous")
            original = CoreService.open(root).recover()
            context = json.loads(execute(root, '{"action":"context"}'))["context"]
            self.assertEqual(meta["next_code"], "AMBIGUOUS")
            self.assertEqual(context["work_queue"]["next_candidates_total"], 2)
            self.assertEqual(len(context["work_queue"]["all"]), 2)
            self.assertIsNone(context["work_queue"]["active"])
            self.assertIsNone(context["accepted_slice"])
            self.assertEqual(CoreService.open(root).recover(), original)

    def test_existing_fixture_root_is_never_overwritten(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "existing"
            root.mkdir()
            marker = root / "user-file.txt"
            marker.write_text("preserve this file", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be new"):
                build_fixture(root, "parked")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve this file")
            self.assertEqual(list(root.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
