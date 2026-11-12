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
