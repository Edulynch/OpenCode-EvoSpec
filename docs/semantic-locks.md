# Semantic Locks

Semantic Locks protect named aspects of accepted Product Truth while a later change is being assessed. They are Core-owned records attached to a Feature. A lock protects the aspect names and the accepted baseline recorded in its current revision. The names are generic. EvoSpec does not interpret the meaning of a product domain automatically.

## Plain-language example

Suppose accepted Product Truth says that an export feature delivers a file to its caller. A team could create a lock with a protected aspect named `delivery`, with a reason such as “preserve delivery for callers.” That name tells the workflow what the team wants to protect. It does not make EvoSpec understand the export format or inspect the implementation for delivery behavior.

Later, a change proposes a new export flow. The agent supplies a change-scoped lock assessment. The assessment identifies affected aspects, names any protected aspects it touches, explains a preserving path, and supplies evidence and rationale. If the change touches `delivery`, the assessment can describe an explicit migration or other reason for the change. EvoSpec then validates the assessment and the required authorization before review or convergence can accept the protected change.

## What a lock protects

A lock revision records:

- the accepted Product Truth publication used as its baseline;
- one or more caller-named protected aspects;
- a rationale for protecting those aspects.

The baseline is part of the lock history. After an authorized change is accepted, the active lock remains active and its Product Truth baseline is rebased atomically to the new publication. Retired locks remain recorded as history but are no longer active.

## Preserving paths and conflicts

Every active lock needs a current assessment for the change under review. The assessment includes a plain-language preserving path. It also states whether the change conflicts with a protected aspect or attempts to evade the lock.

An assessment does not silently approve a conflict. A protected conflict is surfaced during review. The workflow blocks review or convergence until the required current assessment is present and the exact authorization exists. Stale lock references, incomplete coverage, and evasion are also rejected rather than treated as harmless changes.

## Explicit authorization

Authorization is specific to the protected change. It identifies the exact lock and lock revision, Feature, Change, Decision, and protected aspect set. The workflow checks those links and the assessment revision before accepting the authorization. A general statement such as “the team approves this” is not a substitute for the exact scoped authorization.

An authorized change still follows the normal review, verification, and convergence gates. Semantic Locks do not publish Product Truth by themselves, and they do not create automatic convergence.

## What Semantic Locks do not do

There is no `lock` or `unlock` CLI command. Semantic Locks are application-API-first records, not an additional local CLI surface.

EvoSpec does not automatically parse Product Truth, source code, or natural-language intent to invent protected aspects. A caller or agent supplies the lock, its named aspects, the change-scoped assessment, evidence, preserving path, and any authorization. Core validates the supplied records, their scope, revision, recovery, and publication rules.
