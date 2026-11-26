---
description: Show authoritative EvoSpec status without mutation.
---
<!-- EvoSpec-managed: opencode-v2 -->
With no arguments, call `evospec_continuity` status only. If it returns valid composed state, report the selected Feature, optional Increments/Slices, Tasks, current criterion checks, pauses, unknown outcomes, closures, and next eligible Task. Report semantic Markdown edits, formatting/notes drift, generated checklist drift, and identity/reference conflicts in their distinct returned categories; do not present un-reconciled definitions as accepted status or evidence. Never treat a checkbox or Task report as completion. If Core reports legacy `MIXED_AUTHORITY` or no continuity marker, call only `evospec_context` and report its legacy projection instead. Corrupt or diverged state is a stop, not a repair invitation. Do not initialize, resume, accept, select, reconcile, upgrade, or write during a status query. User input: $ARGUMENTS
