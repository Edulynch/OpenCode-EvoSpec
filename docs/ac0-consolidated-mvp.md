# AC0 — Composed continuity MVP contract

This document describes the source-implemented, opt-in composed workflow for
tracking Features, optional Increments, Tasks, checks, pauses, and recovery in
an OpenCode V2 worktree. It is a product contract, not a runtime project
record. The implementation is present on this source branch; this document
does not claim a merged release, CI qualification, or independently witnessed
native OpenCode permission behavior. See [OpenCode V2 integration](opencode.md)
for the current integration and qualification boundary.

## ADR — one composed writer, separate legacy authority

**Status:** implemented in source; native-host qualification remains separate.

**Context.** The existing lifecycle path already routes requests through the
provider-neutral protocol, `WorkflowService`, and `CoreService` into
persistence. AC0 also needs readable Markdown artifacts, revision-bound
updates, safe interrupted-publication recovery, and exclusion between
concurrent processes. A writer in a command, plugin, or a second service would
create another authority and could bypass Core's checks and persistence
coordination.

| Before this decision | Decision and resulting contract |
| --- | --- |
| Existing projects may use legacy `.evospec/state.json` and its established lifecycle. | Composed work is a separate, explicit opt-in for a fresh root, stored in `.evospec/continuity.json`; it is never silently mixed with or migrated from legacy state. |
| Adapter-owned state changes would duplicate lifecycle and revision rules. | All supported composed operations follow `protocol → WorkflowService → CoreService → continuity.execute → ContinuityPersistence`. The adapter supplies the executing host session and enforces its ordinary permission boundary; Core owns validation, revision checks and durable changes. |
| A multi-document update can be interrupted between file replacements. | Core records a pending journal before publishing Markdown. Recovery verifies each old/new digest, resumes only the missing writes, and stops on any third version. |
| Thread-only synchronization would not coordinate separate host processes. | Continuity operations hold the root-keyed shared persistence lock across load, validation and commit, with an expected-revision check under that lock. |

**Why not add another writer:** the OpenCode adapter and commands are request
surfaces, not state owners. The single composed entry point reuses Core's
revision and publication boundary; adding a direct JSON/Markdown writer would
make those paths disagree about authority, recovery, and stale requests.
Composed persistence is a distinct schema from legacy `state.json`, but it is
still reached through the Core facade.

## Product mini-spec: M1–M7

These are the seven behavior areas of this MVP contract, not release or
qualification gates.

### M1 — Explicit mode selection and legacy exclusion

- `specify` opts a fresh repository into composed mode. It can also add a
  Feature in a repository already in composed mode.
- A present legacy `.evospec/state.json` blocks composed mode as
  `MIXED_AUTHORITY`; a present composed marker blocks legacy state recovery.
  There is no automatic migration, initialization, mode switch, or combination
  of the two stores.
- The state is bound to the resolved repository root. Symlinked and
  repository-escaping managed paths are rejected.

### M2 — Canonical, readable Feature documents

- The durable operational record is `.evospec/continuity.json` (schema 1).
  Feature definitions and Task definitions are canonical JSON data blocks
  inside consumer Markdown; Core renders and reads those blocks through
  [`continuity_documents.py`](../evospec/continuity_documents.py).
- The managed documents are `specs/README.md` and, per Feature,
  `specs/NNN-<slug>/{spec,plan,tasks,decisions}.md`. The index is delimited by
  EvoSpec markers; content outside that block is preserved.
- The JSON record holds the revision, Feature directory/status/cycle index,
  selection, Task-attempt state/history, active checkpoint, checks,
  close/reopen history, pauses, stable IDs, managed-document digests, and any
  pending publication journal. It does not duplicate the full Feature
  definition as a second mutable source.
- Core checks the full digest of each managed file before mutating. Any
  external edit or deletion of a tracked document is divergence, not an
  instruction to overwrite it.

### M3 — Structured Features, plans, Tasks, and selection

- A Feature has a title, outcome, at least one explicit criterion, and at
  least one Task. It may have up to 100 optional ordered Increments (Slice
  groupings). Each Task links to current criteria and may link to an Increment
  and earlier Tasks as dependencies.
