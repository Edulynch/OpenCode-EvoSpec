"""Build isolated R2-D2 fixtures for native OpenCode read-only smoke tests.

These records are synthetic test data. Their Core-required acceptance shape is
not evidence that a person accepted a real Feature or Task.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from hashlib import sha256
from pathlib import Path

from evospec.agent_protocol import execute
from evospec.domain import ExpectedRevision, OperationalState
from evospec.opencode_integration import IntegrationRegistry, OpenCodeIntegrationProvider
from evospec.services import CoreService, UserAcceptance
from evospec.workflow import WorkflowService


FEATURE = "Fixture temporal R2-D2"
TASK = "Consultar sin reactivar"
CHECKPOINT = "Pendiente consulta nativa de Task INACTIVO"
MARKER = "EVOSPEC_TEST_FIXTURE_ONLY.txt"


def build_fixture(root: Path, scenario: str) -> dict[str, object]:
    """Create one disposable, authoritative Core state without a CLI consent prompt."""
    if scenario not in ("parked", "ambiguous"):
        raise ValueError("unknown R2-D2 fixture scenario")
    if root.exists():
        raise ValueError("fixture root must be new; never overwrite an existing project")

    root.mkdir(parents=True)
    # Clearly label the directory before the Core starts writing state. This
    # test marker is not a production authorization or a persisted agreement.
    (root / MARKER).write_text(
        "TEST ONLY: synthetic Core data; no human accepted this Feature or Task.\n"
        "Do not reuse this directory as a real EvoSpec project.\n",
        encoding="utf-8",
    )
    workflow = WorkflowService.open(root)
    initial = workflow.init()
    slice_title = "Recuperacion de Task aparcado"
    statement = (
        f"Acepto Feature: {FEATURE}; Slice: {slice_title}; "
        f"Task: {TASK}; checkpoint: {CHECKPOINT}"
    )
    # Production Core currently requires INTERACTIVE_TERMINAL-shaped evidence.
    # Existing unit tests construct this same synthetic value. It is permitted
    # only inside this disposable, visibly marked fixture, never in real work.
    synthetic = UserAcceptance(
        statement,
        f"tty:{sha256(statement.encode('utf-8')).hexdigest()[:12]}",
    )
    accepted = workflow.accept_first_slice(
        FEATURE, slice_title, TASK, CHECKPOINT, synthetic,
        expected_revision=ExpectedRevision(initial.revision),
    )
    if accepted.code != "SLICE_ACCEPTED":
        raise RuntimeError(f"synthetic fixture not accepted by Core: {accepted.code}")
    if workflow.park("synthetic fixture waiting").code != "PARKED":
        raise RuntimeError("accepted synthetic Task could not be parked")

    if scenario == "ambiguous":
        # Two real queue candidates, not a model-invented alternative. They
        # remain inactive even when both are eligible for later activation.
        workflow.new("Another synthetic work item")
        workflow.park("synthetic fixture waiting")
        core = CoreService.open(root)
        state = core.recover()
        for item in state.work_items:
            state = core.set_work_state(state, item.id, OperationalState.READY)

    result = IntegrationRegistry((OpenCodeIntegrationProvider(),)).integrate(
        root, "opencode", dry_run=False, remove=False,
    )
    if result.conflicts:
        raise RuntimeError(f"OpenCode fixture integration conflict: {result.conflicts}")

    response = json.loads(execute(root, '{"action":"context"}'))
    if "error" in response:
        raise RuntimeError(f"synthetic context is unreadable: {response['error']}")
    context = response["context"]
    queue = context["work_queue"]
    if context["accepted_slice"] is not None or queue["active"] is not None:
        raise RuntimeError("test fixture accidentally retained active Task authority")
    if scenario == "parked" and queue["all"][0]["accepted_slice"] is None:
        raise RuntimeError("parked Task lost its synthetic agreement")
    if scenario == "ambiguous" and (
        queue["next_code"] != "AMBIGUOUS" or queue["next_candidates_total"] != 2
    ):
        raise RuntimeError("ambiguous fixture unexpectedly selected a candidate")

    return {
        "scenario": scenario,
        "root": str(root.resolve()),
        "revision": context["revision"],
        "next_code": queue["next_code"],
        "work_items": queue["total"],
        "synthetic_only": True,
        "provenance_disclaimer": "Core-compatible simulated test data; no human acceptance",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare isolated synthetic R2-D2 native fixtures")
    parser.add_argument("--scenario", choices=("parked", "ambiguous", "both"), default="both")
    args = parser.parse_args()
    parent = Path(tempfile.mkdtemp(prefix="evospec-r2d2-native-"))
    scenarios = ("parked", "ambiguous") if args.scenario == "both" else (args.scenario,)
    prepared = [build_fixture(parent / name, name) for name in scenarios]
    print(json.dumps({"root": str(parent), "fixtures": prepared}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
