---
description: Persist a pause and optional related or external detour.
---
<!-- EvoSpec-managed: opencode-v2 -->
Use `evospec_continuity` with the current revision to record why work is pausing. For a detour, record `RELATED` or `EXTERNAL`, its title and the exact Task to return to. This is durable bookkeeping, not cancellation: an in-flight operation remains unresolved and blocks another Task until its outcome is reconciled. Preserve unrelated user changes. User input: $ARGUMENTS
