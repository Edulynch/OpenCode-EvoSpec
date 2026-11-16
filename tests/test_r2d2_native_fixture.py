"""Synthetic native fixtures must never require or impersonate real consent."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path

from evospec.agent_protocol import execute
from evospec.domain import ExpectedRevision, SliceCriterion
from evospec.services import CoreService, SliceCriteriaSpec, UserAcceptance, slice_acceptance_statement
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.r2d2_native_fixture import CHECKPOINT, FEATURE, MARKER, TASK, build_fixture


class R2D2NativeFixtureTests(unittest.TestCase):
    def test_native_gate_uses_canonical_ambiguity_without_unaccepted_title(self) -> None:
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").read_text(encoding="utf-8")
        # Only accepted agreements have titles in Core's canonical response.
        # Requiring the second item's human-readable label was a false negative.
        self.assertIn("function Test-CanonicalFixtureContext", runner)
        self.assertIn("$Output | ConvertFrom-Json -ErrorAction Stop", runner)
        self.assertIn("$context.work_queue.next_code -ceq 'AMBIGUOUS'", runner)
        self.assertIn("$context.work_queue.next_candidates_total -eq 2", runner)
        self.assertIn("$itemIds -ccontains $_", runner)
        self.assertNotIn("Another synthetic work item", runner)

    def test_ambiguous_canonical_context_has_no_title_for_unaccepted_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "ambiguous"
            build_fixture(root, "ambiguous")
            payload = json.loads(execute(root, '{"action":"context"}'))
            queue = payload["context"]["work_queue"]
            # Match actual native evidence: two IDs, one accepted agreement,
            # and no second title to search for in a tool's JSON output.
            self.assertEqual(queue["next_code"], "AMBIGUOUS")
            self.assertEqual(queue["next_candidates_total"], 2)
            self.assertEqual(set(queue["next_candidates"]), {item["id"] for item in queue["all"]})
            self.assertEqual(sum(item["accepted_slice"] is None for item in queue["all"]), 1)
            self.assertNotIn("Another synthetic work item", json.dumps(payload))

    def test_existing_native_evidence_can_be_replayed_without_model_calls(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is unavailable on this host")
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").resolve()
        with TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            evidence.mkdir()
            for name, scenario in (
                ("parked-natural", "parked"),
                ("parked-context", "parked"),
                ("parked-status", "parked"),
                ("ambiguous-natural", "ambiguous"),
            ):
                root = Path(temporary) / name
                build_fixture(root, scenario)
                output = execute(root, '{"action":"context"}')
                session_id = f"ses_fixture_{name}"
                # A completed tool event and a matching preflight prove the
                # recorded session, independently of any model answer text.
                events = [
                    {"type": "step_start", "sessionID": session_id},
                    {"type": "tool_use", "part": {"tool": "evospec_context", "state": {
                        "status": "completed", "output": output,
                    }}},
                ]
                (evidence / f"{name}.ndjson").write_text(
                    "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
                )
                preflight = {
                    "Scenario": name,
                    "PluginState": "active",
                    "ApprovedRunModel": "openai/gpt-6.1-sol#high",
                    "ModelCallsIssuedByRunnerBeforePreflight": 0,
                    "SessionID": session_id,
                    "ExpectedLocation": str(root),
                    "ObservedLocation": str(root),
                    "SessionLocation": str(root),
                    **{key: True for key in (
                        "LocationVerified", "PluginVerified", "ModelCatalogVerified",
                        "PythonCommandVerified", "PythonImportVerified",
                        "PythonContextVerified", "SessionVerified",
                    )},
                }
                (evidence / f"preflight-{name}.json").write_text(
                    json.dumps(preflight), encoding="utf-8"
                )

            replay = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(runner),
                      "-ReplayEvidenceRoot", str(evidence)]
            completed = subprocess.run(replay, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("R2D2_EVIDENCE_REPLAY: PASS (4/4", completed.stdout)

            # A plausible tool call with corrupted ambiguity must fail closed.
            bad_path = evidence / "ambiguous-natural.ndjson"
            recorded = [json.loads(line) for line in bad_path.read_text(encoding="utf-8").splitlines()]
            bad_context = json.loads(recorded[1]["part"]["state"]["output"])
            bad_context["context"]["work_queue"]["next_code"] = "NONE"
            recorded[1]["part"]["state"]["output"] = json.dumps(bad_context)
            bad_path.write_text("\n".join(json.dumps(event) for event in recorded) + "\n", encoding="utf-8")
            rejected = subprocess.run(replay, capture_output=True, text=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("no canonical ambiguous fixture context", rejected.stdout + rejected.stderr)

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
        # An explicit human-approved test route must never turn into automatic
        # provider selection or a fallback to a free catalog default.
        self.assertIn("$authorizedModel = 'openai/gpt-6.1-sol#high'", runner)
        self.assertIn("-and $ApprovedModel -cne $authorizedModel)", runner)
        self.assertIn("opencode run --server $serverUrl --session $fixtureSessionId --model $ApprovedModel", runner)
        self.assertIn("EXPLICIT_HUMAN_APPROVED_TEST_ONLY", runner)
        self.assertNotIn("--model openai/gpt-6.1-sol#high", runner)
        self.assertIn("--agent build", runner)
        self.assertIn("--auto", runner)
        self.assertIn("opencode run --server $serverUrl", runner)
        self.assertIn("opencode run --help", runner)
        self.assertIn("opencode serve --hostname 127.0.0.1", runner)
        self.assertIn("Get-CanonicalContextCalls", runner)
        self.assertIn("tools\\.evospec_context", runner)
        self.assertIn("No more model sessions", runner)
        # A private server can be on the correct directory while the CLI
        # still targets a stale session selected through the inherited PWD.
        self.assertIn("$env:PWD = $fixtureRoot", runner)
        self.assertIn("Remove-Item Env:PWD", runner)
        self.assertIn("$env:PWD = $previousPwd", runner)
        self.assertIn("--session $fixtureSessionId", runner)
        self.assertIn("observedSessionIds[0] -cne $fixtureSessionId", runner)
        self.assertNotIn("accept-slice", runner)
        self.assertNotIn("accept-fixture.ps1", runner)
        self.assertNotIn("gh pr", runner)
        self.assertNotIn("gh issue", runner)

    def test_native_runner_preflights_the_exact_private_host_before_model_use(self) -> None:
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").read_text(encoding="utf-8")
        # A private server is inspected before the model is allowed to start;
        # its process tree is then cleaned up regardless of the outcome.
        self.assertIn("/api/location", runner)
        self.assertIn("/api/plugin?location%5Bdirectory%5D=", runner)
        self.assertIn("$Evidence.LocationVerified = $true", runner)
        self.assertIn("$Evidence.PluginVerified = $true", runner)
        self.assertIn('Invoke-RestMethod -Method Post -Uri "$ServerUrl/api/session"', runner)
        self.assertIn('Invoke-RestMethod -Method Get -Uri "$ServerUrl/api/session/$sessionId"', runner)
        self.assertIn("$Evidence.SessionVerified = $true", runner)
        self.assertIn("/api/model?location%5Bdirectory%5D=", runner)
        self.assertIn("/api/model/default?location%5Bdirectory%5D=", runner)
        self.assertIn("$Evidence.ModelCatalogVerified = $true", runner)
        self.assertIn("$variants -cnotcontains 'high'", runner)
        self.assertIn("OPENCODE_DISABLE_FREE_MODELS = '1'", runner)
        self.assertIn("$state -eq 'active'", runner)
        self.assertIn("RandomNumberGenerator]::GetBytes(32)", runner)
        self.assertIn("-Environment @{ OPENCODE_PASSWORD = $privatePassword; OPENCODE_DISABLE_FREE_MODELS = '1' }", runner)
        self.assertIn("-Headers $headers", runner)
        self.assertIn("$env:OPENCODE_PASSWORD = $privatePassword", runner)
        self.assertIn("Remove-Item Env:OPENCODE_PASSWORD", runner)
        self.assertIn("ModelCallsIssuedByRunnerBeforePreflight = 0", runner)
        self.assertIn("$serverProcess.Kill($true)", runner)
        self.assertLess(
            runner.index("Assert-PrivateHostReady -FixtureRoot $fixtureRoot"),
            runner.index("Assert-ApprovedModelReady -FixtureRoot $fixtureRoot"),
        )
        self.assertLess(
            runner.index("Assert-ApprovedModelReady -FixtureRoot $fixtureRoot"),
            runner.index("New-VerifiedFixtureSession -FixtureRoot $fixtureRoot"),
        )
        self.assertLess(
            runner.index("New-VerifiedFixtureSession -FixtureRoot $fixtureRoot"),
            runner.index("& opencode run --server $serverUrl"),
        )
        # The full four-case suite remains an explicit opt-in; default runs
        # only the first natural-language parked case.
        self.assertIn("[switch]$IncludeRemainingScenarios", runner)
        self.assertIn("[string]$ApprovedModel = ''", runner)
        self.assertIn("if ($IncludeRemainingScenarios)", runner)

    def test_native_runner_installs_and_checks_its_private_python_before_inference(self) -> None:
        """A model cannot be asked to repair a missing test-runtime package."""
        runner = (Path(__file__).parent / "r2d2_native_smoke.ps1").read_text(encoding="utf-8")
        # Installing into an evidence-owned venv never changes the user's
        # system Python or the checked-in project state.
        self.assertIn("$runtimeRoot = Join-Path $parent 'python-runtime'", runner)
        self.assertIn("$runtimePython = Join-Path $runtimeRoot 'Scripts/python.exe'", runner)
        self.assertIn("& python -m venv $runtimeRoot", runner)
        self.assertIn("& $runtimePython -m pip install --no-deps --no-input --no-cache-dir", runner)
        self.assertIn("--disable-pip-version-check $sourceRoot", runner)
        self.assertIn("& $runtimePython -m evospec.cli --project $preparedFixture.root integrate --refresh", runner)
        # The plugin's embedded Python must be the private interpreter. The
        # package import and authoritative context are checked from the fixture.
        self.assertIn("const AGENT_COMMAND = (?<command>", runner)
        self.assertIn("agentCommand[2] -cne 'evospec.agent_protocol'", runner)
        self.assertIn("is_relative_to(pathlib.Path(sys.prefix).resolve())", runner)
        self.assertIn("& $PythonExecutable -m evospec.agent_protocol --root $FixtureRoot", runner)
        self.assertIn("$Evidence.PythonCommandVerified = $true", runner)
        self.assertIn("$Evidence.PythonImportVerified = $true", runner)
        self.assertIn("$Evidence.PythonContextVerified = $true", runner)
        self.assertIn("if ($contextCalls.Count -gt 0)", runner)
        self.assertIn("invoked evospec_context, but it did not return the synthetic fixture context", runner)
        self.assertLess(
            runner.index("Assert-IsolatedPythonContextReady -FixtureRoot $fixtureRoot"),
            runner.index("Assert-PrivateHostReady -FixtureRoot $fixtureRoot"),
        )
        self.assertLess(
            runner.index("Assert-IsolatedPythonContextReady -FixtureRoot $fixtureRoot"),
            runner.index("& opencode run --server $serverUrl"),
        )

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

    def test_parked_schema15_criteria_remain_read_only_and_inactive(self) -> None:
        """A parked Task retains R1 criteria without inheriting active authority."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "parked-with-criteria"
            root.mkdir()
            workflow = WorkflowService.open(root)
            initial = workflow.init()
            spec = SliceCriteriaSpec(
                "Recuperar el trabajo acordado", "Un Task aparcado conserva sus criterios",
                ("Mostrar el acuerdo",), ("No activar automáticamente",),
                ("Conservar el estado del Task",),
                (SliceCriterion("AC-PARKED", "Criterios visibles", "Lectura nativa del contexto"),),
            )
            statement = slice_acceptance_statement(FEATURE, "Slice temporal", TASK, CHECKPOINT, spec)
            # This provenance is simulated solely inside the disposable test root.
            synthetic = UserAcceptance(statement, f"tty:{sha256(statement.encode()).hexdigest()[:12]}")
            workflow.accept_first_slice(
                FEATURE, "Slice temporal", TASK, CHECKPOINT, synthetic,
                expected_revision=ExpectedRevision(initial.revision), criteria=spec,
            )
            self.assertEqual(workflow.park("test fixture parked").code, "PARKED")
            original = CoreService.open(root).recover()
            context = json.loads(execute(root, '{"action":"context"}'))["context"]
            self.assertIsNone(context["accepted_slice"])
            self.assertIsNone(context["work_queue"]["active"])
            parked = context["work_queue"]["all"][0]["accepted_slice"]
            self.assertEqual(parked["criteria_status"], "RECORDED")
            self.assertEqual(parked["criteria_revision"]["criteria"][0]["id"], "AC-PARKED")
            self.assertEqual(CoreService.open(root).recover(), original)

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
