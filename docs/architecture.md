# EvoSpec Architecture

EvoSpec has a simple responsibility split:

```text
agent or caller reasoning
          |
          v
   WorkflowService
          |
          v
 deterministic Core authority
          |
          v
  local persisted state and Living Specs
```

> **E0 V2 boundary:** [`methodology.md`](methodology.md) is an approved method-level contract, not an approved architecture. This page describes the existing runtime boundary below; it does not select or prove an implementation of the E0 method.

The agent or caller interprets intent, chooses what to inspect, supplies evidence, and makes semantic judgments. Core does not infer product meaning. It validates the facts and requests supplied to it, persists them, enforces lifecycle rules, recovers state, and guards publication.

## Agent owned reasoning

Agents own semantic reasoning. They can assess a request, identify material ambiguity, propose research depth, interpret evidence, and supply review, verification, lock, or convergence assessments. Those assessments must be attributable and structured when they cross into the workflow.

Core is agent independent. A different agent, model, conversation, or integration can recover the same persisted context without treating conversation memory as authority. Core does not run an agent, invoke a model, perform research, review code semantically, gather verification evidence, repair work, or decide to publish Product Truth.

## Deterministic Core authority

Core is the durable authority for:

* persisted workflow state and revision history
* valid lifecycle and phase transitions
* recovery of primary and side contexts
* accepted decisions, observations, checkpoints, review results, and verification evidence supplied by callers
* immutable Living Spec publication when guarded convergence explicitly requests it

Each mutation is checked against the current persisted state. An `expected_revision` lets a caller say which state it inspected. If that revision is stale, Core rejects the mutation rather than silently applying it to newer state. A successful mutation advances the stored revision.

## WorkflowService

`WorkflowService` is the application facade. It turns intent into a usable workflow, discovers repository context, routes new work and changes to existing capabilities, classifies changes internally, selects proportional research and verification depth, asks material clarification questions, and prepares structured evidence.

It delegates every durable mutation to Core. It also coordinates the conceptual phases:

```text
RESEARCH -> CLARIFY -> READY -> IMPLEMENT
          -> REVIEW -> VERIFY -> CONVERGE -> DONE
```

WorkflowService may evaluate structured workflow evidence for review. It does not replace human or agent judgment about product meaning. `review` inspection is read only, and convergence remains a separate guarded operation.

## Local persistence and recovery

State is persisted locally with atomic publication operations. Recovery reads the authoritative state rather than reconstructing progress from a conversation. The persisted context includes the active feature, current change, focus, phase, decisions, constraints, progress, review state, verification state, and resume information.

Side work preserves the primary context. When side work ends, Core restores the saved primary context through a validated operation. A failed publication leaves the prior authoritative state in place. Paused work resumes only through the stored valid state and phase. Terminal work is reported, not reopened automatically.

### Provisional Slice format (experimental fixture support)

Schema 22 is a dedicated, experimental persistence format for provisional future-Slice proposals. It leaves the ordinary `SCHEMA = 15` default and schema 15–21 layouts unchanged. Canonical immutable revisions live under `.evospec/records/provisional-slices/`; exact State references and one current head per proposal are the sole authority. Recovery checks canonical IDs and digests, root and existing Feature-agreement binding, provenance shape, and a contiguous linear history. Unreferenced files are inert.

This is fixture-only persistence support, not an enabled product operation: there is no service, CLI, agent, host, or UI route, and `StateStore` remains read-only. The private persistence test seam publishes no AgreementRecord, accepted criteria, Task, checkpoint, verification, or acceptance state. `USER_EXPRESSED` retains exact attributable text and a digest; its shape does not authenticate a host message or establish consent, acceptance, or human presence. Agent suggestions remain distinct.

The focused schema-22 regression tests were run after a test-first RED. They cover canonical encoding, strict provenance and reference validation, CAS/replay/conflict behavior, recovery, and an ordinary Core writer rejection. This does not qualify production activation, independently run the exact baseline mutator, or repeat G1–G3. The independent Nox baseline gate and Vera review remain pending; broader schema 16–21 fixture coverage is not claimed by this focused run.

For the independent baseline gate, create a disposable root beneath the runtime-approved temporary directory, then use the project’s ordinary accepted-Feature test fixture before adding only a D1 proposal:

```python
from pathlib import Path
from tests._tempdir import TemporaryDirectory
from tests.test_persistence import _create_provisional_fixture, _make_provisional_revision
from evospec.persistence import PROVISIONAL_SLICE_SCHEMA, StateStore, _StateStore

with TemporaryDirectory(dir=r"C:\Users\eduma\AppData\Local\Temp\opencode") as path:
    root = Path(path)
    base = _create_provisional_fixture(root)
    revision = _make_provisional_revision(base)
    d1 = _StateStore(root).publish_provisional_slice(base, revision, None)
    state_path = root / ".evospec" / "state.json"
    assert StateStore(root).load_state() == d1
    assert __import__("json").loads(state_path.read_text(encoding="utf-8"))["schema"] == PROVISIONAL_SLICE_SCHEMA
```

The fixture’s ordinary acceptance is synthetic test setup, not evidence of real human consent; the D1 publisher only preserves it. Run the exact-baseline ordinary writer route against this same `root` (for example, a `CoreService.record_safety_facts` mutation from its recovered active context), and compare `state.json` bytes and the `.evospec` file snapshot before and after. The baseline reader in this checkout predates schema 22; its expected fail-closed result is `RecoveryError` during recovery, before any write. Record the actual exception and unchanged snapshot; do not count this recipe as an executed or passing baseline gate.

## Provider neutral protocol

`evospec.agent` projects authoritative context into a bounded provider neutral shape. The related JSON protocol carries context, legal actions, structured intent, progress, review, verification, and convergence requests through WorkflowService. It returns the current revision and reports stale revision errors so an agent can reload before trying again.

The same read-only context projection can include bounded structural evidence of recognized external SDD layouts. This evidence is recomputed at context time and is never persisted, routed as EvoSpec authority, or used to infer activity, history, unknown state, or a conflict. Agents own those semantic assessments and preserve foreign material rather than changing it automatically.

This boundary keeps EvoSpec independent of any one provider. The protocol guide is [`agent-protocol.md`](agent-protocol.md). Semantic Lock details belong in [`semantic-locks.md`](semantic-locks.md), rather than in this overview.

## CLI and optional OpenCode adapter

The local CLI is a thin adapter over the same WorkflowService. Its supported workflow surface is seven commands: `init`, `new`, `change`, `clarify`, `continue`, `status`, and `review`. The CLI does not create a second state authority. Review inspection is read only; application API callers supply the richer review, verification, and convergence operations.

An optional OpenCode adapter uses the provider neutral protocol and does not make OpenCode a Core dependency. It adds an integration surface, not a new lifecycle authority. Installation and adapter details are documented in [`opencode.md`](opencode.md), with the standalone local usage described in [`standalone-cli.md`](standalone-cli.md).

## Expected revisions and explicit convergence

The protocol is context first. A caller reads the current context, follows the returned legal action, and includes the latest `expected_revision` on each mutation. This prevents an old agent view from overwriting newer persisted work.

The architecture has no automatic convergence. Core accepts a convergence request only when review is acceptable, verification is sufficient, and the caller has supplied the required reconciliation. When Product Truth changes, explicit Product Truth content is required for publication. When it does not change, the accepted outcome can be archived without a new publication.

Code and tests remain evidence at this boundary. Neither can publish Product Truth by itself. Only an explicit, guarded convergence request can publish a new immutable Living Spec revision.
