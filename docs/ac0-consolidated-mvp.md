# AC0 — Composed continuity MVP contract

This document describes the source-implemented, opt-in composed workflow for
tracking Features, optional Increments, Tasks, checks, pauses, and recovery in
an OpenCode V2 worktree. It is a product contract, not a runtime project
record. It records the source implementation and acceptance areas from the
[original #72 contract](https://github.com/Edulynch/OpenCode-EvoSpec/issues/72);
it does not own consumer Feature or Task state. See
[OpenCode V2 integration](opencode.md) for the host boundary.

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
| A multi-document update can be interrupted between file replacements. | Core records a bounded pending journal before publishing Markdown. Recovery preflights the journal read set and every destination before replacing documents, resumes only missing writes, and stops on any third version. A second read-set check runs before state publication. |
| Thread-only synchronization would not coordinate separate host processes, and an editor does not honor Core's lock. | Continuity operations hold the root-keyed shared persistence lock across load, validation and commit, with an expected-revision check under that lock. Core compares observed document digests before journaling and during recovery; a noncooperating editor can still race the final check/replace boundary, so any detected conflict remains journaled for inspection rather than being overwritten. |

**Why not add another writer:** the OpenCode adapter and commands are request
surfaces, not state owners. The single composed entry point reuses Core's
revision and publication boundary; adding a direct JSON/Markdown writer would
make those paths disagree about authority, recovery, and stale requests.
Composed persistence is a distinct schema from legacy `state.json`, but it is
still reached through the Core facade.

## Product mini-spec: M1–M7

These names and order preserve the seven acceptance areas in [#72](https://github.com/Edulynch/OpenCode-EvoSpec/issues/72).
They are not seven executed increments, and the deterministic evidence below
does not establish that every user journey has passed in a live OpenCode host.

### M1 — Command and natural-language entry; read purity

- The packaged integration provides the `/evospec.*` commands and skill; both
  guide requests through the composed Core operations. The intended user
  experience does not require a fixed command sequence.
- `status`, `tasks`, and `use` without an argument are reads. The Core fixture
  [`test_specify_writes_stable_composed_markdown_and_reads_without_writing`](../tests/test_continuity.py)
  confirms that these reads leave fixture files byte-identical.
- That unit test does not establish live natural-language interpretation or
  host behavior. Selecting a Feature with `use <id>` is a mutation, not a
  read-only status request.

### M2 — Three successive Increments under dynamic `implement --all`

- The acceptance scenario is one `--all` request that advances through three
  successive Increments, asks Core for the next eligible Task after each
  checkpoint/result, and does not duplicate attempts. There is no fixed
  three-Task/list cap, and `--all` does not imply closure.
- The packaged command and skill describe that loop. The deterministic Core
  fixture [`test_more_than_three_dynamic_tasks_and_worktree_session_selection`](../tests/test_continuity.py)
  creates three Increments and grows the Task list to seven; it does **not**
  execute three successive Increments through the packaged `--all` loop.
  Seven Tasks are not evidence of three completed Increments, and live
  `--all` behavior is not demonstrated.

### M3 — In-work related visual request, bounded external detour, and affected evidence

- During one active mandate, the acceptance fixture injects the actual related
  request “Antes cambia el botón de agregar a azul y luego sigue” at the exact
  pending Task. It durably pauses before changing the app source, checks the
  exact CSS property, records the result, resumes that same Task, and refreshes
  affected evidence. This visual request is not an accepted Increment.
- A separate external correction remains a distinct detour, with its own
  observed file effect, exact return Task, and affected-evidence refresh.
- Core records a durable pause and detour with a required return Task/result;
  an explicit pause remains paused until explicit resume, and a pending
  detour cannot be nested. This is bookkeeping, not cancellation or a sandbox
  that constrains arbitrary effects during the detour.
- The deterministic
  [`external-return fixture`](../tests/test_continuity.py) checks preservation
  of an unrelated fixture file and explicit return. It is not a live visual
  edit or host interruption test. The verifier now captures Core-owned input
  snapshots around the allow-listed check and Core admits a receipt only when
  those snapshots still match the current input scope. This is implemented
  freshness evidence, not a live visual edit or host interruption result.

### M4 — Fresh-session recovery of an unknown outcome before replay

- After a possible effect with no recorded result, a new session must inspect
  the unresolved attempt and reconcile the exact Task as `COMPLETE` or
  `INCOMPLETE` with an evidence reference before any replay.
- The supported protocol persists `implement/begin` before effects, leaves one
  unresolved worktree execution, blocks another begin, and requires explicit
  `resume` reconciliation. The deterministic
  [`checkpoint/unknown-outcome fixture`](../tests/test_continuity.py) verifies
  that a new session can read the attempt and cannot begin it again.
- The fixture is synthetic and begins with the checkpoint already persisted.
  It does not qualify a real host crash after an effect but before that
  checkpoint became durable; effects outside the supported begin-before-effect
  boundary are not detected automatically.

### M5 — Respect host denial/revocation; never fabricate closure

- A host denial, cancellation, or revoked permission is a stop, not a reason
  to retry through another path. A permission request or Core result is not
  proof that a human approved it.
- Core `close` is explicit and is blocked by a paused Feature, unresolved
  attempt, incomplete Task, or criterion without a current passing check.
  Deterministic Core tests exercise those guards; they do not prove live host
  denial/revocation behavior or create permission evidence.

### M6 — Verify, close, and reopen with preserved history

- Verification is criterion-scoped; closure requires all current criteria to
  pass and all Tasks to be complete. Reopening advances the cycle and retains
  the prior closure and check history. A check, closure, and release remain
  distinct authorities.
- [`test_criterion_checks_gate_close_and_reopening_preserves_historical_closure`](../tests/test_continuity.py)
  covers the Core fixture path. Core input-snapshot fixtures also verify that
  changed implementation or test bytes, mismatched before/after snapshots,
  and a Task no longer linked to the criterion cannot produce a fresh check.
  The packaged adapter obtains both snapshots from Core, binds the check to
  the linked Task, and submits neither caller-provided snapshots nor a
  model-reported result.

### M7 — Direct correction without an artificial Slice

- A bounded correction can be recorded directly as a `CORRECTION` Feature
  with no Increments, instead of creating a Slice only to satisfy a workflow
  ritual.
- [`test_plain_pause_resume_keeps_direct_correction_without_a_false_detour`](../tests/test_continuity.py)
  checks that direct Core path. It is deterministic fixture evidence, not a
  claim about every natural-language route in a live host.

## AC1 — Mandate-bound composed continuity

AC1 extends the existing schema-1 continuity writer; it does not add a second
workflow engine, writer, scheduler, permission store, or per-increment schema.
An explicit `implement --all` starts one revision-bound Core mandate with a
stable ID, Feature ID, mode `ALL`, the accepted criterion and Increment IDs
plus their current definition digests, source/recorded revisions, originating
session, and an explicit `close_feature` flag (default `false`). The mandate
is persisted through the existing transaction/journal before any task effect.
Queue, begin, report, verification, scoped Task additions, detours, and recovery
must carry that exact mandate ID while it is active. A Task attempt started
under a mandate also records the mandate ID. Existing unmandated schema-1
projects remain readable and retain their established behavior.

The read-only mandate projection reports each bound criterion/check, Increment
coverage and its Tasks, Task status/eligibility, missing Task IDs for an agreed
Increment, the next eligible Task, active checkpoint and attempt history. It
reports `FINISHED` only when every bound criterion has a current passing check,
all scoped Tasks are complete, and every bound Increment has Task coverage and
is finished; empty work is not completion. If accepted criterion/Increment
definitions change, the old mandate becomes `STALE_SCOPE`; only after explicit
Markdown reconciliation may a new mandate replace it by naming the exact old
ID. A missing Task in an already-bound fourth (or later) Increment can be
added using the same mandate ID. `close_feature` defaults false; `--all` never
closes automatically, and `/evospec.close` remains a separate explicit Core
operation subject to current checks.

Task refinements/additions continue through the existing Core task operation
or explicit Markdown reconciliation, preserving stable IDs and dependency
validation. Core may record reasoned `BLOCKED`, `CANCELED`, and `REOPENED`
events against the same Task ID with a reason and evidence reference. A
canceled Task is not successful. Reopening preserves prior attempts/checks but
requires a new checkpointed attempt and fresh linked check. An unresolved
checkpoint must be explicitly reconciled before a Task lifecycle transition;
a new session cannot retarget or report the original attempt.

The packaged `/evospec.implement`, `/evospec.tasks`, `/evospec.resume`, and
skill instructions describe the same loop and mandate ID propagation. Related
and external detours continue to use existing pause/resume records: one
outstanding detour at a time, exact return Task, an observed result, and no
automatic host-cancellation inference. Criterion receipts remain bound to
current Core snapshots, and repository-input changes require refreshing
affected evidence.

Deterministic Core regressions cover mandate creation/digests, the fourth
Increment Task added later under the same ID, stale scope and exact replacement,
task block/cancel/reopen and fresh-check requirements, and fresh-session
unknown-outcome recovery without replay. The related-mandate regression
[`test_related_mandate_detour_pauses_before_effect_and_returns_exact_task`](../tests/test_continuity.py)
checks the durable pre-effect pause, blocked begin, result recording, affected
check refresh, unchanged accepted scope, and exact return Task. The packaged
integration fixture simulates one `--all` entry through the real packaged
adapter, provider-neutral protocol, and Core against temporary application
files; simulated host permission/session APIs and Node VM tests exercise
create/list, persistence, completion, filtering, a functional delete-completed
fourth Increment, the separate related blue-button request, an isolated
external detour, and fresh-session interruption recovery. The fixture checks
app store functions and button source, not browser rendering.
This is composed application and adapter evidence only. It does not prove a
live OpenCode session, native permission dialog, human approval, browser
rendering, or authenticated identity.

## Cross-cutting mode, document, and persistence contract

- `specify` is the explicit opt-in for a fresh root. A legacy
  `.evospec/state.json` blocks composed mode and a composed marker blocks
  legacy recovery; there is no automatic migration, mode switch, or mixed
  authority. Managed paths are repository-bound and reject symlink/escaping
  targets.
- `.evospec/continuity.json` (schema 1) owns operational state: revision,
  Feature directory/status/cycle, selection, Task-attempt state/history,
  active checkpoint, checks, close/reopen history, pauses, stable IDs,
  the optional current implementation mandate and Task lifecycle-event history,
  semantic and byte digests, typed identity metadata, and any pending
  publication journal. It does not duplicate current Feature/plan/Task
  definitions.
- Ordinary Markdown is the direct human authority under
  `specs/README.md` and `specs/NNN-<slug>/{spec,tasks}.md`, with `plan.md` and
  `decisions.md` only when useful. New documents use headings, prose, list
  fields and small stable-ID markers, not a fenced full-JSON authority block.
  Existing JSON-in-Markdown documents are legacy input accepted only by the
  explicit, permissioned `upgrade_markdown` transition.
- A Feature requires at least one criterion and Task and may have up to 100
  ordered optional Increments. Tasks link to current criteria and may link to
  an Increment and earlier Tasks as dependencies. Selection is session-local
  with a worktree default; it does not create an independent execution slot.
- `status` and `tasks` parse without writing. The projection reports semantic
  edits, formatting/notes drift, generated-checklist drift, and
  identity/reference conflicts separately. A semantic edit or identity
  conflict is never projected as accepted Feature state or evidence. A changed
  checkbox is only generated-region drift; it never records Task progress.
- Adopting human definitions requires explicit `reconcile` at the current
  revision and exact Feature ID. Existing IDs are immutable. New
  criterion/increment/Task/decision declarations use typed aliases such as
  `C-new-filter` or `T-filter-task`; linked references must be complete and
  unambiguous. Removals require the exact `retire_ids` set and IDs are never
  reused. Semantic edits to a closed Feature require
  `reopen_feature_id` equal to that Feature ID and append normal reopen
  history. Read-only status never triggers reconciliation.
- A clean schema-1 JSON-in-Markdown fixture can transition only on explicit
  `upgrade_markdown`. It refuses dirty or mixed document formats and never
  migrates legacy `.evospec/state.json`. It preserves attempts, checkpoints,
  checks, decisions and closure history. Historical checks lacking a
  task-definition digest remain historical and not fresh; reverify before
  relying on them.
- The README Feature index and the Tasks checklist are generated projections;
  index replacement preserves bytes outside its marker block. Core uses the
  same root lock and journal for Markdown and the operational index. When a
  detected editor race conflicts with a journal read set, no state revision
  is published and manual reconciliation is required. Filesystem editors do
  not participate in the lock: an edit racing after the final recheck may be
  observed only on the next read, where digest validation fails closed.
- Each read/modify/publish continuity transaction holds the root-keyed shared
  persistence lock across load, validation, and commit. The process lock uses
  `msvcrt.locking` on Windows and `fcntl.flock` on POSIX alongside an
  in-process reentrant lock. Its lock file is in the platform temporary
  `evospec-state-locks` directory. It coordinates protocol writers, not
  arbitrary filesystem effects, a scheduler, or a distributed lock. The
  [`separate-process regression`](../tests/test_continuity.py) checks that two
  same-revision writers produce one commit and one `STALE_REVISION`.
- A commit journals candidate state and intended document bytes/digests before
  publication. The exact serialized state and pending journal each fit the
  4 MiB recovery bound; writes preflight their serialized size. Recovery
  checks the serialized journal and final state, then validates every
  destination against its prior or intended digest before replacing any
  document. It resumes only missing writes and stops with
  `DOCUMENT_DIVERGED` on a third version. For a conflict, read-only `status`
  returns `publication_recovery` with an opaque `pubres-v1-<sha256>` token,
  the complete exact `conflict_paths`, and previews for at most eight paths
  (each capped at 4096 characters, with truncation and omission counts). Plain
  `resume` never discards a third version. Only an explicit user choice to
  preserve current bytes may use permissioned `resume` at that exact status
  revision with both `publication_resolution` and the full exact unique
  `preserve_current_paths` list (1–4001 paths). Core rechecks the token and
  refuses a preserved document if queued IDs, links, or immutable decisions
  would be lost; merge queued canonical Markdown first and refresh status.
  Successful preservation drops only those paths from the pending write set
  and retries remaining old/intended writes. It is not semantic acceptance,
  verification, Task completion, or Feature closure; refresh status and
  separately reconcile an explicitly requested human definition change with
  the exact Feature ID. Never edit the operational JSON or infer/guess a token.
  Each managed Markdown write is limited to 512 KiB. These Core recovery
  behaviors and the adapter's ordinary permission route have deterministic
  tests; they do not establish live OpenCode permission or native-host
  qualification.

## OpenCode commands and protocol

The packaged OpenCode V2 integration exposes the dedicated
`evospec_continuity` tool and twelve composed commands:

| Command | Core operation | Use |
| --- | --- | --- |
| `/evospec.specify` | `specify` | Opt in on a fresh root or add a Feature in composed mode. |
| `/evospec.clarify` | `clarify` | Record a question and its decision. |
| `/evospec.plan` | `plan` | Record the selected Feature's plan. |
| `/evospec.tasks` | `tasks` | Read the queue or add a Task linked to current IDs. |
| `/evospec.implement` | `implement` | Queue or start a revision-bound `ALL` mandate, checkpoint before effects, report afterward, and re-query scoped Tasks dynamically. |
| `/evospec.use` | `use` | Read or change session/worktree Feature selection. |
| `/evospec.change` | `change` | Amend a Feature or create a correction. |
| `/evospec.status` | `status` | Read the composed projection; legacy fallback stays read-only. |
| `/evospec.verify` | `verify` | Run and record one criterion's supported check. |
| `/evospec.pause` | `pause` | Persist a pause, optionally with a detour/return Task. |
| `/evospec.resume` | `resume`, `reconcile`, `upgrade_markdown` | Resume or recover; explicitly adopt human Markdown edits or explicitly upgrade clean legacy JSON-in-Markdown. |
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

### Verification input freshness

- `/evospec.verify` requires the current Feature, criterion, and a Task linked
  to that criterion. After the allow-listed descriptor and ordinary host
  permission gates, the adapter asks Core for `input_snapshot` at the current
  revision immediately before and after the test. It submits those Core
  snapshots with the actual process exit code and output digest; callers do
  not supply snapshots or results. Core checks both snapshots against each
  other and a fresh scan when admitting the receipt. A missing/invalid
  snapshot, input mismatch, unlinked Task, or revision drift cannot create a
  fresh passing check. The receipt records the observed input scope, Task ID,
  criterion digest, descriptor, exit code, and output digest.
- The effective `continuity-root-input-v1` scope includes regular files and
  directory entries throughout the canonical repository root, whether Git
  tracks or ignores them. It excludes root `.git` and `.evospec`,
  `specs/README.md`, exact managed-document paths in the current continuity
  state, and the directory entries for those managed Feature folders. Other
  repository inputs remain in scope. The digest binds sorted normalized
  relative file paths and content SHA-256 values, plus sorted normalized
  directory paths and presence. Core applies the base scanner's limits of
  50,000 files, 100,000 entries, 1 GiB total file bytes, and depth 128; it
  rejects unsafe entries and fails if the two scans are unstable or differ.
- This is trusted-local operational freshness evidence, not an OS sandbox or
  proof that arbitrary test code has no hidden dependency or read.

## Bounds and operational limits

- State schema is exactly `1`; the serialized state and pending journal are
  each limited to 4 MiB on write and recovery. The reader reads at most the
  bound plus one byte to reject oversize input. Each managed Markdown write is
  limited to 512 KiB.
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
  schema migration operation for this MVP. The 4 MiB bound is not a guarantee
  of indefinite growth: over-bound writes are rejected and recovery fails
  closed rather than silently truncating history.
- The OpenCode verifier accepts only the two declared check kinds and their
  constrained target forms. A check is tied to current Core context, linked
  Task, Core input snapshots, and ordinary host permission; it is not a
  sandbox against arbitrary same-user code.

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

The audited source commit
[`b4c6090`](https://github.com/Edulynch/OpenCode-EvoSpec/commit/b4c609039863fd3a5a018a886ccfa9409e0b3c7c)
had a successful hosted push CI run
([45733307123](https://github.com/Edulynch/OpenCode-EvoSpec/actions/runs/45733307123));
a separate PR-event result was not observed. That run applies to that exact
commit, not later working-tree changes. The reported OpenCode v2.4.1 E2E was
blocked because the local service `/api/info` returned
`Authentication required`; the Ubuntu real-browser gate was blocked for lack
of a verified process boundary, and Linux/Windows runner smokes were omitted.
These deterministic and hosted results do not establish live M1–M7 success,
native permission qualification, or a published release. The integration
boundary and outstanding qualification are tracked in [`opencode.md`](opencode.md);
the evidence summary is in [#72 comment 7179636311](https://github.com/Edulynch/OpenCode-EvoSpec/issues/72#issuecomment-7179636311).
