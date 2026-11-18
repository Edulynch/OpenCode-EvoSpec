"""Thin local command adapter for the canonical WorkflowService."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
import stat
import sys
import tomllib
from collections.abc import Sequence
from importlib import metadata
from typing import TextIO
import unicodedata

from .agent import AgentContext, project_context
from .continuation_summary import ContinuationSummary, project_continuation
from .dashboard import DEFAULT_PORT, serve
from .opencode_integration import IntegrationRegistry, OpenCodeIntegrationInspection, OpenCodeIntegrationProvider
from .persistence import IntegrationRegistryError, RecoveryError
from .workflow import ClarificationAnswer, WorkflowError, WorkflowResult, WorkflowService
from .domain import (
    CriterionCoverageDeclaration,
    CoverageInput,
    ExpectedRevision,
    SliceCriterion,
)
from .input_fingerprint import COVERAGE_CONTRACT_VERSION
from .services import (
    ServiceError, SliceCriteriaSpec, UserAcceptance, slice_acceptance_statement,
    slice_criteria_decision_statement, validate_slice_criteria_spec,
)


class _ArgumentParser(argparse.ArgumentParser):
    version: str = ""


def _tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer from 1 through 65535") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer from 1 through 65535")
    return port


def _load_criteria_spec(path: str) -> SliceCriteriaSpec:
    """Read an explicit, bounded JSON proposal; never infer criteria from task state."""
    source = Path(path)
    try:
        if source.stat().st_size > 40000:
            raise ValueError("criteria file is too large")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("criteria file is missing or not valid JSON") from error
    expected = {"outcome", "example", "inclusions", "exclusions", "decisions", "criteria"}
    if type(raw) is not dict or set(raw) != expected:
        raise ValueError("criteria file must contain exactly outcome, example, inclusions, exclusions, decisions and criteria")
    for name in ("inclusions", "exclusions", "decisions", "criteria"):
        if type(raw[name]) is not list:
            raise ValueError(f"criteria {name} must be an array")
    criteria: list[SliceCriterion] = []
    for item in raw["criteria"]:
        if type(item) is not dict or set(item) != {"id", "expected_result", "expected_evidence"}:
            raise ValueError("each criterion requires id, expected_result and expected_evidence")
        criteria.append(SliceCriterion(item["id"], item["expected_result"], item["expected_evidence"]))
    spec = SliceCriteriaSpec(
        raw["outcome"], raw["example"], tuple(raw["inclusions"]),
        tuple(raw["exclusions"]), tuple(raw["decisions"]), tuple(criteria),
    )
    try:
        validate_slice_criteria_spec(spec)
    except ServiceError as error:
        raise ValueError(str(error)) from error
    return spec


def _load_coverage_declarations(
    path: str,
    project_root: Path,
) -> tuple[CriterionCoverageDeclaration, ...]:
    """Read only a bounded, non-symlink proposal beneath the selected project root."""
    if (
        type(path) is not str
        or not path
        or "\\" in path
        or ":" in path
        or path.startswith("/")
        or unicodedata.normalize("NFC", path) != path
        or any(part in ("", ".", "..") for part in path.split("/"))
        or path.split("/", 1)[0].casefold() in (".git", ".evospec")
    ):
        raise ValueError("coverage file must be a normalized project-relative path outside managed directories")
    try:
        root = project_root.resolve(strict=True)
        source = root.joinpath(*path.split("/"))
        cursor = root
        parts = path.split("/")
        for index, part in enumerate(parts):
            cursor = cursor / part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("coverage file path cannot traverse a symlink or reparse point")
            if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ValueError("coverage file parent is not a directory")
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("coverage file must be a regular file without hard links")
        if info.st_size > 40000:
            raise ValueError("coverage file is too large")
        canonical = source.resolve(strict=True)
        if not canonical.is_relative_to(root):
            raise ValueError("coverage file resolves outside the selected project")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("coverage file is missing or not valid JSON") from error
    if (
        type(raw) is not dict
        or set(raw) != {"version", "criteria"}
        or raw["version"] != COVERAGE_CONTRACT_VERSION
        or type(raw["criteria"]) is not list
        or not raw["criteria"]
    ):
        raise ValueError(f"coverage file must explicitly use version {COVERAGE_CONTRACT_VERSION} and list criteria")
    declarations: list[CriterionCoverageDeclaration] = []
    for item in raw["criteria"]:
        if type(item) is not dict or set(item) != {
            "criterion_id", "test_kind", "test_target", "known_inputs",
        } or type(item["known_inputs"]) is not list:
            raise ValueError("each coverage criterion requires its exact test descriptor and known_inputs array")
        inputs: list[CoverageInput] = []
        for known in item["known_inputs"]:
            if (
                type(known) is not dict
                or set(known) != {"kind", "path"}
                or known["kind"] not in ("root-file", "root-directory")
                or type(known["path"]) is not str
            ):
                raise ValueError("known_inputs only accepts root-file/root-directory objects with a path")
            inputs.append(CoverageInput(known["kind"], known["path"]))
        if any(type(item[name]) is not str for name in ("criterion_id", "test_kind", "test_target")):
            raise ValueError("coverage criterion identity and descriptor must be strings")
        declarations.append(CriterionCoverageDeclaration(
            item["criterion_id"], item["test_kind"], item["test_target"], tuple(inputs),
        ))
    return tuple(declarations)


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="evospec")
    parser.version = _version()
    parser.add_argument("-v", "--version", action="version", version=parser.version)
    parser.add_argument(
        "--project",
        dest="root",
        default=".",
        metavar="PROJECT",
        help="Project directory (default: current directory)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    for name in ("new", "change"):
        command = commands.add_parser(name)
        command.add_argument("intent")
    clarify = commands.add_parser("clarify")
    clarify.add_argument("answer", nargs="*", metavar="QUESTION=ANSWER")
    commands.add_parser("continue")
    commands.add_parser("status")
    accept = commands.add_parser("accept-slice")
    accept.add_argument("--feature", required=True, help="Human-approved Feature title")
    accept.add_argument("--slice", required=True, help="Human-approved first Slice title")
    accept.add_argument("--task", required=True, help="Initial Task to resume after restart")
    accept.add_argument("--checkpoint", required=True, help="Initial pending verification checkpoint")
    accept.add_argument("--criteria-file", help="JSON mini-spec included in the same human acceptance")
    accept.add_argument("--expected-revision", required=True, type=int)
    next_slice = commands.add_parser("accept-next-slice")
    next_slice.add_argument("--feature-id", required=True, help="Exact identity of an already accepted Feature")
    next_slice.add_argument("--slice", required=True, help="Human-approved next Slice title")
    next_slice.add_argument("--task", required=True, help="Initial Task for the next Slice")
    next_slice.add_argument("--checkpoint", required=True, help="Initial verification checkpoint")
    next_slice.add_argument("--criteria-file", required=True, help="Complete JSON mini-spec for human acceptance")
    next_slice.add_argument("--expected-revision", required=True, type=int)
    complete = commands.add_parser("complete-feature")
    complete.add_argument("--feature-id", required=True, help="Exact accepted primary Feature identity")
    complete.add_argument("--condition", required=True, help="Human-owned Feature completion condition")
    complete.add_argument("--expected-revision", required=True, type=int)
    revise = commands.add_parser("revise-slice-criteria")
    revise.add_argument("--criteria-file", required=True, help="New complete Slice criteria JSON mini-spec")
    revise.add_argument("--previous-criteria-revision", required=True, help="Current criteria revision, or NOT_RECORDED")
    revise.add_argument("--reason", required=True, help="Why this material criterion amendment is needed")
    revise.add_argument("--expected-revision", required=True, type=int)
    upgrade = commands.add_parser("upgrade-e1-schema")
    upgrade.add_argument("--expected-revision", required=True, type=int)
    upgrade_r3 = commands.add_parser("upgrade-r3-schema")
    upgrade_r3.add_argument("--expected-revision", required=True, type=int)
    upgrade_r3b = commands.add_parser("upgrade-r3-outcome-schema")
    upgrade_r3b.add_argument("--expected-revision", required=True, type=int)
    upgrade_r3b.add_argument("--coverage-file", required=True, help="Explicit per-criterion root-input contract JSON")
    renew_r3b = commands.add_parser("renew-r3b-coverage")
    renew_r3b.add_argument("--expected-revision", required=True, type=int)
    renew_r3b.add_argument("--coverage-file", required=True, help="Explicit per-criterion root-input contract JSON")
    commands.add_parser("doctor")
    commands.add_parser("review")
    ui = commands.add_parser("ui")
    ui.add_argument("--port", type=_tcp_port, default=DEFAULT_PORT, metavar="PORT")
    ui.add_argument("--no-open", action="store_true", help="Do not open the dashboard in a browser")
    integration = commands.add_parser("integrate")
    integration.add_argument("target", choices=("opencode",), nargs="?")
    integration.add_argument("--dry-run", action="store_true")
    integration.add_argument("--remove", action="store_true")
    integration.add_argument("--refresh", action="store_true")
    return parser


def _version() -> str:
    """Return installed package metadata, or the source project's static metadata."""
    try:
        installed_version = metadata.version("evospec")
    except metadata.PackageNotFoundError:
        installed_version = None
    if installed_version is not None:
        return installed_version
    source_metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    return source_metadata["project"]["version"]


