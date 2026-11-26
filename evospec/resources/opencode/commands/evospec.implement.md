---
description: Dispatch and checkpoint eligible Tasks in the selected Feature.
---
<!-- EvoSpec-managed: opencode-v2 -->
Read canonical continuity status and the eligible Task queue. Before any effect for a Task, call `evospec_continuity` with `operation: "implement"`, `phase: "begin"`, the exact Task ID and current revision; only then perform the scoped work under ordinary host permissions, and record its outcome afterward with `phase: "report"`. `--all` means repeatedly ask Core for the next eligible Task, checkpoint each attempt, and re-evaluate after each result; it has no three-Task/list cap. Split or add technically valid Tasks when needed. Never blindly repeat an unresolved attempt; reconcile an unknown result explicitly. Stop on a material decision, permission denial, failure, cancellation, or no eligible work. A checkpoint is not a filesystem grant or proof of completion. User input: $ARGUMENTS
