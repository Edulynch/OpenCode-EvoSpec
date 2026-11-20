# Regression tests and evidence handling

Tests live here; their outputs are observations, not accepted Product Truth.

- Add regression tests beside related test families (domain, persistence, protocol, integration, dashboard, packaging, CLI). Keep fixtures isolated, disposable and deterministic.
- Use temporary repository roots. Never rewrite a developer's real `.evospec/`, globally installed OpenCode configuration, credentials or existing worktrees to make a test pass.
- Assert exact authorized root, schema, state revision, identity, immutable receipts and failure-closed behavior for negative cases.
- Distinguish Core/unit PASS, adapter/integration PASS and independently witnessed OpenCode native PASS. Record skipped, blocked and unverified tests separately.
- Do not infer human approval, criterion verification, Slice acceptance, Feature completion or release authority from a passing test.
- Do not switch model/provider to work around rate limits; preserve failure evidence. Never claim simulated prompts prove a live permission dialog was displayed.