def main(argv: Sequence[str] | None = None, output: TextIO | None = None) -> int:
    """Run exactly one facade command and return a stable process status."""
    arguments = _parser().parse_args(argv)
    target = output if output is not None else sys.stdout
    if arguments.command == "integrate":
        if arguments.refresh and arguments.target is not None:
            _parser().error("--refresh cannot be combined with an integration provider")
        if arguments.refresh and arguments.remove:
            _parser().error("--refresh cannot be combined with --remove")
        if not arguments.refresh and arguments.target is None:
            _parser().error("an integration provider is required unless --refresh is used")
        registry = IntegrationRegistry((OpenCodeIntegrationProvider(),))
        try:
            if arguments.refresh:
                result = registry.refresh(Path(arguments.root), dry_run=arguments.dry_run)
            else:
                provider_name = arguments.target
                if not isinstance(provider_name, str):
                    _parser().error("an integration provider is required unless --refresh is used")
                result = registry.integrate(
                    Path(arguments.root), provider_name, dry_run=arguments.dry_run, remove=arguments.remove
                )
        except IntegrationRegistryError as error:
            print(f"ERROR {error}", file=target)
            return 2
        if result.conflicts:
            print(f"ERROR conflicts: {', '.join(result.conflicts)}", file=target)
            return 2
        print(" | ".join(result.changed) if result.changed else "CURRENT", file=target)
        return 0
    if arguments.command == "ui":
        try:
            serve(Path(arguments.root), arguments.port, open_browser=not arguments.no_open)
        except OSError as error:
            print(f"ERROR {error}", file=target)
            return 2
        return 0
    if arguments.command in (
        "accept-slice", "accept-next-slice", "revise-slice-criteria", "upgrade-e1-schema",
        "upgrade-r3-schema", "upgrade-r3-outcome-schema", "renew-r3b-coverage",
        "complete-feature",
    ):
        workflow = WorkflowService.open(Path(arguments.root))
        if arguments.command == "upgrade-e1-schema":
            try:
                already = workflow.confirmed_e1_schema_upgrade(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            except RecoveryError as error:
                print(f"ERROR {error}", file=target)
                return 2
            if already is not None:
                # An already committed conversion is an inspection, not a second approval.
                _render(already, target)
                return 0
        if arguments.command == "upgrade-r3-schema":
            try:
                already = workflow.confirmed_r3_schema_upgrade(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            except RecoveryError as error:
                print(f"ERROR {error}", file=target)
                return 2
            if already is not None:
                _render(already, target)
                return 0
        if arguments.command == "upgrade-r3-outcome-schema":
            try:
                already = workflow.confirmed_r3b_schema_upgrade(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            except RecoveryError as error:
                print(f"ERROR {error}", file=target)
                return 2
            if already is not None:
                _render(already, target)
                return 0
        if arguments.command == "complete-feature":
            try:
                already = workflow.confirmed_feature_completion(
                    arguments.feature_id, arguments.condition,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            except (ValueError, RecoveryError, WorkflowError) as error:
                print(f"ERROR {error}", file=target)
                return 2
            if already is not None:
                _render(already, target)
                return 0
        # Only a human in a real terminal may accept or change durable authority.
        if not sys.stdin.isatty() or not sys.stdout.isatty() or not target.isatty():
            print("ERROR approval requires an interactive user terminal", file=target)
            return 2
        if arguments.expected_revision < 0:
            print("ERROR expected revision must be nonnegative", file=target)
            return 2
        coverage = None
        try:
            if arguments.command == "accept-slice":
                criteria = None if not arguments.criteria_file else _load_criteria_spec(arguments.criteria_file)
                statement = slice_acceptance_statement(
                    arguments.feature, arguments.slice, arguments.task, arguments.checkpoint, criteria,
                )
            elif arguments.command == "accept-next-slice":
                criteria = _load_criteria_spec(arguments.criteria_file)
                statement = workflow.next_slice_agreement(
                    arguments.feature_id, arguments.slice, arguments.task, arguments.checkpoint,
                    criteria, expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "complete-feature":
                criteria = None
                statement = workflow.feature_completion_agreement(
                    arguments.feature_id, arguments.condition,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "revise-slice-criteria":
                criteria = _load_criteria_spec(arguments.criteria_file)
                statement = slice_criteria_decision_statement(
                    arguments.previous_criteria_revision, arguments.reason, criteria,
                )
            elif arguments.command == "upgrade-r3-schema":
                criteria = None
                statement = workflow.r3_upgrade_agreement(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "upgrade-r3-outcome-schema":
                criteria = None
                coverage = _load_coverage_declarations(arguments.coverage_file, Path(arguments.root))
                statement = workflow.r3b_upgrade_agreement(
                    coverage,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "renew-r3b-coverage":
                criteria = None
                coverage = _load_coverage_declarations(arguments.coverage_file, Path(arguments.root))
                statement = workflow.r3b_coverage_renewal_agreement(
                    coverage,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            else:
                criteria = None
                coverage = None
                statement = workflow.e1_upgrade_agreement(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
        except (ValueError, ServiceError, RecoveryError, WorkflowError) as error:
            print(f"ERROR {error}", file=target)
            return 2
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        verb = "DECIDO" if arguments.command == "revise-slice-criteria" else "ACEPTO"
        description = {
            "accept-slice": "Acuerdo inicial",
            "accept-next-slice": "Nuevo Slice de Feature existente",
            "complete-feature": "Finalización humana de Feature (incluye autorización explícita de schema 17→18)",
            "revise-slice-criteria": "Decisión de modificación",
            "upgrade-e1-schema": "Acuerdo de migración",
            "upgrade-r3-schema": "Acuerdo de migración R3-A",
            "upgrade-r3-outcome-schema": "Acuerdo de migración R3-B esquema 16→17",
            "renew-r3b-coverage": "Acuerdo de renovación de cobertura R3-B",
        }[arguments.command]
        print(f"{description} para aprobación explícita:\n{statement}", file=target)
        try:
            typed = input(f"Para autorizar la escritura, escribe {verb} {token}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("ERROR acceptance cancelled", file=target)
            return 2
        if typed != f"{verb} {token}":
            print("ERROR acceptance was not explicitly confirmed", file=target)
            return 2
        try:
            if arguments.command == "accept-slice":
                result = workflow.accept_first_slice(
                    arguments.feature, arguments.slice, arguments.task,
                    arguments.checkpoint, UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision), criteria=criteria,
                )
            elif arguments.command == "accept-next-slice":
                assert criteria is not None
                result = workflow.accept_next_slice(
                    arguments.feature_id, arguments.slice, arguments.task,
                    arguments.checkpoint, UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision), criteria=criteria,
                )
            elif arguments.command == "complete-feature":
                result = workflow.complete_feature(
                    arguments.feature_id, arguments.condition,
                    UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "revise-slice-criteria":
                assert criteria is not None
                result = workflow.revise_slice_criteria(
                    criteria, arguments.previous_criteria_revision, arguments.reason,
                    UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "upgrade-e1-schema":
                result = workflow.upgrade_e1_schema(
                    UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "upgrade-r3-outcome-schema":
                assert coverage is not None
                result = workflow.upgrade_r3b_schema(
                    UserAcceptance(statement, f"tty:{token}"),
                    coverage,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            elif arguments.command == "renew-r3b-coverage":
                assert coverage is not None
                result = workflow.renew_r3b_coverage(
                    UserAcceptance(statement, f"tty:{token}"),
                    coverage,
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            else:
                result = workflow.upgrade_r3_schema(
                    UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
        except (RecoveryError, WorkflowError) as error:
            print(f"ERROR {error}", file=target)
            return 2
        _render(result, target)
        return 0
    workflow = WorkflowService.open(Path(arguments.root))
    try:
        if arguments.command == "status":
            inspection = workflow.inspect_locks()
            _render_status(project_context(workflow, inspection), target)
            return 0
        match arguments.command:
            case "init":
                result = workflow.init()
            case "new":
                result = workflow.new(arguments.intent)
            case "change":
                result = workflow.change(arguments.intent)
            case "clarify":
                result = workflow.clarify(tuple(_answer(value) for value in arguments.answer))
            case "continue":
                result = workflow.continue_work()
            case "doctor":
                return _render_doctor(Path(arguments.root), workflow, target)
            case "review":
                result = workflow.review()
            case unreachable:
                raise AssertionError(f"unexpected command {unreachable}")
    except (RecoveryError, WorkflowError) as error:
        print(f"ERROR {error}", file=target)
        return 2
    _render(result, target)
    return 2 if result.code == "INIT_REQUIRED" and arguments.command in ("new", "change") else 0


def _answer(value: str) -> ClarificationAnswer:
    question, separator, answer = value.partition("=")
    if not separator or not question or not answer:
        raise WorkflowError("clarify answers must use QUESTION=ANSWER")
    return ClarificationAnswer(question, answer)


def _render(result: WorkflowResult, output: TextIO) -> None:
    fields = (result.code, str(result.revision), result.feature_id or "-", result.change_id or "-", result.phase or "-", result.message)
    print(" | ".join(fields), file=output)


def _next_guidance(context: AgentContext):
    if context.initialization_state == "UNINITIALIZED":
        return next(item for item in context.guidance if item.action.value == "init")
    guidance = next(item for item in context.guidance if item.action.value == "new")
    if not guidance.allowed and guidance.next_action is not None:
        return next(
            item for item in context.guidance if item.action is guidance.next_action
        )
    return guidance


def _render_status(context: AgentContext, output: TextIO) -> None:
    health = "RECOVERY_REQUIRED" if context.stale else context.initialization_state
    print(f"Health: {health}", file=output)
    print(f"Revision: {context.revision}", file=output)
    if context.feature_completion is not None:
        closure = context.feature_completion
        print(f"Feature completion: {closure.status} ({closure.feature_id})", file=output)
        if closure.receipt_id is not None:
            print(f"Feature completion receipt: {closure.receipt_id}", file=output)
        if closure.blockers:
            print("Feature completion blockers: " + ", ".join(closure.blockers), file=output)
    if context.lifecycle is not None:
        print(f"Flow: {context.lifecycle[0]} / {context.lifecycle[1]}", file=output)
        work = " | ".join(
            item for item in (context.feature_id, context.change_id, context.classification) if item is not None
        )
        print(f"Work: {work}", file=output)
        if context.progress.current_work is not None:
            print(f"Current: {context.progress.current_work}", file=output)
        if context.accepted_slice is not None:
            if context.e1_schema_ready is False:
                print("E1 schema: 13 | explicit upgrade-e1-schema approval required", file=output)
            print(
                f"Accepted Slice: {context.accepted_slice.slice_title} "
                f"| Task ID: {context.accepted_slice.task_id} "
                f"| Pending checkpoint: {context.accepted_slice.checkpoint}",
                file=output,
            )
            # Expected evidence is part of the scope, never proof of execution.
            print(f"Slice criteria: {context.accepted_slice.criteria_status}", file=output)
            if context.accepted_slice.criteria_revision is not None:
                print(f"Criteria revision: {context.accepted_slice.criteria_revision.id}", file=output)
        if context.task_progress is not None:
            print(
                f"Current Task report: {context.task_progress.checkpoint} "
                f"| {context.task_progress.report_kind} "
                f"| revision {context.task_progress.recorded_revision}",
                file=output,
            )
    elif context.completed_terminal:
        print("History: DONE", file=output)
    summary = project_continuation(context)
    guidance = _next_guidance(context)
    if summary.next_action_allowed and summary.next_action == guidance.action.value and guidance.allowed:
        print(f"Next: {summary.next_action} ({guidance.intervention.value}) - {guidance.message}", file=output)
    _render_continuation(summary, output)


def _render_continuation(summary: ContinuationSummary, output: TextIO) -> None:
    print(f"Continuation: {summary.status}", file=output)
    if summary.feature_id is not None:
        feature = summary.feature_id
        if summary.feature_title is not None:
            feature = f"{summary.feature_title} ({feature})"
        print(f"Feature context: {feature}", file=output)
    if summary.lifecycle is not None and summary.active_slice is None:
        print(
            f"Active lifecycle without accepted Slice: {summary.lifecycle[0]} / {summary.lifecycle[1]}",
            file=output,
        )
    if summary.active_slice is not None and summary.accepted_slice is not None:
        accepted = summary.accepted_slice
        print(
            f"Active Slice: {accepted.slice_title} ({accepted.slice_agreement_ref}) "
            f"| {summary.active_slice.provenance}",
            file=output,
        )
        print(
            f"Registered Task: {accepted.task_id} | {accepted.initial_task} "
            f"| initial checkpoint: {accepted.checkpoint}",
            file=output,
        )
        print(f"Criteria authority: {summary.active_slice.criteria_status}", file=output)
        verifications = summary.criterion_verification
        if isinstance(verifications, list):
            statuses = ", ".join(
                f"{item['criterion_id']}={item['status']}"
                for item in verifications if isinstance(item, dict)
            )
            if statuses:
                print(f"Criterion verification: {statuses}", file=output)
        if summary.task_progress is not None:
            print(
                f"Task report: {summary.task_progress.report_kind} "
                f"| {summary.task_progress.checkpoint} "
                f"| revision {summary.task_progress.recorded_revision}",
                file=output,
            )
        if summary.slice_outcome_status is not None:
            print(
                f"Slice outcome: {summary.slice_outcome_status} "
                "(does not establish Feature completion)",
                file=output,
            )
    elif summary.status == "EMPTY_SLOT":
        print("Active Slice: none (no Slice is CURRENT)", file=output)
    elif summary.status == "SOURCE_UNAVAILABLE":
        print(f"Continuation source: unavailable ({summary.next_message})", file=output)

    if summary.other_slices:
        if summary.feature_id is None:
            label = "Representable Slices (no current Feature selected)"
        else:
            label = "Other Slices (not active)"
        print(f"{label}:", file=output)
        for item in summary.other_slices:
            print(
                f"- {item.slice_title or item.slice_agreement_ref} "
                f"[{item.scope_status}; {item.provenance}; criteria {item.criteria_status}]"
                f"{'' if item.criteria_revision is None else f'; revision {item.criteria_revision.id}'}",
                file=output,
            )
    print(
        f"Slice inventory: {summary.inventory_visible}/{summary.inventory_total} visible; "
        f"complete={summary.inventory_complete}; truncated={summary.inventory_truncated}; "
        f"history={summary.inventory_history_scope}",
        file=output,
    )
    print(
        f"Work queue: {summary.queue_visible}/{summary.queue_total} visible; "
        f"complete={summary.queue_complete}; next={summary.queue_next_code}; "
        f"candidates={summary.queue_candidates_total} "
        f"(complete={summary.queue_candidates_complete}); "
        f"relations={summary.queue_relations_total} "
        f"(complete={summary.queue_relations_complete})",
        file=output,
    )
    if summary.terminal_history:
        print(
            "Terminal history exists; it is historical and does not by itself prove current Feature completion.",
            file=output,
        )
    print(f"Continuation next: {summary.next_code}", file=output)
    if summary.next_action is not None and summary.next_options_kind is not None:
        state = (
            "allowed" if summary.next_action_allowed is True
            else "blocked" if summary.next_action_allowed is False
            else "requires explicit input"
        )
        print(f"Continuation action ({state}): {summary.next_action}", file=output)
    print(f"Continuation guidance: {summary.next_message}", file=output)
    if summary.decision is not None:
        print(f"Decision: {summary.decision.decision}", file=output)
    if summary.next_options:
        label = {
            "MATERIAL_DECISION": "Decision options",
            "QUEUE_CANDIDATES": "Queue candidates (none selected)",
            "CORE_IDENTIFIED_CANDIDATE": "Core-identified candidate (not active)",
        }.get(summary.next_options_kind or "", "Options")
        print(f"{label}: {', '.join(summary.next_options)}", file=output)
    for value in summary.required_input:
        print(f"Required: {value}", file=output)


def _render_doctor(root: Path, workflow: WorkflowService, output: TextIO) -> int:
    try:
        target = root.resolve()
    except OSError:
        print(f"Runtime: evospec {_version()}", file=output)
        print("Project: UNUSABLE", file=output)
        return 2
    if not target.is_dir():
        print(f"Runtime: evospec {_version()}", file=output)
        print("Project: UNUSABLE", file=output)
        return 2
    integration = OpenCodeIntegrationProvider().inspect(target)
    print(f"Runtime: evospec {_version()}", file=output)
    print(f"Project: USABLE {target}", file=output)
    try:
        context = project_context(workflow)
    except RecoveryError:
        print("Authority: RECOVERY_REQUIRED", file=output)
        print("CLI: BLOCKED", file=output)
        _render_integration(integration, output)
        print("Workflow: UNAVAILABLE - state authority cannot be recovered", file=output)
        print("Readiness: BLOCKED", file=output)
        _render_remediation(integration, output)
        return 2
    print(f"Authority: {context.initialization_state} revision {context.revision}", file=output)
    cli_readiness = "READY" if context.initialization_state == "INITIALIZED" and not context.stale else "ACTION_REQUIRED"
    print(f"CLI: {cli_readiness}", file=output)
    _render_integration(integration, output)
    _render_opencode_readiness(integration, output)
    _render_doctor_workflow(context, output)
    exit_code = _doctor_exit_code(context, integration)
    readiness = "READY" if exit_code == 0 else "ACTION_REQUIRED" if exit_code == 1 else "BLOCKED"
    print(f"Readiness: {readiness}", file=output)
    _render_remediation(
        integration,
        output,
        authority_ready=not context.stale and context.initialization_state == "INITIALIZED",
        authority_stale=context.stale,
    )
    return exit_code


def _doctor_exit_code(context: AgentContext, integration: OpenCodeIntegrationInspection) -> int:
    if integration.state in ("UNREADABLE", "CONFLICT"):
        return 2
    if context.initialization_state != "INITIALIZED" or context.stale:
        return 1
    return 0 if integration.state == "CURRENT" else 1


def _render_doctor_workflow(context: AgentContext, output: TextIO) -> None:
    if context.lifecycle is None:
        print("Workflow: no active work", file=output)
        return
    print(f"Workflow: {context.lifecycle[0]} / {context.lifecycle[1]}", file=output)


def _render_opencode_readiness(inspection: OpenCodeIntegrationInspection, output: TextIO) -> None:
    readiness = "READY" if inspection.state == "CURRENT" else "REFRESH_REQUIRED" if inspection.state in ("STALE", "MISSING") else "BLOCKED"
    print(f"OpenCode readiness: {readiness}", file=output)


def _render_remediation(
    integration: OpenCodeIntegrationInspection,
    output: TextIO,
    *,
    authority_ready: bool = False,
    authority_stale: bool = False,
) -> None:
    if integration.state in ("STALE", "MISSING"):
        print("Remediation: evospec integrate --refresh", file=output)
    elif integration.state in ("CONFLICT", "UNREADABLE"):
        print("Remediation: manual review required", file=output)
    elif authority_stale:
        print("Remediation: assess stale semantic lock", file=output)
    elif not authority_ready:
        print("Remediation: initialize or recover EvoSpec authority", file=output)


def _render_integration(inspection: OpenCodeIntegrationInspection, output: TextIO) -> None:
    resource_states = ", ".join(
        f"{state}:{sum(1 for _, observed in inspection.resources if observed == state)}"
        for state in ("CURRENT", "STALE", "CONFLICT", "UNREADABLE", "MISSING")
        if any(observed == state for _, observed in inspection.resources)
    )
    summary = f"OpenCode: {inspection.state} registration:{inspection.registration} manifest:{inspection.manifest} resources:{resource_states}"
    print(summary, file=output)


if __name__ == "__main__":
    raise SystemExit(main())
