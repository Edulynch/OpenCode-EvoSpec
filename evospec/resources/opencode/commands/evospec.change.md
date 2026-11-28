---
description: Record a bounded change or a short correction in composed continuity.
---
<!-- EvoSpec-managed: opencode-v2 -->
Use `evospec_continuity` with the latest revision. For an open Feature, add or amend only the requested criteria and add a Task; preserve prior checks and closure history. A change to criterion text or test target makes that criterion's prior evidence stale. If the Feature is closed, first call `reopen` at the current revision with its exact `feature_id` and a concise reason reflecting the requested evolution; reread status, then make the semantic change at the new revision. Reopening starts a new cycle and retains prior closure/check history, but old-cycle evidence is not current for the new cycle. Without a Feature ID, create a short `CORRECTION` Feature with no Slice using the same criteria/Task model. If the project has not opted into composed mode, use `/evospec.specify` first; never migrate legacy state. User input: $ARGUMENTS
