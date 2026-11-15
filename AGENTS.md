# EvoSpec repository agent instructions

These rules govern agents developing, reviewing, and testing **this repository**. They are not part of the EvoSpec methodology distributed to consumer projects.

## Model and provider ownership

- EvoSpec is an SDD methodology, not a model runner, model benchmark, or provider router. The OpenCode host owns its active session, model, provider, credentials, quotas, and inference.
- When testing EvoSpec through OpenCode, use the model already selected in the user's current host session. Do not select, override, rotate, benchmark, or probe alternative models/providers; do not use `--model`, consult a model catalog to pick a trial route, or open separate model sessions merely to check availability.
- Do not change OpenCode's model configuration, credentials, accounts, or provider settings to make a validation pass. Do not silently fall back to a free or different model when the selected route is unavailable.
- Run deterministic Core, protocol, persistence, adapter, and permission-negative tests without inference whenever possible. A model-free PASS does not establish native host/agent execution.
- Genuine native end-to-end verification may require independent OpenCode sessions, but they must retain the user's host model selection. Do not interpret a new session as permission to choose a different model.
- On HTTP 429, `Retry-After`, or unavailable inference, respect the provider's stated limit, preserve evidence, report the native gate as `BLOCKED`, and stop. Never evade the limit using another model, provider, credential, or account. A provider rejection before an EvoSpec tool call is not proof of an EvoSpec functional failure.

## Validation integrity

- Before acting, inspect the repository path, branch, HEAD, and worktree status. Preserve existing changes and use isolated fixtures for native qualification.
- Before claiming OpenCode V2 native compatibility, verify the latest published stable host version and record the exact version and commit tested. Automated bundle/adapter tests are separate evidence.
- Preserve already accepted Feature/Slice/Task identities, revisions, provenance, checkpoints, and authority files during blocked tests. Never repeat user acceptance or fabricate a new Task solely to bypass model availability.
- Report executed, skipped, blocked, and unverified gates separately. Do not infer permission to implement, merge, or release from test success.
