# Distributed resources ownership

This directory contains source assets packaged by EvoSpec for consumers. These are **not** live consumer-project state or local development copies.

- OpenCode V2 plugin templates, commands and skill sources belong under `opencode/`. The integration owner in `../opencode_integration.py` determines exactly which assets are copied.
- Dashboard HTML, JS and CSS belong under `dashboard/`. Keep them read-only in relation to Core-managed state.
- Do not edit generated assets inside a consumer project's `.opencode/` to fix a packaging issue; change the canonical packaged source, then verify the integration/refresh behavior.
- Do not write Feature agreements, Slice criteria, task progress, decisions, test receipts or Living Specs into this directory.
- When changing an asset, consider source packaging, hash ownership, conflict behavior and corresponding adapter/dashboard tests.
