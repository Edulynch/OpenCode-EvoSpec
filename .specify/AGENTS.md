# Spec-Kit support files

This is Spec-Kit's supporting configuration, templates and workflow material for **developing EvoSpec**.

- Keep reusable Spec-Kit templates under `templates/`, helper automation under `scripts/`, method support under `workflows/`, and historical configuration/memory in their existing locations.
- Do not deposit actual EvoSpec runtime Feature/Slice/Task state or Product Truth here. Runtime state belongs exclusively to Core-managed project-local `.evospec/`.
- Treat existing `constitution.md` and templates as source-development inputs, not an implicit grant of feature, release or destructive-operation authority.
- Avoid sweeping template regeneration or migration of existing material unless the requested change explicitly includes it.
- Preserve foreign SDD layouts; do not rename, overwrite or adopt them into EvoSpec authority automatically.
