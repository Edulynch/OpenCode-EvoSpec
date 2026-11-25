---
description: Run the selected criterion's allow-listed check and record its result.
---
<!-- EvoSpec-managed: opencode-v2 -->
Call `evospec_continuity` with `operation: "verify"`, the current revision, Feature ID and criterion ID. The plugin resolves the criterion's canonical test target, requests host permission, rechecks the revision, then runs only its allow-listed Python unittest module or Node test file. Report the actual exit code and output digest. A failed check is evidence of failure; a pass is criterion verification only, not Feature acceptance or closure. Never supply a fabricated result or retry after a stale result. User input: $ARGUMENTS
