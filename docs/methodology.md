# EvoSpec Methodology

EvoSpec treats product work as a chain of decisions and evidence. It keeps that chain outside one conversation so a person, agent, or new session can understand what was intended, what was observed, and what still needs a decision.

## The workflow in brief

```text
intent -> research -> material clarification -> implementation
       -> REVIEW -> VERIFY -> explicit convergence -> accepted outcome
```

The depth of each step should match the change. A small tuning change needs focused evidence. A foundational, risky, or uncertain change needs broader research and stronger checks. EvoSpec classifies changes internally as `TUNE`, `PATCH`, `FEATURE`, `FOUNDATION`, or `EXPERIMENT`; people describe intent without choosing a category first.

## 1. Start with intent and evidence

Record what someone wants to change or create. For an established capability, research comes before changing its accepted behavior. Start with the active feature and relevant Living Specs, then inspect decisions, constraints, related capabilities, implementation, and tests when those sources are relevant.

Research is evidence led. It should preserve where a conclusion came from and how much confidence it deserves. Project documents and tests can inform a decision, but weak or conflicting discoveries do not silently become binding product rules.

When a request already matches an existing capability, treat it as a change to that capability rather than creating duplicate product truth. This lets an existing project adopt EvoSpec without pretending that every existing behavior is new. The broader adoption path is described in [`standalone-cli.md`](standalone-cli.md), while provider integration is documented in [`opencode.md`](opencode.md).

## 2. Clarify only material questions

Ask a question when its answer could change behavior, architecture, scope, risk, lifecycle, or product meaning. Keep each question atomic and record the accepted answer as a scoped decision. A theoretical ambiguity with no material effect does not need a clarification record.

An unresolved material decision blocks a change from becoming ready or entering implementation. Superseded decisions remain traceable, so a later choice changes the current guidance without erasing the earlier one.

## 3. Implement with a recoverable context

Once required decisions are accepted, implementation can begin with a ready context. Record the current action, focus, completed work, current work, pending work, and resume point as work changes. This makes interruption ordinary rather than destructive. Recovery should restore recorded context, not invent progress or rely on an old conversation.

Unrelated work can be handled as side work. EvoSpec preserves the primary context and restores it after the side change reaches a terminal outcome. Side work remains traceable, but it is not silently presented as unfinished primary work.

## 4. Keep REVIEW and VERIFY separate

**REVIEW** asks whether the result fits the intent, accepted decisions, constraints, product truth, scope, and available evidence. Structured review evidence and findings produce one durable verdict: `PASS`, `WARN`, or `BLOCKED`. An explicit review inspection is read only. Review is not a claim that code was understood autonomously.

**VERIFY** records technical or other supporting evidence and whether that evidence is sufficient for the change. Verification is not product acceptance. A passing test can show that an observed behavior works under a test condition, but it cannot decide whether that behavior is the intended product behavior.

If implementation changes after a review or verification checkpoint, the affected assurance is invalidated and fresh evidence is needed.

## 5. Converge only by explicit choice

Convergence is optional until someone explicitly requests it. It is allowed only after an acceptable review, sufficient verification, and explicit reconciliation of relevant decisions, observations, experiments, drift, and final context.

If expected behavior changed, convergence needs caller or model authored Product Truth content before a new immutable Living Spec revision can be published. If expected behavior did not change, the change can reach an accepted terminal archive without publishing a new Product Truth revision. There is no automatic convergence.

Code, tests, runtime observations, and experiment results are evidence. They are not autonomous Product Truth publication. A rejected experiment remains useful knowledge without changing the Living Spec. An accepted experiment becomes eligible for deliberate product adoption only through an explicit decision.

## Living Product Truth

A Living Spec describes accepted expected behavior for one capability. It is deliberately evolvable, and its revisions retain history. Runtime truth may differ from Product Truth. EvoSpec records that difference as drift for reconciliation instead of silently choosing implementation behavior or specification text.

This separation keeps a useful question visible:

```text
What happened?       evidence, code, tests, observations
What should happen?  accepted Product Truth in the Living Spec
Who decides?         an explicit product decision at convergence
```

For the system boundary behind this method, see [`architecture.md`](architecture.md). Guides cover the provider-neutral boundary in [`agent-protocol.md`](agent-protocol.md), protected accepted behavior in [`semantic-locks.md`](semantic-locks.md), and the OpenCode adapter in [`opencode.md`](opencode.md).
