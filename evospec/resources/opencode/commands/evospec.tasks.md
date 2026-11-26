---
description: Inspect or add Tasks for the selected Feature.
---
<!-- EvoSpec-managed: opencode-v2 -->
With no task details, call `evospec_continuity` with `operation: "tasks"` as a read-only projection. To add work through Core, use the current revision and stable criterion, increment and dependency IDs. Human edits to `tasks.md` are authoritative definitions only after explicit `reconcile`; use typed `T-new-name` aliases and explicit references when adding, and provide exact `retire_ids` when removing. Generated checkboxes are projections; editing them never records progress. Legacy projects remain on their existing workflow. User input: $ARGUMENTS
