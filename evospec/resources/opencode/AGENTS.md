# Canonical OpenCode resource templates

This is the **source** of distributed OpenCode integration assets. It is not a consumer's `.opencode/` folder.

- Store plugin implementation under `plugins/evospec/`, slash-command templates under `commands/`, and the EvoSpec skill under `skills/evospec/`.
- The explicit resource map in `evospec/opencode_integration.py` controls distribution. A file appearing here does **not** mean it is installed automatically.
- Never persist runtime lifecycle state, accepted agreements or user decisions in a command, skill, plugin resource or `AGENTS.md`.
- Maintain the distinction between agent reasoning, host permission prompts and Core-validated mutations. Never imply that an available tool or command is permission to act.
- Keep provider/model selection with the OpenCode host, and avoid requiring app-local npm dependencies unless separately authorized.
- Validate integration manifests, refresh/removal ownership, conflict preservation and native permission boundaries after changes.
