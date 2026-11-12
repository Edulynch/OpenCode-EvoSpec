# Implementation Plan: EvoSpec Core Bootstrap

## 1. Scope and outcome

Implement the smallest importable Python core that gives a repository a lazy EvoSpec artifact area, one authoritative recoverable working state, immutable material history, and a domain independent model for features, changes, decisions, observations, and Living Specs.

This plan implements the observable behavior in [spec.md](spec.md). It does not implement commands, integrations, agent behavior, project discovery, semantic lock enforcement, ChangeBudget, or version control operations.

## 2. Governing inputs and decisions

The active specification is `specs/001-core-bootstrap/spec.md`. The design also follows:

* `.specify/memory/constitution.md`, especially Living Product Truth, Active Feature Continuity, Persistent State Outside the LLM, Product Truth and Runtime Truth, Disposable Experiments, Tool Responsibility Separation, Agent Independence, Deterministic Testability and Recovery, and Third Party Provenance.
* `README.md`, which defines EvoSpec as domain agnostic, keeps artifacts proportional to complexity, and treats `.specify` and future `.opencode` integrations as development context rather than product runtime.
* `ROADMAP.md`, where this work closes the Phase 0 core state, Living Spec, repository structure, and lifecycle foundations needed by later phases.
* `THIRD_PARTY_NOTICES.md`, which confirms EvoSpec's independent identity and requires provenance for later third party source additions. No Spec-Kit source or template is copied.

### Language and dependency decision

Select CPython 3.11 or newer as the single core language. The runtime uses only the standard library: `pathlib` for repository-relative path handling, `json` for the persisted interchange format, `tempfile` for same-directory temporary files, `os.replace` for replacement, `os.fsync` for best-effort durability, `argparse` only if a future command layer needs argument parsing, and `unittest` for tests. Core Bootstrap itself exposes no command line interface.

TypeScript with Node.js was considered. Node has equivalent filesystem primitives and a built-in test runner, and TypeScript has strong type checking. It also introduces a compiler, module configuration, package metadata, and a JavaScript runtime/toolchain boundary for a core whose required behavior is small and file based. A hybrid would duplicate schemas and increase serialization and release failure modes without satisfying a current requirement. Therefore the plan rejects TypeScript, Node, and a hybrid for this feature.

Current primary references:

