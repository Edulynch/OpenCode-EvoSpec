# E0 V2 Contract Review: Walkthroughs, Premortem, and Gate

> **Status: documentary desk review prepared for product-owner review.** This page checks coherence of the proposed method in [`methodology.md`](methodology.md). S1–S9 are hypothetical/tabletop examples, not runs of an agent, Core, CLI, or runtime. An illustrative answer is not an actual user decision or accepted Product Truth.

## How to read the walkthroughs

Each case records entry context, what is accepted versus hypothetical, inspectable facts, necessary questions, decisions and minimum records, focus, blockers, evidence, and allowed/prohibited outcomes. A question is marked **none under stated assumptions** where the scenario supplies enough context; any sample response is illustrative only. If a fact or authorization is absent in a real project, inspect read-only first and then stop or ask only for the material missing item.

The scenarios validate the method and proposed records. They do **not** test model quality, execute tools, prove runtime behavior, or turn an example into a real project's requirements.

## S1 — Visible text change, low risk

- **Entry/context:** Request: change one already identified visible label. The correct project/worktree, target, and bounded permission are available; the change is reversible and has a relevant regression check.
- **Intent accepted vs. hypothesis:** The requested new text and target are accepted as the bounded request. Whether it belongs to a larger feature is unknown unless the canonical context says so; do not invent one.
- **Facts to discover:** Read the current label, its owning UI location, nearby behavior, and focused test or user-facing check. Do not ask the user for facts available in the repository.
- **Question:** None if the requested text, target, and scope are unambiguous. If multiple labels or a behavior change are plausible, ask which one; this example does not supply an answer.
- **Decision and minimum artifacts:** Route A in `/change`; record only the bounded change and attributable focused evidence if a trace is required. No new feature, full mini-spec, or future-task list.
- **Focus/resume:** Keep the existing unique active feature/slice if this is its correction; otherwise retain the explicit change as separate work. Resume only from the validated target and scope.
- **Blockers:** Ambiguous target, invalid worktree identity, conflict with accepted copy/behavior, or missing write authority blocks editing. Request only the missing selection/authority.
- **Evidence:** Confirm the exact displayed text and relevant regression check. Report what was checked and its limits.
- **Allowed/prohibited; false PASS:** A bounded edit and focused check are allowed. Prohibited: ceremony, unrelated formatting, silent feature creation, or calling an unrun check PASS. A passing string assertion alone is not evidence for unrelated UI behavior.

## S2 — One-line permission change, high risk

- **Entry/context:** A one-line edit changes an access or permission rule. The size is small; the effect may be broad. The exact principal/resource/action and intended authorization may or may not be specified.
- **Intent accepted vs. hypothesis:** Only the request to change permission is known. The intended access matrix and whether the request authorizes the precise security effect are not assumed.
- **Facts to discover:** Inspect the existing policy, affected callers, principal/resource boundaries, tests, and project permission source read-only. Identify any broadening, privilege escalation, data exposure, and rollback implications.
- **Question:** Ask one scoped question only if the allowed/denied behavior, scope, or authorization remains material and cannot be inspected. Example options such as “grant only this action to this role” versus “grant all actions” are illustrative, not accepted answers.
- **Decision and minimum artifacts:** Record the exact accepted permission decision, scope, rationale, and supersession if applicable; define focused positive and negative acceptance checks. A decision proposal is not approval.
- **Focus/resume:** Stay on the selected feature/change. Do not switch focus or classify as lightweight merely because the diff has one line.
- **Blockers:** Missing authority; unclear principal or resource; unresolved least-privilege, data, or rollback concern; stale policy; failed or insufficient security evidence.
- **Evidence:** Exercise permitted and denied cases for the relevant principals/resources; verify no adjacent privilege expansion and run applicable regression/security checks. Evidence must be attributable to the current policy revision.
- **Allowed/prohibited; false PASS:** Read-only inspection and a scoped proposal are allowed before decision. Implementation waits for precise current authority. Prohibited: treating a small diff, unit-test PASS, or successful build as permission approval or proof of least privilege.

## S3 — Profile feature, commands with and without arguments

