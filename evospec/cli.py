"""Thin local command adapter for the canonical WorkflowService."""

from __future__ import annotations

import argparse
from hashlib import sha256
import sys
import tomllib
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import TextIO

from .agent import AgentContext, project_context
from .dashboard import DEFAULT_PORT, serve
from .opencode_integration import IntegrationRegistry, OpenCodeIntegrationInspection, OpenCodeIntegrationProvider
from .persistence import IntegrationRegistryError, RecoveryError
from .workflow import ClarificationAnswer, WorkflowError, WorkflowResult, WorkflowService
from .domain import ExpectedRevision
from .services import UserAcceptance


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
    accept.add_argument("--expected-revision", required=True, type=int)
    upgrade = commands.add_parser("upgrade-e1-schema")
    upgrade.add_argument("--expected-revision", required=True, type=int)
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
    if arguments.command in ("accept-slice", "upgrade-e1-schema"):
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
        # Only a user at a real terminal can cross the first acceptance boundary.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("ERROR E1 approval requires an interactive user terminal", file=target)
            return 2
        if arguments.expected_revision < 0:
            print("ERROR expected revision must be nonnegative", file=target)
            return 2
        if arguments.command == "accept-slice":
            statement = (
                f"Acepto Feature: {arguments.feature}; Slice: {arguments.slice}; "
                f"Task: {arguments.task}; checkpoint: {arguments.checkpoint}"
            )
        else:
            try:
                statement = workflow.e1_upgrade_agreement(
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            except (RecoveryError, WorkflowError) as error:
                print(f"ERROR {error}", file=target)
                return 2
        token = sha256(statement.encode("utf-8")).hexdigest()[:12]
        print(f"Acuerdo para aprobación explícita:\n{statement}", file=target)
        try:
            typed = input(f"Para autorizar la escritura, escribe ACEPTO {token}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("ERROR acceptance cancelled", file=target)
            return 2
        if typed != f"ACEPTO {token}":
            print("ERROR acceptance was not explicitly confirmed", file=target)
            return 2
        try:
            if arguments.command == "accept-slice":
                result = workflow.accept_first_slice(
                    arguments.feature, arguments.slice, arguments.task,
                    arguments.checkpoint, UserAcceptance(statement, f"tty:{token}"),
                    expected_revision=ExpectedRevision(arguments.expected_revision),
                )
            else:
                result = workflow.upgrade_e1_schema(
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
        if context.task_progress is not None:
            print(
                f"Current Task report: {context.task_progress.checkpoint} "
                f"| {context.task_progress.report_kind} "
                f"| revision {context.task_progress.recorded_revision}",
                file=output,
            )
    elif context.completed_terminal:
        print("History: DONE", file=output)
    guidance = _next_guidance(context)
    next_action = guidance.action.value if guidance.allowed else "-" if guidance.next_action is None else guidance.next_action.value
    print(f"Next: {next_action} ({guidance.intervention.value}) - {guidance.message}", file=output)


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
