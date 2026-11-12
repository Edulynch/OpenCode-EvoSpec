# EvoSpec Roadmap

This roadmap is intentionally human-readable. It answers three questions:

1. What is done?
2. What are we working on now?
3. What comes next?

## Roadmap maintenance rule

- Every completed major task or feature must update this file in the same commit/PR or immediately after.
- When the active milestone changes, update the **Current focus** section in both `ROADMAP.md` and `README.md`.
- Keep `master` current after a major task/feature is validated and integrated.
- Register new features, material scope changes, and milestone or priority changes here before implementation. Clear, bounded, low-risk corrections need only a proportional change/evidence trace and do not require a roadmap entry unless they affect a milestone or priority.

## Current focus

### E0 V2 methodology contract — prepared for product-owner review [documentation only]

The compact contract, proposed command/context boundaries, S1–S9 desk walkthroughs, premortem, and E0→E1 gate are documented in [`docs/methodology.md`](docs/methodology.md) and [`docs/e0-v2-validation.md`](docs/e0-v2-validation.md).

- **Document status:** prepared for review; this is not product-owner approval or accepted Product Truth.
- **Gate:** proposed NO-GO to E1 until the product owner explicitly reviews and approves the contract and gate.
- **Scope:** documentation only. No E1 comparison, runtime work, architecture/schema decision, or code change is included.
- The Autonomy Pass plan below is preserved as historical planning with its task checkboxes unchanged; it is not an active E0 objective.

## Historical implementation planning (paused)

### Feature 10 — Autonomy Pass / v0.6.0 [paused; historical planning]

Goal: EvoSpec should finish normal work with dramatically fewer human handoffs.

- [x] **10.1 Core Outcome Taxonomy**
  - Project deterministic `HUMAN_MATERIAL_DECISION`, `SAFE_AUTOMATIC_OPERATION`, `BOUNDED_DELEGATION`, `EXTERNAL_WAIT`, and `STRUCTURAL_DEFECT` outcomes from Core facts.
  - Preserve the exact compatibility projection: human decisions require `USER`; safe operations, bounded delegation, and waits remain `AUTOMATIC`; structural defects remain `NONE`.
  - Require structured, distinct Core-offered choices before projecting `HUMAN_MATERIAL_DECISION`; terminal contexts carry completion through existing facts with `outcome: null`.

- [x] **10.2 Autonomy Envelope**
  - Persist the work goal, allowed scope, budgets, reversible actions, external waits/retries, irreversible permissions, and stop conditions.
  - Core evaluates proposed operations against conservative, canonical-path authority without executing continuations.
  - Persist distinct monotonic metrics for material decisions, operational handoffs, and automatic/delegated operations.

- [x] **10.2.1 OpenCode V2-only Migration**
  - Replace the V1 adapter with a dependency-free OpenCode V2 local plugin, retaining the provider-neutral protocol and typed tools.
  - Retain the EvoSpec skill and exactly nine native commands; stop editing `opencode.json` and remove the obsolete ChangeBudget integration.
  - Track exact V2 resource ownership in `.evospec/opencode-resources.json` and keep install, refresh, and removal transactional.
  - Preserve unmanifested V1 installations, user resources, and foreign Spec-Kit material; do not migrate or delete V1 resources automatically.
  - Verify clean-project dependency resolution, actual OpenCode V2 plugin loading, package/native behavior, Core/autonomy tests, and basedpyright.

- [x] **10.2.2 Explicit Irreversible Authorization**
  - Persist revision-bound, one-shot Core authorization records with active Feature/Change, exact branch, base/result SHAs, identity policy, reviewed commit mapping, and purpose.
  - Keep ordinary commits, pushes, and CI distinct from prohibited force pushes, history rewrites, destructive resets, tags, and releases; recompute any authorized commit map and execute only the exact one-shot operation.
  - Verify the remote result, preserve unrelated refs, consume authority inside Core, and block concurrent Core mutations while execution is reserved.
  - Project pending and consumed authorization through the provider-neutral protocol, preserve audit history, and invalidate stale authority.

- [x] **10.2.3 Explicit Autonomy Grant**
  - Persist one exact pending envelope delta bound to the repository, active Feature/Change, and request revision; proposing it never grants authority.
  - Require a revision-bound `AUTHORIZE`, `REJECT`, or strict-subset `NARROW` decision, and retain the resolved proposal in append-only Core audit history.
  - Keep canonical path containment, explicit budgets, monotonic human-decision/handoff metrics, and hard exclusions for force/history rewrite/reset/tag/release operations.
  - Project the exact proposal and decision options through Core so material choices remain user-driven.

- [x] **10.3 OpenCode Autonomous Continuation**
  - Consume Core's structured outcome and the active Autonomy Envelope.
  - Wait for CI/Oracle/builds instead of ending the session.
  - Retry safe transient failures within policy.
  - Ask the human only for material decisions.

- [x] **10.3.1 Typed Material Clarification**
  - Require Core-selectable, durable decisions and distinct options at assessed intake; resolve only exact pending choices in stable order.
  - Preserve legacy unresolved clarifications as fail-closed until exact, revision-bound Core recovery structures their choices without answering them.

- [x] **10.3.2 Legacy Non-Material Recovery**
  - Recover an exact legacy detail explicitly confirmed as non-material by recording it as an unaffirmed Observation in the same durable Core mutation that removes the blocker.
  - Preserve auditability and autonomy state; keep remaining unstructured detail fail-closed and resume only when no material work remains.
  - Expose the distinct exact recovery through the provider-neutral protocol and typed OpenCode V2 tool without inferring classification from prose.

