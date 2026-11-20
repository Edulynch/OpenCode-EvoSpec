# This repository's local OpenCode configuration

These files assist agents developing the EvoSpec repository; they are **not**
automatically the canonical resources shipped to consumer projects.

- Canonical distributed EvoSpec command, skill and plugin source lives under `evospec/resources/opencode/`; change that source and the integration owner rather than editing `.opencode/` generated copies as the fix.
- This checkout also contains Spec-Kit commands. Preserve existing foreign tooling and user-owned resources.
- Do not write accepted Feature/Slice/Task records, decisions, test evidence or Living Specs into local command Markdown or skills. Use Core-managed state and supported tools.
- A command is an interface, not workflow authority. Observe returned legal actions, current revisions, source identity, explicit human gates and native host permissions.
- Do not change the active model, provider, credentials or global OpenCode settings for an EvoSpec test.
