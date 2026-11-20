# Human dashboard asset rules

These assets present EvoSpec information; they are not a second authority or storage system.

- Keep browser presentation in the existing HTML, CSS and JavaScript files.
- Read status, active Feature/Slice/Task, blockers and acceptance indexes from the supported read-only projection; do not infer missing results or reconstruct truth from stale browser data.
- Never write directly to `.evospec/state.json`, receipts or Living Specs from dashboard assets.
- Treat unverified progress, verified criteria, accepted Slice outcome and completed Feature as different states; label unknown/blocked explicitly.
- Test display and filtering behavior using isolated fixtures in `tests/` rather than altering user records.