- [ ] **10.4 ChangeBudget Envelope Bridge**
  - Allow exact-path amendments automatically when they fit a previously authorized envelope.
  - Allow bounded numeric budget adjustments without repeated approval.
  - Handle authorized `BASELINE_HEAD_MOVED` transitions without unnecessary handoffs.
  - Represent pre-existing preserved untracked files without forcing them into feature commits.

- [ ] **10.5 Autonomy Acceptance Suite**
  - Normal feature: **<= 5 human interventions**.
  - Normal release: **1 prompt** when publication is pre-authorized.
  - Tests/builds/Oracle/CI/waits/worktrees/hashes/uploads: **0 prompts**.
  - More than 10 human handoffs for a normal feature is an autonomy failure.
  - Produce an end-of-work autonomy report.

---

## Completed features

### Feature 1 — Core [complete]

- [x] Define the project architecture and stack.
- [x] Create durable project authority.
- [x] Create feature/change lifecycle.
- [x] Add revisioned state and validation.
- [x] Add `AUTOMATIC / USER / NONE` guidance.
- [x] Add safe restart/recovery behavior.
- [x] Deliver schema 9 and safe compatibility behavior.

### Feature 2 — Work Queue [complete]

- [x] Add `ACTIVE`, `READY`, `BLOCKED`, `FROZEN`, `WAITING`, and `DONE`.
- [x] Allow blocked/frozen work without freezing the whole repository.
- [x] Model dependencies, conflicts, locks, resume points, review, and verification state.
- [x] Preserve history and resume safely after restart.
- [x] Dogfood the queue on EvoSpec itself.

### Feature 3 — Scalable Discovery [complete]

- [x] Keep repository discovery bounded and deterministic.
- [x] Avoid turning large repositories into an automatic dead end.
- [x] Add safe evidence discovery and external SDD structural awareness.
- [x] Preserve fail-closed behavior when evidence is insufficient.
- [x] Handle historical managed OpenCode resources safely.

### Feature 4 — Capability Selection [complete]

- [x] Detect `AMBIGUOUS_CAPABILITY`.
- [x] Persist structured candidate snapshots.
- [x] Add revision-bound `select_capability`.
- [x] Support existing-capability selection.
- [x] Support explicit `CREATE_STANDALONE_CAPABILITY` for `NEW`.
- [x] Reject stale, forged, replayed, malformed, or unoffered selections.
- [x] Expose the flow through the typed protocol.

### Feature 5 — Discovery Recovery [complete]

- [x] Add `DISCOVERY_RECOVERY_REQUIRED`.
- [x] Persist pending discovery recovery.
- [x] Add Core-owned `recover_discovery`.
- [x] Validate exact evidence IDs, fingerprints, limits, and provenance.
- [x] Fail closed when evidence is invalid or insufficient.
- [x] Survive restart before evidence collection.
- [x] Connect successful recovery to capability selection when required.
- [x] Prove the natural flow without manually constructing `IntentAssessment`.

### Feature 6 — Natural OpenCode Integration [complete]

- [x] Project Core-owned recovery and selection state.
- [x] Expose typed recovery/selection helpers.
- [x] Prevent the model from fabricating assessments, candidates, or evidence conclusions.
- [x] Continue automatically when Core guidance allows it.
- [x] Ask the human only for actual ambiguity/material decisions.
- [x] Keep managed OpenCode resources refreshable and fail-closed on unknown edits.

### Feature 7 — Human Dashboard [complete]

- [x] Add `evospec ui`.
- [x] Serve a loopback-only, read-only dashboard.
- [x] Add `/api/context`.
- [x] Add SSE `/api/events` with polling fallback.
- [x] Make the UI human-first: current work, blockers, features, tasks, and technical details.
- [x] Add responsive desktop/mobile behavior.
- [x] Preserve tab selection across realtime rerenders.
- [x] Package dashboard assets in installed and native distributions.

### Feature 8 — Reproducible Release / v0.5.0 [complete]

This completed product milestone is represented by the source snapshot. Prior-repository tags, release assets, CI run evidence, and commit references are not imported or claimed here.

- [x] Include reproducible build and package-metadata validation tooling.
- [x] Include Windows and Linux native build and installer sources.
- [x] Document release identity and artifact validation gates.

---

## Paused work

### Feature 9 — External Blocker Reconciliation [paused]

Goal: safely reevaluate a persisted blocker when new attributable evidence shows that the external condition changed.

- [ ] Define the blocker/evidence reconciliation model.
- [ ] Record attributable external evidence.
- [ ] Reevaluate whether the blocker still applies.
- [ ] Preserve blocker history and auditability.
- [ ] Let Work Queue choose the next legal state after the blocker is satisfied.
- [ ] Use the historical 0.4.3 artifact-quota blocker as a real acceptance case.

**This remains paused unless a future roadmap decision explicitly re-prioritizes it; the Autonomy Pass is historical planning, not an active prerequisite.**

---

## Future

- [ ] Additional providers beyond OpenCode.
- [ ] Broader workflow validation across more project types/platforms.
- [ ] Dashboard write actions only if a future product decision justifies them.
- [ ] Continue simplifying EvoSpec whenever a safety mechanism creates unnecessary human coordination.
