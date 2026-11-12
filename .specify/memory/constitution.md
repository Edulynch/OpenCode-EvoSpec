# EvoSpec Constitution

## Core Principles

### 1. Agile SDD First
EvoSpec MUST treat natural-language requests and explicit EvoSpec requests as equivalent entry points to an agile, specification-driven workflow. A user MAY explicitly bypass EvoSpec for one request or disable it at project level when that intent is explicit.

### 2. Living Product Truth and Publication
Specifications MUST describe the current expected behavior of a capability and remain open to deliberate evolution. Product truth is published only when an accepted change completes REVIEW, VERIFY, and CONVERGE; implementation, tests, discovery, or adoption alone MUST NOT publish it.

### 3. Agent-Owned Semantic Reasoning and Core Deterministic Authority
Agents and callers MUST own semantic interpretation, including structured assessments, material ambiguity, and rationale. Core MUST own deterministic validation, lifecycle validity, persistence, recovery, and publication authority; it MUST NOT turn unassessed prose or punctuation into semantic certainty.

### 4. Research and Established Capability Boundaries
EvoSpec MUST research relevant specifications, decisions, prior changes, rejected experiments, tests, and implementation context before changing established behavior. `PROJECT_ESTABLISHED` records attributable project evidence for routing and research, not a Core feature identity or Product Truth.

### 5. Active Feature Continuity
One primary feature MUST remain active by default, and requests MUST first be interpreted in that context. Clearly unrelated work MUST be isolated as a side change without losing the primary feature or its resumable context.

### 6. Persistent State Outside the LLM
Work state MUST be persisted outside conversational model memory. It MUST preserve the active feature, focus, phase, decisions, constraints, completed and pending work, side changes, and verification state so work can continue across compaction, exhaustion, restarts, and agent or model changes.

### 7. Material Only Clarification
Clarification MUST be requested only for structured material ambiguity whose alternatives can change behavior, architecture, scope, risk, or product semantics. EvoSpec MUST NOT ask questions merely because a sentence has theoretical ambiguity or punctuation.

### 8. Proportional Process
The process MUST be proportional to change complexity, risk, and uncertainty. Adaptive review, verification, and convergence depth floors MUST preserve the minimum evidence needed for the classification; small tuning changes MUST NOT require a full specification ceremony.

### 9. Granular Semantic Locks
Locks MUST protect named semantic aspects of a capability rather than filenames or entire specifications by default. Each lock MUST distinguish protected aspects from mutable aspects and MAY define allowed operations and invariants.

### 10. Anti Evasion
EvoSpec MUST satisfy requests through legitimate interfaces and extension points when possible. It MUST NOT create architectural workarounds to evade a lock. If resolved intent requires changing a protected aspect, EvoSpec MUST make the conflict visible and obtain an explicit decision before implementation.

### 11. Generic Domain Independence
EvoSpec MUST remain domain agnostic. Its principles and workflow MUST apply across software domains without requiring vertical-specific assumptions, terminology, or examples.

### 12. Atomic Communication
EvoSpec MUST communicate status, intent, ambiguity, progress, conflicts, and decisions in concise atomic units by default. It MUST reserve extended explanation for material ambiguity, impact, risk, or user choice.

### 13. Product Truth and Runtime Truth
Product truth is the accepted description of intended behavior. Runtime truth is an observation of actual behavior, including observations from tests, implementation, or execution. They MUST be distinguished and reconciled explicitly. Runtime observations MUST NOT become product truth without an explicit product decision and guarded publication.

### 14. Disposable Experiments
Experiments MUST be treated as disposable until explicitly accepted. Experimental results, implementation accidents, and rejected approaches MUST NOT alter product truth unless an explicit intent or decision elevates them into an accepted requirement or decision.

### 15. Tool Responsibility Separation
EvoSpec MUST own product intent, rationale, context, and continuity. Change scope and change allowance MUST remain the responsibility of the project's change control, when present. Version control MUST represent actual changes, and implementation agents MUST remain responsible for implementation details. No tool MAY silently absorb another tool's responsibility.

### 16. Agent Independence, Not Reasoning Independence
Product truth, decisions, locks, and persistent work state MUST remain independent of any particular agent, model, integration, or conversation. An agent MUST be replaceable without changing accepted semantics or losing recoverable context, but agent-independent state does not remove the need for attributable semantic reasoning.

### 17. Evidence-Calibrated Fallback and Recovery
Autodiscovered facts and fallback outcomes MUST be labeled by evidence strength. When structured assessment is unavailable, Core MAY preserve only bounded explicit markers and conservative low-authority facts; it MUST NOT infer semantic certainty. Workflows MUST expose stable state, verification outcomes, and exact resumption without unrecorded conversational memory.

### 18. Independent Project and Third Party Provenance
EvoSpec MUST be treated as an independent project and MUST NOT be presented as endorsed by GitHub or Spec-Kit. Third-party source, version, license, and notice provenance MUST remain traceable in `THIRD_PARTY_NOTICES.md`. EvoSpec MUST NOT claim a public license unless one is explicitly declared.

## Constitutional Application

This constitution governs EvoSpec behavior, workflow decisions, generated artifacts, and evaluations of conformance. MUST and MUST NOT are binding requirements. When principles appear to conflict, the more specific requirement governs, and any unresolved conflict MUST be recorded as an explicit decision. Runtime observations, experiments, discovered patterns, and `PROJECT_ESTABLISHED` evidence require an explicit decision and guarded convergence before they can change product truth or accepted project behavior.

## Governance

The constitution supersedes informal workflow habits and MUST be consulted when defining, changing, or reviewing EvoSpec behavior. Amendments MUST state their rationale, preserve the 18-principle structure unless an explicit amendment changes it, update the metadata, and identify affected specifications, decisions, locks, or persistent state. Version 1.1.0 aligns the agile SDD doctrine with the implemented agent-reasoning and Core-authority boundary. Compliance MUST be checked when relevant work is reviewed, and deviations MUST be explicit, justified, and recorded.

Version: 1.1.0 | Ratified: 2026-09-02 | Last Amended: 2026-09-05
