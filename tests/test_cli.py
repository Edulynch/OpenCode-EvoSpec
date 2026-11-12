from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from evospec.cli import _parser, _version, main
from evospec.domain import ReviewResult, VerificationEvidence
from evospec.lifecycle import ReviewVerdict
from evospec.opencode_integration import PLUGIN, _resource_bytes
from evospec.services import CoreService
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


class CliTests(unittest.TestCase):
    def test_cli_version_option_prints_resolved_version(self) -> None:
        # Given: installed package metadata with a distinct release version
        output = io.StringIO()

        # When: the public CLI version option is invoked
        with patch("evospec.cli.metadata.version", return_value="9.8.7"), redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            _parser().parse_args(("--version",))

        # Then: argparse exits successfully after printing the resolved version
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue(), "9.8.7\n")

    def test_cli_short_version_option_prints_resolved_version(self) -> None:
        # Given: installed package metadata with a distinct release version
        output = io.StringIO()

        # When: the short public version option is invoked
        with patch("evospec.cli.metadata.version", return_value="9.8.7"), redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            _parser().parse_args(("-v",))

        # Then: argparse exits successfully after printing the resolved version
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue(), "9.8.7\n")

    def test_cli_version_uses_installed_package_metadata(self) -> None:
        # Given: installed package metadata with a distinct release version
        output = io.StringIO()

        # When: the version option is parsed
        with patch("evospec.cli.metadata.version", return_value="9.8.7"), redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            _parser().parse_args(("--version",))

        # Then: the console surface reports the installed package metadata version
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue(), "9.8.7\n")

    def test_cli_version_falls_back_to_source_package_metadata(self) -> None:
        # Given: a source checkout without installed package metadata
        with patch("evospec.cli.metadata.version", side_effect=metadata.PackageNotFoundError):

            # When: the CLI resolves its version
            version = _version()

        # Then: the canonical project metadata remains the source fallback
        self.assertEqual(version, "0.5.0")

    def test_cli_version_falls_back_when_installed_metadata_is_empty(self) -> None:
        # Given: an installed metadata provider with no usable version
        with patch("evospec.cli.metadata.version", return_value=None):

            # When: the CLI resolves its version
            version = _version()

        # Then: the canonical project metadata remains the source fallback
        self.assertEqual(version, "0.5.0")

    def test_cli_reports_uninitialized_authority_without_treating_it_as_a_workflow_error(self) -> None:
        # Given: an integrated project whose lifecycle authority was never initialized
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), output), 0)

            # When: status and intake commands use the standalone CLI surface
            status = main(("--project", temporary, "status"), output)
            new = main(("--project", temporary, "new", "Add export capability"), output)
            change = main(("--project", temporary, "change", "Change export capability"), output)

            # Then: integration is separate, status succeeds, and intake requests recovery with exit 2
            self.assertTrue((root / ".evospec" / "integrations.json").is_file())
            self.assertFalse((root / ".evospec" / "state.json").exists())
            self.assertEqual((status, new, change), (0, 2, 2))
            self.assertEqual(output.getvalue().count("INIT_REQUIRED"), 2)
            self.assertNotIn("ERROR init must establish repository authority first", output.getvalue())

    def test_cli_preserves_lifecycle_commands_and_exposes_doctor(self) -> None:
        # Given: the supported parser
        parser = _parser()

        # When: its subcommands are inspected
        action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))

        # Then: the public command surface remains intentionally narrow
        self.assertEqual(
            tuple(name for name in action.choices if name not in ("integrate", "doctor")),
            ("init", "new", "change", "clarify", "continue", "status", "review", "ui"),
        )
        self.assertIn("doctor", action.choices)

    def test_doctor_reports_recovery_and_integration_states_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = io.StringIO()
            before = tuple(root.rglob("*"))

            self.assertEqual(main(("--project", temporary, "doctor"), output), 1)
            self.assertEqual(tuple(root.rglob("*")), before)
            self.assertIn("Authority: UNINITIALIZED revision 0", output.getvalue())
            self.assertIn("CLI: ACTION_REQUIRED", output.getvalue())
            self.assertIn("OpenCode: MISSING", output.getvalue())
            self.assertIn("OpenCode readiness: REFRESH_REQUIRED", output.getvalue())

            WorkflowService.open(root).init()
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "doctor"), output), 1)
            self.assertIn("Authority: INITIALIZED", output.getvalue())
            self.assertIn("CLI: READY", output.getvalue())
            self.assertIn("OpenCode: MISSING", output.getvalue())
            self.assertIn("OpenCode readiness: REFRESH_REQUIRED", output.getvalue())
            self.assertIn("Remediation: evospec integrate --refresh", output.getvalue())

            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), io.StringIO()), 0)
            integrated_before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(main(("--project", temporary, "doctor"), output), 0)
            self.assertEqual({path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}, integrated_before)
            self.assertIn("Authority: INITIALIZED", output.getvalue())
            self.assertIn("CLI: READY", output.getvalue())
            self.assertIn("OpenCode: CURRENT", output.getvalue())
            self.assertIn("OpenCode readiness: READY", output.getvalue())

            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "doctor"), output), 0)
            self.assertIn("Authority: INITIALIZED", output.getvalue())
            self.assertIn("OpenCode: CURRENT", output.getvalue())

    def test_doctor_marks_corrupt_authority_as_recovery_required_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / ".evospec" / "state.json"
            state.parent.mkdir()
            state.write_text("not json", encoding="utf-8")
            before = state.read_bytes()
            output = io.StringIO()

            self.assertEqual(main(("--project", temporary, "doctor"), output), 2)

            self.assertEqual(state.read_bytes(), before)
            self.assertIn("Authority: RECOVERY_REQUIRED", output.getvalue())
            self.assertIn("CLI: BLOCKED", output.getvalue())
            self.assertIn("Readiness: BLOCKED", output.getvalue())

    def test_doctor_identifies_integrated_but_uninitialized_project(self) -> None:
        with TemporaryDirectory() as temporary:
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), io.StringIO()), 0)

            self.assertEqual(main(("--project", temporary, "doctor"), output), 1)

            self.assertIn("Authority: UNINITIALIZED", output.getvalue())
            self.assertIn("OpenCode: CURRENT", output.getvalue())

    def test_doctor_reports_stale_opencode_as_refresh_required_without_mutation(self) -> None:
        # Given: an initialized integration whose manifest-owned plugin is an older packaged version
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            WorkflowService.open(root).init()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), io.StringIO()), 0)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = io.StringIO()

            updated = dict(_resource_bytes())
            updated[PLUGIN] += b"\n// newer packaged plugin\n"
            with patch("evospec.opencode_integration._resource_bytes", return_value=updated):
                # When: doctor observes an older but unmodified manifest-owned plugin
                exit_code = main(("--project", temporary, "doctor"), output)

            # Then: refresh is required and doctor leaves every byte unchanged
            self.assertEqual(exit_code, 1)
            self.assertIn("OpenCode: STALE", output.getvalue())
            self.assertIn("OpenCode readiness: REFRESH_REQUIRED", output.getvalue())
            self.assertIn("Remediation: evospec integrate --refresh", output.getvalue())
            self.assertEqual(
                {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()},
                before,
            )

    def test_doctor_reports_conflicted_opencode_as_blocked_without_mutation(self) -> None:
        # Given: initialized authority with an OpenCode resource that becomes conflicting
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            WorkflowService.open(root).init()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), io.StringIO()), 0)
            plugin = root / PLUGIN
            plugin.write_bytes(plugin.read_bytes() + b"\nstale\n")
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = io.StringIO()

            # When: doctor observes the conflicting resource
            exit_code = main(("--project", temporary, "doctor"), output)

            # Then: blocked OpenCode state is exit 2, asks for manual review, and stays read-only
            self.assertEqual(exit_code, 2)
            self.assertIn("OpenCode: CONFLICT", output.getvalue())
            self.assertIn("OpenCode readiness: BLOCKED", output.getvalue())
            self.assertIn("Readiness: BLOCKED", output.getvalue())
            self.assertIn("Remediation: manual review required", output.getvalue())
            self.assertNotIn("Next: new", output.getvalue())
            self.assertEqual(
                {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()},
                before,
            )

    def test_status_renders_exact_flow_and_canonical_recovery_action(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "status"), output), 0)
            self.assertIn("Health: UNINITIALIZED", output.getvalue())
            self.assertIn("Next: init (USER)", output.getvalue())

            workflow.init()
            workflow.new("Export capability")
            workflow.record_progress("implement", "export", (), "write export", (), "test")
            workflow.pause()
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "status"), output), 0)
            self.assertIn("Flow: PAUSED / IMPLEMENT", output.getvalue())
            self.assertIn("Current: write export", output.getvalue())
            self.assertIn("Next: continue (AUTOMATIC)", output.getvalue())

            workflow.continue_work()
            workflow.record_review(ReviewResult(ReviewVerdict.PASS, (), True))
            workflow.record_verification(VerificationEvidence(("tests",), True))
            workflow.invalidate_assurance()
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "status"), output), 0)
            self.assertIn("Flow: IN_PROGRESS / IMPLEMENT", output.getvalue())
            self.assertIn("Next: review (AUTOMATIC)", output.getvalue())

    def test_cli_uses_explicit_project_for_workflow_commands(self) -> None:
        # Given: an empty repository and output capture
        with TemporaryDirectory() as temporary:
            output = io.StringIO()

            # When: the thin commands are invoked
            init_code = main(("--project", temporary, "init"), output)
            status_code = main(("--project", temporary, "status"), output)

            # Then: facade results are rendered with stable success codes
            self.assertEqual(init_code, 0)
            self.assertEqual(status_code, 0)
            self.assertIn("INITIALIZED", output.getvalue())
            self.assertTrue((Path(temporary) / ".evospec" / "state.json").is_file())

    def test_natural_cli_recovers_one_bounded_project_capability_or_stops_without_work(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index < 2 else "unrelated", encoding="utf-8"
                )
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "init"), output), 0)

            succeeded = main(("--project", temporary, "change", "Change export delivery"), output)
            state = CoreService.open(root).recover()
            self.assertEqual(succeeded, 0)
            self.assertIn("PROPOSED", output.getvalue())
            self.assertEqual(state.features[-1].title, "export delivery")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index == 0 else "unrelated", encoding="utf-8"
                )
            self.assertEqual(main(("--project", temporary, "init"), io.StringIO()), 0)
            before = CoreService.open(root).recover()

            stopped = main(("--project", temporary, "change", "Change export delivery"), output)

            self.assertEqual(stopped, 0)
            self.assertIn("DISCOVERY_LIMIT", output.getvalue())
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_cli_uses_current_directory_when_project_is_omitted(self) -> None:
        # Given: an empty directory as the public CLI working directory
        with TemporaryDirectory() as temporary:
            output = io.StringIO()

            # When: init is invoked without a project selector
            with chdir(temporary):
                exit_code = main(("init",), output)

            # Then: authority state is created in the current directory
            self.assertEqual(exit_code, 0)
            self.assertTrue((Path(temporary) / ".evospec" / "state.json").is_file())

    def test_cli_exposes_project_help_and_rejects_root(self) -> None:
        # Given: the public CLI parser and captured usage output
        parser = _parser()
        error = io.StringIO()

        # When: the renamed selector is inspected and its predecessor is supplied
        help_text = parser.format_help()
        with redirect_stderr(error), self.assertRaises(SystemExit) as exited:
            parser.parse_args(("--root", ".", "status"))

        # Then: only the documented project selector is accepted
        self.assertIn("--project PROJECT", help_text)
        self.assertIn("Project directory (default: current directory)", help_text)
        self.assertNotIn("--root", help_text)
        self.assertEqual(exited.exception.code, 2)
        self.assertIn("usage:", error.getvalue())

    def test_integrate_opencode_registers_provider_and_refresh_uses_registration(self) -> None:
        # Given: a project with no integration registry
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = io.StringIO()

            # When: OpenCode is explicitly integrated then refreshed without a provider
            installed = main(("--project", temporary, "integrate", "opencode"), output)
            (root / ".opencode" / "plugins" / "evospec" / "index.ts").unlink()
            refreshed = main(("--project", temporary, "integrate", "--refresh"), output)

            # Then: the provider is durable and refresh dispatches it from the registry
            self.assertEqual(installed, 0)
            self.assertEqual(refreshed, 0)
            self.assertEqual(
                json.loads((root / ".evospec" / "integrations.json").read_text(encoding="utf-8")),
                {"providers": ["opencode"], "schema": 1},
            )
            self.assertTrue((root / ".opencode" / "plugins" / "evospec" / "index.ts").is_file())

    def test_integrate_remove_unregisters_provider(self) -> None:
        # Given: an explicitly registered OpenCode integration
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = io.StringIO()
            self.assertEqual(main(("--project", temporary, "integrate", "opencode"), output), 0)

            # When: the provider is removed
            removed = main(("--project", temporary, "integrate", "opencode", "--remove"), output)

            # Then: provider registration is removed with its managed resources
            self.assertEqual(removed, 0)
            self.assertEqual(
                json.loads((root / ".evospec" / "integrations.json").read_text(encoding="utf-8")),
                {"providers": [], "schema": 1},
            )
            self.assertFalse((root / ".opencode" / "plugins" / "evospec" / "index.ts").exists())

    def test_refresh_refuses_legacy_v1_resources_and_ignores_arbitrary_config(self) -> None:
        # Given: an unmanifested V1 resource and a separate project with only user configuration
        with TemporaryDirectory() as temporary, TemporaryDirectory() as unrelated_temporary:
            root = Path(temporary)
            unrelated = Path(unrelated_temporary)
            output = io.StringIO()
            legacy = root / ".opencode" / "tools" / "evospec.ts"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("// EvoSpec-managed: opencode-v1\nexport default {}\n", encoding="utf-8")
            arbitrary_config = unrelated / "opencode.json"
            arbitrary_config.write_text('{"instructions": []}\n', encoding="utf-8")
            legacy_before = legacy.read_bytes()
            config_before = arbitrary_config.read_bytes()

            # When: provider-less refresh observes V1 residue and arbitrary configuration
            refused = main(("--project", temporary, "integrate", "--refresh"), output)
            ignored = main(("--project", unrelated_temporary, "integrate", "--refresh"), output)

            # Then: V1 files remain untouched, while config alone neither adopts nor mutates OpenCode
            self.assertEqual(refused, 2)
            self.assertEqual(ignored, 0)
            self.assertEqual(legacy.read_bytes(), legacy_before)
            self.assertFalse((root / ".evospec" / "integrations.json").exists())
            self.assertFalse((unrelated / ".evospec" / "integrations.json").exists())
            self.assertEqual(arbitrary_config.read_bytes(), config_before)

    def test_integrate_rejects_provider_with_refresh(self) -> None:
        # Given: the public CLI facade
        output = io.StringIO()

        # When/Then: refresh cannot be combined with an explicit provider
        with self.assertRaises(SystemExit):
            main(("integrate", "opencode", "--refresh"), output)

    def test_integrate_rejects_refresh_with_remove_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "user-resource.md"
            legacy.write_text("user-owned\n", encoding="utf-8")
            before = legacy.read_bytes()
            with self.assertRaises(SystemExit) as exited:
                main(("--project", temporary, "integrate", "--refresh", "--remove"), io.StringIO())

            self.assertEqual(exited.exception.code, 2)
            self.assertEqual(legacy.read_bytes(), before)
            self.assertFalse((root / ".evospec" / "integrations.json").exists())