* [Python `pathlib`](https://docs.python.org/3.11/library/pathlib.html), [`json`](https://docs.python.org/3.11/library/json.html), [`tempfile`](https://docs.python.org/3.11/library/tempfile.html), [`os.replace` and `os.fsync`](https://docs.python.org/3.11/library/os.html), [`argparse`](https://docs.python.org/3.11/library/argparse.html), and [`unittest`](https://docs.python.org/3.11/library/unittest.html).
* [Node.js `fs`](https://nodejs.org/api/fs.html), [`path`](https://nodejs.org/api/path.html), and [Node.js test runner](https://nodejs.org/api/test.html).
* [TypeScript handbook](https://www.typescriptlang.org/docs/handbook/intro.html).

### Packaging decision

The minimal root layout is:

```text
pyproject.toml
evospec/
    __init__.py
    domain.py
    lifecycle.py
    persistence.py
    services.py
    ports.py
tests/
```

`pyproject.toml` contains project identity, the supported Python requirement, and no dependencies or entry points. It intentionally has no build backend. Core Bootstrap is importable from a checkout, but this plan makes no promise that `pip install`, wheel creation, or sdist creation works. `.specify/` and `.opencode/` remain outside the runtime package.

## 3. Architecture

`domain.py` contains immutable, typed conceptual values and records: repository context, Living Spec publication, feature, change, decision, observation, one canonical `WorkingContext`, checkpoint, review result, verification evidence, and references. It owns no filesystem access.

`lifecycle.py` contains the lifecycle states, persisted phases, allowed transitions, phase to lifecycle mapping, terminal-state rules, and validation of exactly one current state and one current phase. It also defines future classification values `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, and `EXPERIMENT` as optional internal metadata. Users never need to select one.

`persistence.py` owns the artifact layout, strict decoding, deterministic encoding, cross-platform atomic publication, revision checks, and recovery-safe reads. It is the only module that reads or writes `.evospec`.

`services.py` validates and persists caller-supplied state transitions, checkpoints, immutable record creation before state publication, side-change context push and restoration, explicit product truth decisions, drift observations, repair limits, and read-only inspection. It must not invoke agents, run commands, perform workflow work, manage locks, or own change-scope policy.

`ports.py` defines narrow protocols for persistence and service boundaries. Protocols describe the core's needs and return domain values. They are extension points for tests and later adapters, not a plugin registry, discovery mechanism, or plugin system.

`__init__.py` exposes only the small public import surface needed by callers. It must not perform repository discovery, create `.evospec`, or import optional integrations.

## 4. Lazy artifact layout and authority

The repository root is supplied explicitly by the caller. The core uses stable IDs and repository-relative POSIX paths. Records contain no agent IDs, model IDs, conversation IDs, host-specific absolute paths, or timestamps as identity.

`.evospec/` is created lazily. Its permitted shape is:

```text
.evospec/
    state.json
    living-specs/<living-spec-id>/<publication-revision>.md
    records/
        changes/<id>.json
        decisions/<id>.json
        observations/<id>.json
```

`state.json` is created when working state is established. A Living Spec publication exists only after a deliberate product-truth update and is written once at `living-specs/<living-spec-id>/<publication-revision>.md`; publication revisions are never modified in place. A record file exists only when material history is created. Empty directories are not required.

`state.json` is the sole mutable active and recovery authority. It contains:

* schema version and monotonic revision;
* zero or one primary feature;
* active canonical `WorkingContext`, including its current action;
* an optional preserved canonical `WorkingContext` while side work is active;
* current lifecycle state and persisted phase;
* checkpoints;
* applicable decisions and constraints;
* latest review verdict and compact findings;
* verification evidence and sufficiency;
* repair count and resume point;
* references to immutable change, decision, observation, and Living Spec publication records, including the current authoritative publication for each related Living Spec.

`state.json` is the single mutable recovery and product-truth authority. A `LivingSpecPublication` contains the stable Living Spec identity, immutable publication revision, capability or feature relationship, repository-relative publication path, and any prior-publication reference needed for traceability. Updating product truth first writes a new immutable publication, then atomically replaces `state.json` with its reference. A publication file is not authoritative merely because it exists: recovery resolves current product truth directly from the successfully published `state.json` reference, never by replaying records or examining directory order, filenames, timestamps, or Markdown contents. If publication fails before state replacement, the prior state reference and prior publication remain authoritative. A valid unreferenced publication may remain as an orphan for later cleanup, but recovery and inspection ignore it. Immutable history records follow the same write-before-state-publication rule.

## 5. Persistence and recovery semantics

All JSON is UTF-8, uses stable key order, strict JSON with no non-finite numbers, and ends with one final newline. Encoding is centralized so every record and state write has identical behavior. The caller supplies a repository root as a platform-native `pathlib.Path`: Windows drive paths, backslash separators, relative inputs, absolute inputs, and UNC/network-style inputs are interpreted by the host platform's `pathlib` rules. The core resolves a relative root against the caller's current working directory, rejects a root that cannot be resolved or accessed, and derives every persisted target from that root. Stored artifact references are validated repository-relative POSIX paths and cannot escape that resolved root; host-native absolute paths and separators are never persisted as identity.

One shared atomic-publication primitive writes every mutable target, immutable record, and immutable Living Spec publication. It creates a uniquely named temporary file in the destination directory, so source and target remain on the same filesystem and Windows can close the temporary handle before replacement. It writes complete bytes, flushes the Python file object, calls `os.fsync()` on the temporary descriptor when supported, closes the descriptor, and publishes only through `os.replace(temp_path, target_path)`. `os.replace` is the logical publication boundary: before it succeeds, the old target remains authoritative; after it succeeds, readers observe the complete old or new target, never a partial target.

Before replacement, any write, flush, file-fsync, close, or replacement failure leaves the prior target authoritative and triggers best-effort temporary-file cleanup. A failed replacement never mutates the old target. After successful replacement, cleanup failure is non-fatal and must not reclassify a committed publication as failed. The primitive attempts a parent-directory `fsync` only where a directory descriptor and `fsync` are supported (normally POSIX); it is best effort, not universally available or required for logical recovery. If this optional post-replacement operation fails, publication remains committed but directory-durability confirmation is unavailable. The plan makes no universal power-loss durability claim, no filesystem lock claim, and no multi-writer guarantee.

Reads reject missing required fields, invalid types, malformed JSON, unknown schema versions, illegal lifecycle and phase combinations, multiple primary features, terminal changes left as unfinished current work, a missing or invalid authoritative Living Spec publication reference, and references that violate the model. A read failure returns a typed recovery error or a safe inspection result that clearly reports recovery as unavailable. It must not fabricate progress, silently migrate an unknown schema, or infer product truth from unreferenced publication files.

There is no concurrent writer support. The documented operating model is one writer. Every state mutation accepts the expected revision and rejects a stale revision before publication. This prevents a stale single writer from overwriting a newer state, but it is not a lock or a multi-writer protocol.

## 6. Lifecycle, phase, and state rules

Implement exactly these lifecycle states: `PROPOSED`, `CLARIFYING`, `READY`, `IN_PROGRESS`, `PAUSED`, `VERIFYING`, `ACCEPTED`, `REJECTED`, and `ABANDONED`. Only `ACCEPTED`, `REJECTED`, and `ABANDONED` are terminal. The transition table in `spec.md` is authoritative and must be encoded as data or explicit validation, not inferred from display text.

Persist exactly these phases: `RESEARCH`, `CLARIFY`, `READY`, `IMPLEMENT`, `REVIEW`, `VERIFY`, `CONVERGE`, and `DONE`. Enforce this mapping: `RESEARCH` under `PROPOSED`; `CLARIFY` under `CLARIFYING`; `READY` under `READY`; `IMPLEMENT` and `REVIEW` under `IN_PROGRESS`; `VERIFY` and `CONVERGE` under `VERIFYING`; and `DONE` under `ACCEPTED`. `PAUSED` retains its exact valid non-terminal state/phase pair; `REJECTED` and `ABANDONED` retain their final valid phase for traceability and cannot resume. A change has exactly one current lifecycle state, phase, and action.

An unfinished change persists completed work, current work, pending work, latest caller-supplied durable review verdict and findings, verification evidence and sufficiency, repair count, and resume point. `READY` and `IN_PROGRESS` are forbidden while a required material decision remains unresolved. A behavior-producing change must pass through `VERIFYING` before acceptance. A caller may record `CONVERGE` only with `PASS` or a safe nonblocking `WARN` and sufficient verification.

When a caller records an implementation alteration after a review or verification checkpoint, the core invalidates the affected checkpoints. The next caller-supplied review and verification must establish fresh checkpoints. Caller-supplied repair is at most two iterations, with each review checkpoint and count persisted. At exhaustion, the caller records `PAUSED`, a blocker, and a safe next step; Core Bootstrap never initiates repair or changes a lifecycle state autonomously.

An explicit read-only inspection boundary exposes the durable review view but cannot advance lifecycle or phase, repair, accept, alter product truth, or write state. Review remains holistic and considers intent, active and side work, Living Specs, requirements, decisions, constraints, Constitution, future locks when present, actual changes, evidence, product and runtime truth, unsolicited scope, pending work, state, and future ChangeBudget metadata. The core stores the relationship points but does not enforce locks or ChangeBudget.

## 7. Side changes, truth boundaries, and future relationships

`WorkingContext` is the one canonical representation for both active and restorable work. It contains, as applicable: feature identity; current change identity; lifecycle status and persisted phase; classification; current action and focus; completed work, current work, and pending work; accepted clarification and applicable-constraint references; review verdict, findings, and checkpoint references; verification state, evidence metadata, and sufficiency; convergence state; repair-attempt count; resume point and any other recovery/checkpoint information; and references to authoritative Living Spec publications and immutable records needed to recover the work. It does not duplicate global repository state.

Before activating unrelated work, persist the complete active primary `WorkingContext` verbatim as `preserved_primary_context`, then replace the active context with the side-change `WorkingContext`. The side change references the preserved primary feature and has its own identity and lifecycle. Restoration makes the serialized preserved context active verbatim: it performs no merge, recomputation, defaulting, lifecycle or phase advancement, checkpoint invalidation, field omission, or progress invention. While side work is active, the preserved context is the single source for the primary identity; otherwise the active context is. The implementation rejects a mismatched side-change linkage or incomplete context. The current specification requires independently identifiable side changes but not nested side changes, so the plan does not introduce a context stack.

Living Spec publications are accepted product truth for one capability only when the authoritative `state.json` reference selects them. A caller may record an accepted change's product-truth update only at an explicit product decision during convergence, using immutable publication-before-state ordering. Runtime, test, implementation, and experiment observations are separate records. Drift is surfaced as an unresolved comparison between expected and observed truth. Rejected experiments remain knowledge and do not update the Living Spec. An experiment requires an explicit accepted or rejected outcome.

Decisions include accepted material clarifications and user constraints, their scope, affected feature or change, current or superseded status, and a reference to the immutable record. Supersession preserves the old record and makes only the newer decision current. The model includes optional relationship points for future lock metadata describing protected aspects, mutable aspects, allowed operations, invariants, and rationale. It stores no lock enforcement logic.

Product intent, Living Specs, decisions, continuity, change scope, implementation details, and repository history remain separate. Future classifications and granular locks are metadata relationships only. They are future-only classifications and relation points, not user prerequisites or active policy.

## 8. Test strategy

Use only `unittest`. Tests should construct temporary repositories with `tempfile`, inspect bytes and paths directly, and avoid network, subprocesses, wall-clock assertions, platform-specific fixtures, and agent or model metadata.

Required deterministic categories:

* domain construction, stable IDs, POSIX relative paths, empty versus populated state, and one-primary invariant;
* strict JSON bytes, UTF-8, stable key order, final newline, rejection of non-finite numbers, unknown schema, malformed data, invalid references, and illegal state combinations;
* lazy directory and file creation, including absence of empty Living Spec and history files;
* immutable Living Spec publication revisions, state-selected authoritative publication references, recovery rejection of missing or invalid references, and recovery ignoring unreferenced orphan publications, directory ordering, timestamps, and Markdown contents;
* atomic persistence ordering, same-directory temporary files, replacement, file flush and fsync calls through a narrow test port, POSIX-only best-effort directory fsync, no temporary-file adoption, stale revision rejection, and Windows-safe close-before-replace behavior;
* all lifecycle transitions, terminal rules, phase mapping, unresolved-decision blocking, and recovery of current action and resume point;
* checkpoint creation, invalidation after implementation alteration, holistic review verdict forms, read-only review inspection, proportional metadata, and two-repair maximum with blocked pause;
* canonical `WorkingContext` construction, complete side-change preservation, independent side lifecycle, exact restoration, multiple side changes, rejection of incomplete contexts or mismatched linkage, and no invented or lost progress;
* decision acceptance, scope, supersession, constraint availability, observation and experiment truth boundaries, explicit experiment outcomes, and drift without automatic reconciliation;
* recovery after interruption, restart simulation, missing or corrupt files, and no active work represented explicitly;
* generic domain use, agent/model independence, future classification values, future lock relation points, and absence of plugin, lock, ChangeBudget, command, server, or database behavior;
* protocol contract tests proving services depend on narrow ports rather than concrete integrations.

Failure-injection tests use filesystem-operation ports instead of destructive crash simulation. They cover write, flush, file-fsync, close, and replacement failures before publication; each preserves the preexisting target and cleans the temporary file best effort. They cover optional post-replacement directory-fsync failure as committed publication with unconfirmed directory durability, and cleanup failure after publication as non-fatal. Tests exercise Windows drive, backslash, relative, absolute, and UNC-style path parsing through platform-appropriate `pathlib` behavior without requiring a network share or a platform-specific machine-level crash fixture.

## 9. Ordered implementation sequence

1. Add `pyproject.toml` metadata only, then add tests for the supported Python declaration, no dependencies, no entry points, and the intentional absence of a build backend.
2. Add `evospec/domain.py` and `evospec/__init__.py`. Pair them with domain, identity, path, reference, and invariant tests.
3. Add `evospec/lifecycle.py`. Pair it with transition, phase mapping, terminal-state, unresolved-decision, checkpoint, repair, and classification tests.
4. Add `evospec/ports.py`. Pair it with protocol contract tests using in-memory test doubles, without introducing a plugin mechanism.
5. Add `evospec/persistence.py` after tests for lazy layout, deterministic JSON, immutable Living Spec publication-before-state ordering, state-selected authority, strict reads, same-directory atomic write, injected pre-replacement failures, optional directory-fsync behavior, stale revisions, and recovery failure.
6. Add `evospec/services.py` after tests for canonical primary state, exact side-context push and restoration, checkpoints, decisions, observations, truth boundaries, drift, read-only review inspection, and convergence guards.
7. Expand cross-module recovery tests to simulate interruption before and after state replacement, restart, orphan publication handling, side work, checkpoint invalidation, repair exhaustion, and a different caller reconstructing context and current product truth from `state.json` alone.
8. Run the complete `unittest` suite and inspect the resulting repository shape. Confirm that only deliberate `.evospec` files are created by tests in temporary repositories and that development integrations remain outside `evospec/`.

## 10. Traceability

| Plan work | Specification coverage |
| --- | --- |
| Repository layout, lazy artifacts, domain records, and references | FR-001 through FR-005, FR-049 through FR-051 |
| Authoritative `state.json`, canonical WorkingContext, exact restoration, recovery fields, and revisions | FR-006 through FR-009, FR-018 through FR-019, FR-026, FR-039 through FR-043, invariants 1 through 6, 9, and 13 |
| Immutable Living Spec publications, decisions, observations, experiments, and drift | FR-012 through FR-016, FR-044 through FR-048, invariants 7 through 9 |
| Lifecycle transitions and persisted phase semantics | FR-017 through FR-025, FR-036 through FR-037, invariants 2, 4, and 13 |
| Review, verification, convergence, checkpoint invalidation, and repair cap | FR-027 through FR-035, invariants 14 through 16 |
| Primary and side-change snapshot and restoration | FR-038 through FR-043, invariants 1 and 3 |
| Standard-library atomic publication, cross-platform persistence, and injected-failure tests | Constitution principles 2, 5, 12, 15, and 17; SC-001, SC-002, SC-004, and SC-005 |

Success criteria are exercised by recovery tests (`SC-001`, `SC-002`), decision and clarification tests (`SC-003`), truth and experiment tests (`SC-004`, `SC-005`), domain-neutral record tests (`SC-006`), and future classification and lock relationship tests (`SC-007`).

## 11. Excluded work

This feature does not create commands, integrations, plugins, locks, ChangeBudget support, servers, databases, transaction services, event logs, filesystem locks, multi-writer protocols, background services, autonomous workflow orchestration, build or deployment configuration, wheel or sdist support, project autodiscovery, repository initialization, version-control operations, vendor-specific code, agent or model adapters, automatic Living Spec discovery or reconciliation, or runtime code outside the listed package modules. It makes no unsupported universal crash- or power-loss-durability promise. It does not copy Spec-Kit materials. It does not create `tasks.md`, `.evospec/`, or any file other than the implementation files listed in this plan's future layout. The current task creates only this `plan.md`.