- Tasks may be added incrementally; there is no fixed three-Task cap. Queue
  status and the generated Markdown checkbox are projections: editing a
  checkbox does not record progress.
- Selection is per host session, with a worktree default. A session-local
  selection does not create a second Feature or independent execution slot.

### M4 — Checkpoint before work; reconcile unknown outcomes

- `implement/queue` reads the eligible work. Before effects, `implement/begin`
  persists the exact Task, attempt number, session and checkpoint. A later
  `implement/report` records `SUCCEEDED`, `BLOCKED`, or `UNKNOWN`.
- There is one unresolved Task execution for the composed worktree. It blocks
  another begin and verification across Features/selections. A different
  session cannot repeat or report it.
- A missing or unknown result requires `resume` with the exact Task,
  `COMPLETE` or `INCOMPLETE`, and an evidence reference. Do not rerun an
  unresolved attempt blindly. A checkpoint or report is not proof of a passing
  criterion or Feature closure.

### M5 — One admitted same-revision writer across processes

- Each read/modify/publish continuity transaction is serialized by the
  root-keyed persistence lock. The process lock uses `msvcrt.locking` on
  Windows and `fcntl.flock` on POSIX, alongside an in-process reentrant lock.
  The lock file is in the platform temporary `evospec-state-locks` directory;
  it is coordination, not an OS security boundary or a distributed lock.
- The current state and expected revision are checked while holding the lock.
  Two independent processes submitting the same revision therefore produce
  one committed next revision and one `STALE_REVISION`, not two accepted
  mutations. The process regression is
  [`test_separate_processes_admit_only_one_same_revision_writer`](../tests/test_continuity.py);
  it uses spawned interpreters, a ready signal and a shared start barrier.
- This excludes competing state publications; it is not a scheduler and does
  not serialize arbitrary filesystem effects performed outside this protocol.

### M6 — Criterion checks, guarded closure, and history

- Supported checks are `python-unittest` targets matching `tests.<module>`
  and Node test files matching
  `tests/<segment>(/<segment>)*.test.mjs`. In the OpenCode path the plugin
  builds an argument vector for the allow-listed target (no free-form shell
  command), requests ordinary host permission, checks that the
  revision/criterion is still current, runs the check, then records its actual
  exit code and output digest.
- A check receipt belongs to the current criterion digest. Editing a criterion
  or test target makes only that criterion's earlier receipt stale. A passing
  check is criterion evidence; it is not Feature acceptance, publication, or
  human identity.
- `close` is an explicit mutation. Core requires every current criterion to
  have a fresh passing check and every Task to be complete; unresolved work or
  a pause blocks it. `change` can amend a Feature or create a short
  `CORRECTION` Feature. Reopening a closed Feature advances its cycle and
  preserves the old closure and check history.
- AC0 closure is not Living Spec publication or release; those remain separate
  workflow authorities.

### M7 — Durable pause, detour, and recoverable publication

- A pause is durable bookkeeping, not cancellation. One Feature may be paused
  at a time. A related or external detour records its kind, title, return Task
  and a required result on resume; unrelated changes are preserved.
- A commit journals its candidate state, intended Markdown bytes, prior
  digests, and intended digests before document publication. Each managed file
  is atomically replaced. After interruption, `resume` accepts a destination
  only if it matches either the journal's old or intended digest; if neither
  matches, Core returns `DOCUMENT_DIVERGED` and requires manual reconciliation.
- If recovery is incomplete or publication durability is uncertain, reread
  status and reconcile; do not infer success or retry a write from an
  interrupted tool result.

## OpenCode commands and protocol

The packaged OpenCode V2 integration exposes the dedicated
`evospec_continuity` tool and twelve composed commands:

