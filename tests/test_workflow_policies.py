from __future__ import annotations

import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import mock_open, patch

from evospec.discovery import DiscoveryPolicy, MAX_EXTERNAL_SDD_COMMAND_ENTRIES
from evospec.domain import (
    AssessmentConfidence,
    AssessmentDepth,
    AssessmentDepthValue,
    AssessmentEvidence,
    AssessmentOperation,
    CapabilityState,
    IntentAssessment,
    MaterialDecision,
    RepositoryPath,
)
from evospec.lifecycle import Classification, ReviewVerdict
from evospec.policies import AssurancePolicy, IntakeOperation, IntakePolicy
from evospec.services import CoreService
from evospec.workflow import ClarificationAnswer, WorkflowService
from tests._tempdir import TemporaryDirectory


class WorkflowPolicyTests(unittest.TestCase):
    def test_capability_matching_normalizes_punctuation_without_incidental_overlap(self) -> None:
        # Given: a one-word request persisted with terminal punctuation
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export.")
            workflow.reject()
            before = workflow.status().revision

            # When: the exact request is repeated without punctuation
            repeated = WorkflowService.open(root).new("Add export")

            # Then: punctuation does not create another capability
            self.assertEqual(repeated.code, "ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_capability_matching_requires_two_meaningful_terms(self) -> None:
        # Given: a Core Feature sharing two incidental terms with the request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add billing retry delay")
            workflow.reject()
            before = workflow.status().revision

            # When: CHANGE names a different capability with two overlapping terms
            result = WorkflowService.open(root).change("Add export retry delay")

            # Then: incidental overlap cannot adopt the Core Feature
            self.assertEqual(result.code, "NO_ESTABLISHED_CAPABILITY")
            self.assertEqual(WorkflowService.open(root).status().revision, before)

    def test_unassessed_lexical_core_match_requires_assessment_without_mutation(self) -> None:
        # Given: an archived Core Feature whose name overlaps an unrelated request
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("Add export")
            workflow.reject()
            before = CoreService.open(root).recover()

            # When: unassessed CHANGE includes the Core Feature's lexical term
            result = WorkflowService.open(root).change(
                "Change billing retries; leave export unchanged"
            )
            after = CoreService.open(root).recover()

            # Then: fallback evidence cannot bind or mutate the Core Feature
            self.assertEqual(result.code, "ASSESSMENT_REQUIRED")
            self.assertEqual(after.revision, before.revision)
            self.assertEqual(after.active_context, before.active_context)

    def test_project_candidates_require_a_contiguous_meaningful_phrase(self) -> None:
        # Given: two files containing separated words, not an export-delivery phrase
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.py").write_text("export pending delivery", encoding="utf-8")
            (root / "two.md").write_text("export later delivery", encoding="utf-8")

            # When: native capability evidence is scanned
            outcome = DiscoveryPolicy(root).capability_candidates("Change export delivery")

            # Then: individually present terms do not produce a capability candidate
            self.assertEqual(outcome.candidates, ())

    def test_complete_phrase_is_line_bounded_but_accepts_a_heading(self) -> None:
        # Given: a heading phrase followed by separate body text
        policy = DiscoveryPolicy(Path("."))

        # When: the phrase is checked against heading and longer-line evidence
        heading_match = policy._contains_complete_phrase(
            "# export delivery\n\ndownloads reports.", "export delivery"
        )
        extended_match = policy._contains_complete_phrase(
            "export delivery billing", "export delivery"
        )
        cross_line_match = policy._contains_complete_phrase(
            "export\ndelivery", "export delivery"
        )

        # Then: headings establish the phrase but same-line extensions do not
        self.assertTrue(heading_match)
        self.assertFalse(extended_match)
        self.assertFalse(cross_line_match)

    def test_project_candidates_require_a_repeated_meaningful_phrase(self) -> None:
        # Given: repeated generic text and one repeated meaningful capability phrase
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "delivery.py").write_text("export delivery", encoding="utf-8")
            (root / "docs" / "delivery.md").write_text("export delivery", encoding="utf-8")
            (root / "generic.py").write_text("with the behavior", encoding="utf-8")
            (root / "generic.md").write_text("with the behavior", encoding="utf-8")

            # When: project evidence is scanned for the request phrase
            outcome = DiscoveryPolicy(root).capability_candidates("Change export delivery behavior")

            # Then: only the meaningful two-term phrase becomes a candidate
            self.assertFalse(outcome.limited)
            self.assertEqual(tuple(item.name for item in outcome.candidates), ("export delivery",))
            self.assertEqual(
                DiscoveryPolicy(root).capability_candidates("Change with the behavior").candidates,
                (),
            )

    def test_capability_scan_bounds_entries_and_reports_the_limit(self) -> None:
        # Given: more eligible repository files than the deterministic scan budget
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text("export delivery", encoding="utf-8")

            # When: capability discovery reaches its eligible-file cap
            outcome = DiscoveryPolicy(root).capability_candidates("Change export delivery")

            # Then: the caller receives an explicit safe limit outcome
            self.assertTrue(outcome.limited)
            self.assertEqual(outcome.candidates, ())

    def test_recovery_probe_can_use_two_unique_native_references_after_the_global_file_limit(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index < 2 else "unrelated", encoding="utf-8"
                )

            discovery = DiscoveryPolicy(root).recovery_candidates("Change export delivery")

            self.assertFalse(discovery.limited)
            self.assertEqual(
                discovery.candidates[0].provenance,
                ("evidence-000.py", "evidence-001.py"),
            )

    def test_recovery_probe_fails_closed_for_ambiguous_or_incomplete_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(257):
                (root / f"evidence-{index:03}.py").write_text("export delivery", encoding="utf-8")

            limited = DiscoveryPolicy(root).recovery_candidates("Change export delivery")
            for index in range(257):
                (root / f"evidence-{index:03}.py").unlink()
            for index in range(4):
                (root / f"evidence-{index:03}.py").write_text(
                    "export delivery" if index < 2 else "billing retry", encoding="utf-8"
                )
            ambiguous = DiscoveryPolicy(root).recovery_candidates("Change export delivery billing retry")

            self.assertTrue(limited.limited)
            self.assertEqual(limited.candidates, ())
            self.assertEqual(tuple(item.name for item in ambiguous.candidates), ("export delivery", "billing retry"))

    def test_recovery_probe_excludes_foreign_speckit_commands_but_retains_project_opencode_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)
            for name in ("speckit.plan.md", "speckit.tasks.md"):
                (commands / name).write_text("export delivery", encoding="utf-8")

            foreign_only = DiscoveryPolicy(root).recovery_candidates("Change export delivery")

            for name in ("project-plan.md", "project-tasks.md"):
                (commands / name).write_text("export delivery", encoding="utf-8")
            project_owned = DiscoveryPolicy(root).recovery_candidates("Change export delivery")

            self.assertEqual(foreign_only.candidates, ())
            self.assertEqual(
                project_owned.candidates[0].provenance,
                (
                    ".opencode/commands/project-plan.md",
                    ".opencode/commands/project-tasks.md",
                ),
            )

    def test_foreign_speckit_commands_cannot_authorize_a_recovery_retry(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            for index in range(129):
                (root / f"evidence-{index:03}.py").write_text("unrelated", encoding="utf-8")
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)
            for name in ("speckit.plan.md", "speckit.tasks.md"):
                (commands / name).write_text("export delivery", encoding="utf-8")
            before = CoreService.open(root).recover()

            result = workflow.new("Add export delivery")

            self.assertEqual(result.code, "DISCOVERY_LIMIT")
            self.assertEqual(CoreService.open(root).recover(), before)

    def test_capability_scan_reads_at_most_the_content_cap_plus_one_byte(self) -> None:
        # Given: an oversized eligible file and a recording read boundary
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "oversized.py"
            oversized.write_bytes(b"export delivery " * 10_000)
            opener = mock_open(read_data=oversized.read_bytes())

            # When: the scanner inspects the oversized file
            with patch.object(Path, "open", opener):
                DiscoveryPolicy(root).capability_candidates("Change export delivery")

            # Then: it never requests an unbounded read
            opener().read.assert_called_once_with(65_537)

    def test_capability_scan_ignores_external_symlink_evidence(self) -> None:
        # Given: one internal file and an external symlink with matching text
        with TemporaryDirectory() as temporary, TemporaryDirectory() as external_temporary:
            root = Path(temporary)
            (root / "internal.py").write_text("export delivery", encoding="utf-8")
            external = Path(external_temporary) / "external.py"
            external.write_text("export delivery", encoding="utf-8")
            link = root / "external.py"
            try:
                link.symlink_to(external)
            except OSError:
                self.skipTest("symlink creation is unavailable on this platform")

            # When: the scanner evaluates repository-local evidence
            outcome = DiscoveryPolicy(root).capability_candidates("Change export delivery")

            # Then: the external target cannot create a project candidate
            self.assertFalse(outcome.limited)
            self.assertEqual(outcome.candidates, ())

    def test_discovery_is_ordered_bounded_and_never_executes_files(self) -> None:
        # Given: repository documentation, ignored state, and an unreadable binary candidate
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("read me", encoding="utf-8")
            (root / "AGENTS.md").write_text("instructions", encoding="utf-8")
            (root / "script.py").write_text("raise SystemExit(1)", encoding="utf-8")
            (root / ".evospec").mkdir()
            (root / ".evospec" / "README.md").write_text("ignored", encoding="utf-8")

            # When: deterministic discovery is evaluated twice
            first = DiscoveryPolicy(root).discover()
            second = DiscoveryPolicy(root).discover()

            # Then: evidence is stable and project code was not run
            self.assertEqual(first, second)
            self.assertEqual(tuple(str(item.path) for item in first), ("AGENTS.md", "README.md"))

    def test_discovery_excludes_vendor_parts_but_retains_project_opencode_instructions(self) -> None:
        # Given: project-owned OpenCode instructions beside nested vendor documentation
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("project docs", encoding="utf-8")
            (root / ".opencode").mkdir()
            (root / ".opencode" / "AGENTS.md").write_text("project instructions", encoding="utf-8")
            vendor = root / ".opencode" / "node_modules" / "dependency"
            vendor.mkdir(parents=True)
            (vendor / "README.md").write_text("vendor docs", encoding="utf-8")
            nested_vendor = root / "docs" / "build" / "generated"
            nested_vendor.mkdir(parents=True)
            (nested_vendor / "AGENTS.md").write_text("generated instructions", encoding="utf-8")

            # When: document evidence is discovered
            evidence = DiscoveryPolicy(root).discover()

            # Then: ignored parts never contribute while native .opencode instructions remain
            self.assertEqual(
                tuple(str(item.path) for item in evidence),
                (".opencode/AGENTS.md", "README.md"),
            )

    def test_external_sdd_evidence_projects_only_recognized_structure(self) -> None:
        # Given: recognized structures beside ordinary and custom process material
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)
            (commands / "speckit.plan.md").write_text("foreign command", encoding="utf-8")
            (root / "openspec" / "specs").mkdir(parents=True)
            (root / "openspec" / "changes").mkdir()
            (root / "openspec" / "config.yaml").write_text("foreign config", encoding="utf-8")
            (root / ".openspec-store").mkdir()
            (root / ".openspec-store" / "store.yaml").write_text("foreign store", encoding="utf-8")
            (root / "specs").mkdir()
            (root / "custom-process").mkdir()

            # When: bounded external SDD structure is projected
            discovery = DiscoveryPolicy(root).external_sdd_evidence()

            # Then: only supported structural facts are returned in stable order
            self.assertEqual(
                discovery.evidence,
                (
                    ("OPENSPEC", (".openspec-store/store.yaml", "openspec/changes/", "openspec/config.yaml", "openspec/specs/")),
                    ("SPEC_KIT", (".opencode/commands/speckit.plan.md", ".specify/")),
                ),
            )

    def test_external_sdd_direct_probes_are_independent_of_capability_scan_limits(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            for index in range(513):
                (root / f"unrelated-{index:03}.py").write_text("unrelated", encoding="utf-8")

            discovery = DiscoveryPolicy(root).external_sdd_evidence()

            self.assertEqual(discovery.evidence, (("SPEC_KIT", (".specify/",)),))
            self.assertFalse(discovery.limited)

    def test_external_sdd_command_probe_discards_partial_facts_after_its_entry_cap(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            (root / "openspec").mkdir()
            (root / "openspec" / "config.yaml").write_text("foreign config", encoding="utf-8")
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)
            for index in range(MAX_EXTERNAL_SDD_COMMAND_ENTRIES + 1):
                (commands / f"speckit.command-{index:03}.md").write_text("foreign command", encoding="utf-8")

            first = DiscoveryPolicy(root).external_sdd_evidence()
            second = DiscoveryPolicy(root).external_sdd_evidence()

            self.assertTrue(first.limited)
            self.assertEqual(first, second)
            self.assertEqual(
                first.evidence,
                (
                    ("OPENSPEC", ("openspec/config.yaml",)),
                    ("SPEC_KIT", (".specify/",)),
                ),
            )

    def test_recovery_probe_rejects_external_symlink_evidence(self) -> None:
        with TemporaryDirectory() as temporary, TemporaryDirectory() as external_temporary:
            root = Path(temporary)
            (root / "internal.py").write_text("export delivery", encoding="utf-8")
            external = Path(external_temporary) / "external.py"
            external.write_text("export delivery", encoding="utf-8")
            try:
                (root / "external.py").symlink_to(external)
            except OSError:
                self.skipTest("symlink creation is unavailable on this platform")

            discovery = DiscoveryPolicy(root).recovery_candidates("Change export delivery")

            self.assertEqual(discovery.candidates, ())

    def test_external_sdd_evidence_excludes_ignored_and_external_symlink_paths(self) -> None:
        # Given: recognized structures only in excluded directories and through external links
        with TemporaryDirectory() as temporary, TemporaryDirectory() as external_temporary:
            root = Path(temporary)
            for ignored in (".git", ".pytest_cache", "build", "dist", "generated", "node_modules", "vendor"):
                (root / ignored / "openspec" / "specs").mkdir(parents=True)
            external = Path(external_temporary) / ".specify"
            external.mkdir()
            link = root / ".specify"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable on this platform")

            # When: structural discovery uses the shared safe traversal
            discovery = DiscoveryPolicy(root).external_sdd_evidence()

            # Then: excluded and external material cannot become evidence
            self.assertFalse(discovery.limited)
            self.assertEqual(discovery.evidence, ())

    def test_external_sdd_evidence_reports_partial_results_when_direct_command_probe_fails(self) -> None:
        # Given: recognized Spec-Kit structure and an unreadable direct command probe
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".specify").mkdir()
            commands = root / ".opencode" / "commands"
            commands.mkdir(parents=True)

            # When: the content-free direct command probe cannot enumerate its known path
            with patch(
                "evospec.discovery.os.scandir",
                side_effect=OSError("access denied"),
            ):
                discovery = DiscoveryPolicy(root).external_sdd_evidence()

            # Then: retained structural facts are explicitly incomplete
            self.assertEqual(discovery.evidence, (("SPEC_KIT", (".specify/",)),))
            self.assertTrue(discovery.limited)

    def test_workflow_init_accepts_a_relative_repository_root(self) -> None:
        # Given: repository documentation under the current relative directory
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("repository docs", encoding="utf-8")

            # When: the facade opens the repository as Path('.')
            with chdir(root):
                initialized = WorkflowService.open(Path(".")).init()

            # Then: discovery records relative evidence without raising ValueError
            self.assertEqual(initialized.code, "INITIALIZED")

    def test_intake_classifies_without_user_selection_and_review_precedence_is_deterministic(self) -> None:
        # Given: natural-language requests and explicit finding severities
        intake = IntakePolicy()
        assurance = AssurancePolicy()

        # When: policy classifies and evaluates the supplied findings
        classification = intake.classify(IntakeOperation.NEW, "Experiment a foundational storage migration")
        blocked = assurance.evaluate(("WARN", "BLOCKED"), True)
        warning = assurance.evaluate(("WARN",), True)

        # Then: internal classification and precedence do not depend on a caller-selected enum
        self.assertEqual(classification, Classification.EXPERIMENT)
        self.assertEqual(blocked.verdict, ReviewVerdict.BLOCKED)
        self.assertEqual(warning.verdict, ReviewVerdict.WARN)

    def test_intake_classification_uses_operation_and_leading_action_signals_only(self) -> None:
        # Given: requests whose incidental prose and leading actions disagree
        intake = IntakePolicy()

        # When: both intake operations classify their requests
        classifications = (
            intake.classify(IntakeOperation.NEW, "Semantic locks describe changes"),
            intake.classify(IntakeOperation.NEW, "Add export capability"),
            intake.classify(IntakeOperation.CHANGE, "Fix intake classification"),
            intake.classify(IntakeOperation.CHANGE, "Patch duplicate capability detection"),
            intake.classify(IntakeOperation.CHANGE, "Tune export retry delay"),
            intake.classify(IntakeOperation.NEW, "Experiment alternate storage"),
            intake.classify(IntakeOperation.CHANGE, "Foundation storage migration"),
            intake.classify(IntakeOperation.CHANGE, "This prose mentions experiment and tune"),
        )

        # Then: only leading process markers override the operation default
        self.assertEqual(
            classifications,
            (
                Classification.FEATURE,
                Classification.FEATURE,
                Classification.PATCH,
                Classification.PATCH,
                Classification.TUNE,
                Classification.EXPERIMENT,
                Classification.FOUNDATION,
                Classification.PATCH,
            ),
        )

    def test_clarification_persists_partial_answer_and_blocks_ready_until_complete(self) -> None:
        # Given: an assessment that identifies a material decision
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()
            assessment = IntentAssessment(
                AssessmentOperation.NEW,
                Classification.FEATURE,
                AssessmentConfidence.HIGH,
                CapabilityState.PLANNED,
                "Add export delivery",
                ("Add export delivery",),
                None,
                (AssessmentEvidence("caller", RepositoryPath("assessment.md")),),
                (MaterialDecision("export delivery channel", ("email", "download")),),
                AssessmentDepth(
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                    AssessmentDepthValue.NORMAL,
                ),
                "caller identified a material delivery decision",
            )
            intake = workflow.new("Add export delivery", assessment=assessment)

            # When: the answer is persisted through the canonical facade
            question = intake.questions[0]
            clarified = workflow.clarify((ClarificationAnswer(question.id, "email"),))

            # Then: the answer becomes a Core decision and work is ready
            self.assertEqual(intake.code, "NEEDS_CLARIFICATION")
            self.assertEqual(clarified.code, "READY")
            self.assertEqual(clarified.questions, ())
            self.assertEqual(WorkflowService.open(Path(temporary)).status().phase, "READY")

    def test_unassessed_question_mark_does_not_create_material_ambiguity(self) -> None:
        # Given: a natural-language intent with punctuation but no supplied assessment
        with TemporaryDirectory() as temporary:
            workflow = WorkflowService.open(Path(temporary))
            workflow.init()

            # When: the intent is recorded
            intake = workflow.new("Add export delivery?")

            # Then: punctuation cannot independently enter the clarification lifecycle
            self.assertEqual(intake.code, "PROPOSED")
            self.assertEqual(intake.questions, ())
            self.assertEqual(WorkflowService.open(Path(temporary)).status().phase, "RESEARCH")