- **Entry/context:** A request proposes a private, editable user profile. A real-repository variant has an accessible project; a tabletop variant has none. The proposed command surface is `/specify`, `/clarify`, `/plan`, `/implement`, and `/status`.
- **Intent accepted vs. hypothesis:** The user's direct request supplies intent without redundant second approval when the target is identifiable. Material behavior not already stated remains a hypothesis until decided; example responses about fields or ownership cardinality are not decisions unless the user explicitly accepts them.
- **Facts to discover:** In a real repository inspect existing account/authentication, data/API contracts, decisions, and tests. In the no-repository tabletop do not claim inspection or fabricate an architecture.
- **Question:** Ask only current-slice material questions not answered by evidence (for example, required fields, visibility, edit rights, or whether one user may own multiple profiles when that affects this slice). Do not conflate “each profile has one owner” with “each owner has only one profile.” Illustrative answers below are not real decisions. With one pending question, no-argument `/clarify` shows that question and its options, then waits; with no user answer it makes no documentary mutation, records no Decision, and selects no default. Record only an explicit, attributable user answer. With no pending question, report status/no-op rather than inventing one.
- **Decision and minimum artifacts:** `/specify` records the already-stated intent and explicit scope/decisions, then surfaces only unresolved material choices; it does not request redundant approval of a clear request or grant implementation authority. `/clarify` records a decision only after its explicit user answer. `/plan` identifies the next useful end-to-end profile slice, its exclusions, criteria, evidence, and only the technical tasks needed now. **Illustrative sequence only—not accepted requirements or defaults:** for a hypothetical “private profile” agreement, Slice 1 lets the signed-in owner view their own agreed fields while another account is denied; verify the end-to-end view and denial. Slice 2 lets the owner edit one agreed field and see it persisted on reload; detail only Slice 1's technical tasks now, leaving Slice 2 as an outcome and nonblocking field choices deferred. No stack is selected.
- **Focus/resume:** No-argument `/clarify`, `/plan`, `/implement`, and `/status` work only when one valid feature and current agreement are unambiguous. `/implement` continues authorized tasks through successive useful slices until the agreed feature outcome is met or a blocker appears, without routine per-task approvals; feature completion is still separately assessed and never inferred from the task list.
- **Blockers:** Ambiguous/multiple/closed feature; unresolved material privacy, ownership, or access decision; missing authority; or missing mandatory evidence. A proposal or plan cannot unblock implementation by itself.
- **Evidence:** Verify the relevant user-visible create/view/edit path, privacy/access rules, and regressions; choose checks appropriate to the actual accepted slice. `/status` reports progress, evidence, decisions, and blockers.
- **Allowed/prohibited; false PASS:** `/status` is read-only in every state, even if its index is invalid. A no-prompt command is allowed only with unique valid context. Prohibited: picking the latest feature, treating a model-suggested field as accepted, claiming profile value from a backend-only check, or continuing through missing authority.

## S4 — JSON → SQLite → API, one usable transition at a time

- **Entry/context:** A game currently loads playable levels from local JSON; later work may use SQLite and a remote API. Each step must produce a route usable by the application. The future architecture is deliberately not fixed.
- **Intent accepted vs. hypothesis:** Preserve only behavior and constraints that were explicitly accepted. Offline JSON behavior is an observation unless offline availability was agreed. Whether future API behavior must work offline is unresolved if not recorded.
- **Facts to discover:** Inspect current accepted behavior, level format, migration path, data integrity checks, app entry points, and current offline evidence. Find prior decisions and reasons before proposing technology changes.
- **Question:** If an API migration may materially remove observed offline use and there is no current decision, ask once: preserve offline, deliberately change it, or defer the incompatible migration. These choices are examples, not an accepted answer.
- **Decision and minimum artifacts:** Keep a compact agreement for each next slice, a technical decision with rationale/supersession for JSON → SQLite and later API choices, and linked migration/end-to-end evidence. Do not decide the eventual architecture in the initial agreement or replay all history.
- **Focus/resume:** Complete and assess one usable transition before moving on. A later transition reuses still-valid decisions and reopens only affected tasks/evidence with a reason.
- **Blockers:** Missing data-integrity evidence, unresolved material offline behavior, stale source records, or a transition that is only an isolated backend endpoint when the slice promises app behavior.
- **Evidence:** For JSON → SQLite, load/play a real level through the app and verify parity/data integrity. For SQLite → API, fetch and use an actual level in the app; test failure/offline behavior according to the accepted decision. Attribute tests and environment to each transition.
- **Allowed/prohibited; false PASS:** Change storage incrementally when authorized. Prohibited: treating observed offline operation as permanently required without a decision, silently dropping it, freezing an API/cache architecture early, or marking an endpoint/mock-only check as the app-level slice PASS.