| Command | Core operation | Use |
| --- | --- | --- |
| `/evospec.specify` | `specify` | Opt in on a fresh root or add a Feature in composed mode. |
| `/evospec.clarify` | `clarify` | Record a question and its decision. |
| `/evospec.plan` | `plan` | Record the selected Feature's plan. |
| `/evospec.tasks` | `tasks` | Read the queue or add a Task linked to current IDs. |
| `/evospec.implement` | `implement` | Queue, checkpoint before effects, and report afterward. |
| `/evospec.use` | `use` | Read or change session/worktree Feature selection. |
| `/evospec.change` | `change` | Amend a Feature or create a correction. |
| `/evospec.status` | `status` | Read the composed projection; legacy fallback stays read-only. |
| `/evospec.verify` | `verify` | Run and record one criterion's supported check. |
| `/evospec.pause` | `pause` | Persist a pause, optionally with a detour/return Task. |
| `/evospec.resume` | `resume` | Resume, recover a pending publication, or reconcile an attempt. |
| `/evospec.close` | `close` | Request closure when Core's current guards pass. |

All operations use the same provider-neutral request envelope:

```json
{
  "action": "continuity",
  "operation": "status",
  "expected_revision": null,
  "session_id": "<host-supplied session>",
  "data": {}
}
```

For a mutation, supply the revision just read; stale revisions fail rather than
being silently replayed. OpenCode supplies the session identity. Empty
`status`, `tasks`, and `use` calls are read-only; selection with a Feature ID
is a mutation. Mutating operations and checks pass through the dedicated
adapter's ordinary OpenCode permission boundary. Permission is independent of
Feature acceptance and does not establish that a permission dialog appeared or
that a human approved it.

## Bounds and operational limits

- State schema is exactly `1`; `.evospec/continuity.json` is limited to 4 MiB
  on recovery. Each managed Markdown document is limited to 512 KiB.
- The model allows up to 1,000 Features, 1,000 Tasks per Feature, 200 criteria
  per Feature, and 100 Increments per Feature. `clarify` rejects an append when
  the existing decision list already has 1,000 entries; `pause`/`resume` detour
  entries also use that list but have no separate count cap. Each Feature needs
  at least one criterion and one Task.
- Text limits are UTF-8 byte limits: Feature title 2,048; outcome 8,192;
  criterion description 2,048; plan 16,384; Task title 2,048 and description
  4,096; clarification question/decision 2,048; Task checkpoint 2,048 and
  report 4,096. Newline, carriage return, and tab are accepted only for
  explicitly multiline fields; NUL and unsupported controls are rejected.
- History is retained; the implementation defines no pruning, archive, or
  schema migration operation for this MVP. The 4 MiB recovery bound is not a
  guarantee of indefinite growth: when the record exceeds it, recovery fails
  closed rather than silently truncating history.
- The OpenCode verifier accepts only the two declared check kinds and their
  constrained target forms. A check is tied to current Core context and
  ordinary host permission; it is not a sandbox against arbitrary same-user
  code.

## Implementation map

- Provider-neutral dispatch: [`agent_protocol.py`](../evospec/agent_protocol.py)
  and [`agent_protocol_schema.py`](../evospec/agent_protocol_schema.py).
- Application and Core boundary:
  [`workflow.py`](../evospec/workflow.py) →
  [`services.py`](../evospec/services.py).
- Composed transition, projection, and state validation:
  [`continuity.py`](../evospec/continuity.py),
  [`domain.py`](../evospec/domain.py), and
  [`lifecycle.py`](../evospec/lifecycle.py).
- Canonical Markdown codec and transactional persistence:
  [`continuity_documents.py`](../evospec/continuity_documents.py) and
  [`persistence.py`](../evospec/persistence.py).
- Packaged OpenCode route and user guidance:
  [`plugin`](../evospec/resources/opencode/plugins/evospec/index.ts),
  [`skill`](../evospec/resources/opencode/skills/evospec/SKILL.md),
  and [`continuity tests`](../tests/test_continuity.py).

The Core/unit and adapter tests provide automated implementation evidence.
They do not establish successful CI for this change, live OpenCode UI behavior,
independent native permission qualification, or a published release. The
integration boundary and outstanding qualification are tracked in
[`opencode.md`](opencode.md).
