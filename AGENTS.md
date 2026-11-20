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

## Canonical information ownership and directory rules

Apply these repository-development rules together with the closest applicable
nested `AGENTS.md`. Each nested file describes its directory's responsibility;
none overrides Core authority or grants permission to write.

| Information | Canonical owner | Do not store it in |
| --- | --- | --- |
| Python domain, workflow, protocol and persistence implementation | `evospec/` | `docs/`, `specs/`, generated `.evospec/` files |
| Packaged OpenCode plugin, commands, skill and UI assets | `evospec/resources/` | Manually edited local `.opencode/` copies |
| Runtime Feature/Slice/Task authority, decisions, evidence and checkpoints | Core-managed project-local `.evospec/` through supported operations | `AGENTS.md`, chat history, arbitrary Markdown, direct JSON edits |
| Accepted Product Truth | Core-published immutable Living Specs under project-local `.evospec/living-specs/` | Draft specs, tests, source-code comments |
| Source repository design history and proposals | `specs/` and `docs/`, with their respective scopes | Runtime authority records |
| Executable regression checks and isolated fixtures | `tests/` | Production state or user-owned repositories |
| Release construction and installation logic | `packaging/` and `scripts/` | Project-local runtime state |
| CI automation and operations | `.github/` and `ops/` | Product specifications |

Before writing, identify whether a change is to the EvoSpec **framework** or
to a **consumer project's** workflow state. Framework development must not
rewrite consumer state. The `.evospec/` directory is Git-ignored here and
must not receive a generated `AGENTS.md`; its content is managed by Core.

Do not duplicate operational rules or feature decisions across multiple files.
Reference the authoritative owner and update adjacent documentation only when
the underlying contract really changed. `AGENTS.md` files are instructions
for agents, not a storage schema, a permission gate, or Product Truth.