## S5 — Resume without a new prompt

- **Entry/context:** A prior checkpoint identifies repository/worktree, unique feature, current agreement revision, active slice/task, decisions, progress, evidence, and resume point. The user invokes continuation without repeating the request.
- **Intent accepted vs. hypothesis:** Recover the accepted agreement from canonical references; a previous chat transcript is not the authority. Current authorization is valid only if the real permission source still covers the exact work.
- **Facts to discover:** Re-read the referenced canonical records; verify identity, current feature, decision supersession, revision/concurrency state, evidence freshness, and authority. Do not assume a saved pointer means its target remains valid.
- **Question:** None when references are unique, current, and authorized. Ask or request the minimum explicit feature selection when missing/ambiguous; request a fresh decision/authority only for an actual stale or material gap.
- **Decision and minimum artifacts:** Continue the referenced task, or record the exact blocker and resume point. Refreshing an index is not an implicit repair; invalid or conflicting references remain visible until explicitly resolved.
- **Focus/resume:** Resume the same feature only after validation. An explicit user-selected focus change is recorded separately; retain prior resumable context.
- **Blockers:** Stale agreement/revision, changed permission, conflicting worktree, superseded decision, incomplete checkpoint, or invalidated evidence.
- **Evidence:** Reuse only evidence still attributable to the unchanged relevant revision and criteria. Re-run checks invalidated by changed implementation or requirements.
- **Allowed/prohibited; false PASS:** Continue authorized nonblocked work without asking for a prompt repetition. Prohibited: reconstructing progress from chat, using an old authorization, claiming freshness because a checkpoint exists, or treating active selection as a permission boolean.

## S6 — Two worktrees or multiple candidate features

- **Entry/context:** Two worktrees or feature candidates contain distinct active work. A command is issued without enough information to distinguish them, or an explicit worktree/feature selector is supplied.
- **Intent accepted vs. hypothesis:** A request to continue is known; its target is not accepted until repository/worktree and feature references resolve uniquely. Recency is not a user choice.
- **Facts to discover:** Validate canonical repository identity, exact worktree identity, feature/agreement references, revisions, and whether the records are distinct or shared. Inspect without writing.
- **Question:** If more than one valid target remains, ask the smallest selection (worktree and/or feature). No answer is inferred in this walkthrough.
- **Decision and minimum artifacts:** Keep each worktree/feature pointing to its canonical agreement, slice, and task; retain a separate checkpoint when work differs. Do not copy task states into a shared active-feature index.
- **Focus/resume:** Do not merge plans, switch to “latest,” or change active focus automatically. Resume the selected target only after its authority and revisions validate.
- **Blockers:** Duplicate/missing pointer, identity mismatch, stale revision, conflicting writes, or no user selection among multiple candidates.
- **Evidence:** Record exact worktree/revision references for changed work and ensure evidence belongs to the same target and current agreement.
- **Allowed/prohibited; false PASS:** Read-only reporting and explicit selection are allowed. Prohibited: cross-worktree task mixing, last-writer-wins, and counting evidence from one feature/worktree as proof for another.

## S7 — Change arrives during an active feature

- **Entry/context:** One feature is active and a new request arrives. It could correct a known defect, materially evolve the feature, or be unrelated.
- **Intent accepted vs. hypothesis:** The new request is a change proposal. Its relationship to the active feature is not inferred from shared words or timing.
- **Facts to discover:** Read the active feature's agreed outcome, slice criteria, related decisions, current work, and relevant evidence; compare the request semantically, including affected behavior and scope.
- **Question:** None if the relation is explicit in accepted scope and authority. If correction vs. behavior change vs. unrelated work would change ownership/focus, ask one minimum relationship/focus question. Examples below are routes, not user answers.
- **Decision and minimum artifacts:** **Correction:** update only the affected task/evidence inside existing criteria. **Material evolution:** propose a delta/new decision and obtain agreement, preserving supersession rationale. **Unrelated:** create/route separate side work only with its own scope; preserve the primary checkpoint.
- **Focus/resume:** Stay on the primary feature for a correction or evolution; unrelated work does not take focus unless the user explicitly selects it. After side work, resume validated primary state.
- **Blockers:** Cannot establish relation from canonical truth; expansion lacks agreement/authority; side-work target ambiguous; affected assurance has not been invalidated/rechecked.
- **Evidence:** Recheck only criteria/tasks affected by the correction or evolution and explain why unaffected evidence remains valid. Record side-work evidence under its own identity.
- **Allowed/prohibited; false PASS:** Route each of the three relations separately. Prohibited: hijacking the feature, silently expanding criteria, duplicating Product Truth, or declaring all evidence stale without reason (or valid without checking impact).

## S8 — Tests pass; game fun is unknown

- **Entry/context:** A playable game mechanic builds and automated tests pass, but no player has evaluated whether it is fun/useful.
- **Intent accepted vs. hypothesis:** Technical behavior may have objective acceptance criteria. “Fun” remains a subjective hypothesis until the relevant player/product judgment is available.
- **Facts to discover:** Inspect which criteria were agreed, what test fixtures exercised, whether the mechanic was played in a relevant environment, and whether subjective value is a condition of slice or feature acceptance.
- **Question:** If fun/usefulness is a material agreed acceptance criterion, request appropriate human playtest/value feedback. If it was not part of this slice's accepted criteria, do not add it retroactively as a blocker; report it as an open product hypothesis.
- **Decision and minimum artifacts:** Store test result as technical evidence and any playtest feedback as distinct evidence/decision. Do not mark value accepted based on model opinion or test output.
- **Focus/resume:** Keep the feature open while required subjective feedback is pending. A bounded objective slice may be accepted only if its agreed criteria and authority independently permit it.
- **Blockers:** Missing required playtest or unresolved value decision blocks the corresponding acceptance, not necessarily every technical task.
- **Evidence:** Tests prove only their checked behavior. Actual playtest and attributed human judgment are needed where agreed. Record unknowns and evidence scope.
- **Allowed/prohibited; false PASS:** Report technical verification `PASS` with value `UNKNOWN`. Prohibited: infer fun from compilation/tests, accept the subjective outcome autonomously, or complete the whole feature by inference.

## S9 — Constitution absent/incomplete, minimal repair vs. cross-cutting decision

- **Entry/context:** A small, clear, bounded repair is requested in a repository with no Constitution, or with an incomplete one. A second variant exposes a decision that could affect project-wide security, compatibility, or product invariants.
- **Intent accepted vs. hypothesis:** The bounded repair is accepted only to the extent the request and existing evidence define it. A missing document does not imply missing product intent, nor does it authorize inventing a universal rule.
- **Facts to discover:** Check whether a Constitution exists, its current stable invariants, project-specific constraints, tests, and relevant accepted decisions. Distinguish absent material from inaccessible material.
- **Question:** None for a clear low-risk repair whose outcome is otherwise unambiguous. Ask one material question for an unresolved cross-cutting invariant or conflicting policy; an example answer is illustrative only.
- **Decision and minimum artifacts:** For the repair, keep a minimal change/evidence trace; do not create a full Constitution or backlog. For a material transversal rule, record the scoped decision/rationale and amend stable principles only after explicit agreement.
- **Focus/resume:** Keep the repair within its selected project/feature. Do not change project-wide focus or governance by filling a blank document.
- **Blockers:** Real policy conflict, material invariant unknown, or missing scope/authority. Mere absence/incompleteness of a Constitution is not itself a blocker for the simple route.
- **Evidence:** Use focused regression and relevant compatibility/security checks. If a new project invariant is agreed, validate affected behavior and retain the decision source.
- **Allowed/prohibited; false PASS:** Continue the minimum authorized repair when safe. Prohibited: mandatory governance paperwork for every fix, treating an incomplete Constitution as consent, or presenting document creation as proof of behavioral conformance.

## Premortem: ways this method can fail

| Failure mode | Early signal | Prevention in the contract | Observable check / stop condition |
| --- | --- | --- | --- |
| **Bureaucracy** | Routine fixes create multiple mandatory documents, prompts, or exhaustive future plans. | Proportional lightweight route; mini-spec is semantic and only for a material feature/next slice. | For S1/S9, inspect artifacts/questions. If extra ceremony did not resolve a material uncertainty or record needed evidence, remove it; do not count more documents as progress. |
| **Scope creep** | Suggestions, adjacent cleanup, or a planned task enter implementation without agreement/authority. | Explicit inclusions/exclusions and a separate authority boundary; task decomposition stays inside the slice. | Compare requested and changed scope plus current permission. Any unapproved target/effect stops; do not accept an agent's own recommendation. |
| **Stale context** | An older session writes after a newer decision, changed scope, or invalidated check. | Validate identity, revision, authority, and evidence freshness before mutation; reject stale/conflicting writes instead of last-wins. | Compare each write's inspected revision with the current canonical revision. Mismatch means reload/reconcile; no retry from stale data. |
| **Dual source of truth** | Index copies requirements, task status, evidence, or permission, or disagrees with linked records. | Index contains references only; each concept has one named canonical source responsibility. | Resolve every index pointer and compare current revisions before use. On mismatch/duplicate authority, block writes and report; never silently repair/select one copy. |
| **Feature hijack** | A new unrelated request is folded into the active feature or changes focus without a choice. | Classify correction, material evolution, or unrelated work against accepted scope; focus change is explicit. | Review the chosen relationship and record refs. If it is unsupported by scope/decision evidence, revert routing and request the minimum selection. |
| **False acceptance** | Test PASS, task completion, plan approval, or slice acceptance is presented as feature completion/value/publication. | Keep verification, slice acceptance, feature completion, and Product Truth/release authority separate. | Trace each claim to its own pre-agreed criterion, current authority, and fresh attributable evidence. Missing/subjective/material evidence is UNKNOWN/BLOCKED, never PASS. |

## E0 deliverable traceability

| E0 deliverable / criterion | Contract location | Walkthrough / validation |
| --- | --- | --- |
| Compact method and glossary: Project/Constitution, Feature, Slice, Task, Change, Decision, Evidence | [`methodology.md`](methodology.md) — “Core terms and boundaries,” routes, gates | S1, S3, S4, S8, S9 |
| Proposed command table, optional arguments, context, effects, authorization, missing context | [`methodology.md`](methodology.md) — “Proposed command behavior” | S3, S5, S6, S7 |
| Conceptual JSON index and canonical sources, no schema selection | [`methodology.md`](methodology.md) — “Conceptual active-feature index and canonical sources” | S5, S6; stale/dual-source premortem |
| `/change` preflight, semantic risk, adversarial examples | [`methodology.md`](methodology.md) — “Proportional routes and materiality” and “`/change` preflight” | S1, S2, S4, S7, S9 |
| All nine required desk scenarios and minimum records | This page, S1–S9 | This page; all are hypothetical desk checks, not runtime trials |
| Premortem: bureaucracy, scope creep, stale context, dual source, feature hijack, false acceptance | This page, “Premortem” | Six failure rows each have a signal, prevention, and check/stop condition |
| Explicit gate into E1 and non-goals | This page, “E0 → E1 gate” | Product approval is pending; no E1 work or architecture selection occurred |

### Contrast sources, not dependencies

- [Gentle AI / ODD usage guide](https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/usage.md) was a contrast for small explore → implement → proportional check loops. This proposal does not adopt its separate review artifacts or infrastructure as requirements.
- [Spec Kit `implement` template](https://github.com/github/spec-kit/blob/main/templates/commands/implement.md) was a contrast for optional arguments and use of available context. This proposal does not adopt a mandatory future-task waterfall, hooks, or a ritual checklist.

## E0 → E1 gate

**Documentary result:** The requested E0 materials are prepared for review and cross-referenced. This is an internal document completeness/coherence assessment, not human product approval, live implementation validation, or adoption of Product Truth.

**Recommendation: NO-GO to E1 now; conditional GO only after explicit product-owner review and approval of the methodology contract and this gate.** Until that decision is recorded, E0 acceptance remains pending. E1 has **not** started or been approved; no architecture, JSON schema, storage, engine, CLI, or repository merger is selected.

**Next and only gate action:** product owner reviews and explicitly accepts, requests a delta to, or rejects the contract plus this gate. After that decision, E1 may compare implementation options against the accepted contract; it must not be inferred from these prepared documents.
