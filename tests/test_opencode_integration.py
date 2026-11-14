from __future__ import annotations

import json
import os
import subprocess
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from evospec.agent import project_context
from evospec.agent_protocol import context_json
from evospec.domain import Feature, FeatureId, LivingSpecId
from evospec.opencode_integration import (
    COMMANDS,
    CONTINUATION,
    MANIFEST,
    PLUGIN,
    SKILL,
    IntegrationRegistry,
    OpenCodeIntegrationProvider,
    _agent_command,
    _cli_command,
    _resource_bytes,
    _resources,
    integrate,
)
from evospec.persistence import integration_registry_path
from evospec.services import CoreService
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


class OpenCodeIntegrationTests(unittest.TestCase):
    @property
    def provider(self) -> OpenCodeIntegrationProvider:
        return OpenCodeIntegrationProvider()

    @property
    def registry(self) -> IntegrationRegistry:
        return IntegrationRegistry((self.provider,))

    def test_packaged_resources_are_dependency_free_v2_plugin_nine_commands_and_skill(self) -> None:
        resources = _resources()
        self.assertEqual(set(resources), {
            PLUGIN,
            CONTINUATION,
            SKILL,
            *(f".opencode/commands/{name}" for name in COMMANDS),
        })
        self.assertEqual(len(COMMANDS), 9)
        plugin = resources[PLUGIN]
        self.assertIn('windowsHide: globalThis.process.platform === "win32"', plugin)
        self.assertIn("runtime.tool.transform", plugin)
        self.assertIn('"evospec_context"', plugin)
        self.assertIn('"evospec_start"', plugin)
        self.assertIn('"evospec_assessed_start"', plugin)
        self.assertIn('"evospec_continuation"', plugin)
        self.assertIn("deriveContinuationDirective", plugin)
        self.assertIn("runtime.session.hook(\"context\"", plugin)
        self.assertIn("runtime.session.get({ sessionID: context.sessionID })", plugin)
        self.assertNotIn("@opencode-ai/plugin", plugin)
        self.assertNotIn("@opencode/plugin", plugin)
        self.assertNotIn("__EVOSPEC_", plugin)
        self.assertNotIn("opencode-v1", plugin)
        self.assertIn("provider-neutral protocol request", plugin)
        self.assertIn("additionalProperties: false", plugin)
        self.assertTrue(all("opencode-v2" in resources[f".opencode/commands/{name}"] for name in COMMANDS))
        self.assertIn("opencode-v2", resources[SKILL])
        self.assertIn("opencode-v2", resources[CONTINUATION])

    def test_v2_plugin_registers_and_serializes_provider_neutral_typed_actions(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.ts").write_text(_resources()[PLUGIN], encoding="utf-8")
            (root / "continuation.mjs").write_text(_resources()[CONTINUATION], encoding="utf-8")
            (root / "evidence.md").write_text("attributable project evidence\n", encoding="utf-8")
            runner = root / "runner.ts"
            runner.write_text(
                '''import plugin from "./plugin.ts"

const definitions = []
const requests = []
const commands = []
const writes = []
let canonicalContext = { context: {
  revision: 13,
  initialization_state: "INITIALIZED",
  lifecycle: ["IN_PROGRESS", "IMPLEMENT"],
  legal_actions: ["progress"],
  guidance: [{ action: "progress", allowed: true, intervention: "AUTOMATIC" }],
  completed_terminal: false,
  stale: false,
  pending_intake: false,
  side_work_active: false,
  accepted_capabilities: [],
  pending_discovery_recovery: null,
  pending_capability_selection: null,
  work_queue: { next_code: "ACTIVE", next: null, active: { id: "work-1" } },
  autonomy_envelope: null,
  outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "Core permits progress", material_decision: null },
} }
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const runtime = {
  tool: {
    transform: async (callback) => callback({ add: (definition) => definitions.push(definition) }),
    list: async () => definitions.map((definition) => ({ id: definition.name })),
  },
  session: {
    get: async ({ sessionID }) => {
      assert(sessionID === "ses-smoke", "tool execution forwards the current session id")
      return { location: { directory: process.cwd() } }
    },
    hook: async (name, callback) => { assert(name === "context", "plugin registers the V2 context hook"); runtime.contextHook = callback },
  },
}
await plugin.setup(runtime)
assert(definitions.length === 25, "raw protocol tool and all typed helpers register")
const names = new Set(definitions.map((definition) => definition.name))
for (const name of ["evospec", "evospec_start", "evospec_assessed_start", "evospec_continuation", "evospec_wait", "evospec_retry", "evospec_context", "evospec_work_list", "evospec_work_next", "evospec_work_activate", "evospec_work_state", "evospec_work_relation", "evospec_recover_discovery", "evospec_select_capability", "evospec_progress", "evospec_accepted_task_progress", "evospec_review", "evospec_verify", "evospec_continue", "evospec_clarify", "evospec_structure_material_clarification", "evospec_recover_legacy_non_material_detail", "evospec_converge", "evospec_irreversible_authorize", "evospec_irreversible_execute"]) assert(names.has(name), `missing registered tool ${name}`)
const recoveryTool = definitions.find((definition) => definition.name === "evospec_recover_legacy_non_material_detail")
assert(recoveryTool.input.additionalProperties === false, "typed recovery tool rejects undeclared replacement prose")
assert(JSON.stringify(recoveryTool.input.required) === JSON.stringify(["expected_revision", "repository_id", "feature_id", "change_id", "legacy_detail"]), "typed recovery tool requires exact recovery identity and detail only")
const hookEvent = { system: [] }
runtime.contextHook(hookEvent)
assert(hookEvent.system[0].text.includes("evospec_context"), "context hook guides agents to canonical EvoSpec context")
assert(hookEvent.system[0].text.includes("SAFE_AUTOMATIC_OPERATION"), "context hook installs outcome-driven continuation defaults")
let exitStatus = 0
Object.defineProperty(Bun, "spawn", { value(command, options) {
  commands.push({ command, cwd: options.cwd })
  if (options.stdin) writes.push(options.stdin.text().then((request) => requests.push(request)))
  const output = command.includes("--project") ? "  PROPOSED | 4 | capability | RESEARCH  " : `  ${JSON.stringify(canonicalContext)}  `
  return { stdout: new Blob([output]).stream(), stderr: new Blob(["  error  "]).stream(), exited: Promise.resolve(exitStatus) }
} })
const call = async (name, args) => {
  const definition = definitions.find((item) => item.name === name)
  assert(definition, `tool ${name} is registered`)
  return definition.execute(args, { sessionID: "ses-smoke", signal: new AbortController().signal })
}
await call("evospec", { request: "{\\"action\\":\\"progress\\",\\"expected_revision\\":7}" })
await call("evospec_context", {})
await call("evospec_progress", { expected_revision: 7, current_action: null, focus: "café", completed_work: [], current_work: null, pending_work: [], resume_point: null })
await call("evospec_review", { expected_revision: 8, verdict: "WARN", findings: ["finding"], safe_to_proceed: true })
await call("evospec_verify", { expected_revision: 9, evidence: ["tests"], sufficient: true })
await call("evospec_continue", { expected_revision: 10 })
await call("evospec_clarify", { expected_revision: 11, answers: [{ question_id: "question-1", answer: "answer" }] })
await call("evospec_structure_material_clarification", { expected_revision: 11, repository_id: "repo-1", feature_id: "feature-1", change_id: "change-1", legacy_detail: "legacy", decision: "select channel", options: ["email", "download"] })
await call("evospec_recover_legacy_non_material_detail", { expected_revision: 11, repository_id: "repo-1", feature_id: "feature-1", change_id: "change-1", legacy_detail: "operational note" })
await call("evospec_converge", { expected_revision: 12, change_id: "change-1", product_truth_changed: false, product_truth_content: null, confidence: "HIGH", evidence: [{ source: "agent", reference: "tests", feature_id: null, living_spec_id: null }], rationale: "complete" })
await call("evospec_irreversible_authorize", { expected_revision: 25, operation_kind: "GIT_HISTORY_REWRITE", target_ref: "refs/heads/master", expected_remote_sha: "a".repeat(40), rewrite_policy: "NORMALIZE_MISMATCHED_IDENTITIES", target_identity_name: "Synthetic Contributor", target_identity_email: "security-researcher@example.invalid", expected_result_sha: "b".repeat(40), commit_rewrites: [{ old_sha: "a".repeat(40), new_sha: "b".repeat(40), identity_normalized: true }], purpose: "normalize approved author identity", force_with_lease: true })
await call("evospec_irreversible_execute", { expected_revision: 26, authorization_id: "irreversible-1" })
await call("evospec_recover_discovery", { expected_revision: 13, recovery_id: "recovery-1", candidate: "candidate-1", evidence_ids: ["evidence-1", "evidence-2"] })
await call("evospec_select_capability", { expected_revision: 14, selection_id: "selection-1", choice: "CREATE_STANDALONE_CAPABILITY" })
await call("evospec_work_list", { expected_revision: 20, filter: "actionable" })
await call("evospec_work_next", { expected_revision: 21 })
await call("evospec_work_activate", { expected_revision: 22, work_id: "work-change-1" })
await call("evospec_work_state", { expected_revision: 23, work_id: "work-change-1", operational_state: "FROZEN", reason: "waiting" })
await call("evospec_work_relation", { expected_revision: 24, operation: "remove", predecessor_id: "work-change-1", successor_id: "work-change-2", kind: "HARD" })
const continuation = await call("evospec_continuation", { explicit_task: null, operation: null, paths: [], irreversible: false, external_status: null, failure_code: null, attempt: 0 })
assert(JSON.parse(continuation.content).directive === "CONTINUE", "continuation tool derives a deterministic directive from canonical context")
canonicalContext.context.outcome = { kind: "EXTERNAL_WAIT", message: "external work is pending", material_decision: null }
canonicalContext.context.autonomy_envelope = { external_wait_allowed: true, retry_budget: 2 }
const waited = await call("evospec_wait", { delay_ms: 0, external_status: "PENDING" })
assert(JSON.parse(waited.content).state === "PENDING", "V2 wait helper distinguishes an external pending state")
canonicalContext.context.outcome = { kind: "SAFE_AUTOMATIC_OPERATION", message: "Core permits progress", material_decision: null }
const retry = await call("evospec_retry", { failure_code: "ECONNRESET", attempt: 0, operation: null, paths: [], irreversible: false })
assert(JSON.parse(retry.content).retry.eligible === true, "V2 retry helper consumes the current Core retry budget")
canonicalContext.context.autonomy_envelope = null
const assessed = await call("evospec_assessed_start", { expected_revision: 13, operation: "new", intent: "Document assessed start", assessment: {
  operation: "NEW", classification: "FEATURE", confidence: "HIGH", capability_state: "PLANNED",
  selected_capability: "Document assessed start", candidate_capabilities: [], selected_feature_id: null,
  evidence: [{ source: "repository", reference: "evidence.md", feature_id: null, living_spec_id: null }],
  material_ambiguities: [], recommended_depth: { review: "NORMAL", verification: "NORMAL", convergence: "NORMAL" },
  rationale: "repository evidence supports a planned capability",
} })
assert(JSON.parse(assessed.content).context.revision === 13, "assessed intake returns Core's canonical context")
let rejected = false
try {
  await call("evospec_assessed_start", { expected_revision: 13, operation: "new", intent: "Document assessed start", assessment: {
    operation: "NEW", classification: "FEATURE", confidence: "HIGH", capability_state: "PLANNED",
    selected_capability: "Document assessed start", candidate_capabilities: [], selected_feature_id: null,
    evidence: [{ source: "repository", reference: "missing-evidence.md", feature_id: null, living_spec_id: null }],
    material_ambiguities: [], recommended_depth: { review: "NORMAL", verification: "NORMAL", convergence: "NORMAL" },
    rationale: "missing repository evidence",
  } })
} catch { rejected = true }
assert(rejected, "assessed helper rejects evidence paths that do not exist in the executing repository")
rejected = false
try {
  await call("evospec_assessed_start", { expected_revision: 13, operation: "new", intent: "Document assessed start", assessment: {
    operation: "NEW", classification: "FEATURE", confidence: "HIGH", capability_state: "ESTABLISHED",
    selected_capability: "fabricated Core feature", candidate_capabilities: ["fabricated Core feature"], selected_feature_id: "feature-fabricated",
    evidence: [{ source: "repository", reference: "evidence.md", feature_id: null, living_spec_id: null }],
    material_ambiguities: [], recommended_depth: { review: "NORMAL", verification: "NORMAL", convergence: "NORMAL" },
    rationale: "fabricated established identity",
  } })
} catch { rejected = true }
assert(rejected, "assessed helper cannot invent an established Core capability identity")
const started = await call("evospec_start", { operation: "new", intent: "launch café" })
assert(JSON.parse(started.content).intake === "PROPOSED | 4 | capability | RESEARCH", "intake returns CLI output")
assert(JSON.stringify(JSON.parse(started.content).context) === JSON.stringify(canonicalContext), "intake returns fresh canonical context")
await Promise.all(writes)
const actual = requests.map(JSON.parse)
assert(JSON.stringify(actual) === JSON.stringify([
  { action: "progress", expected_revision: 7 },
  { action: "context" },
  { action: "progress", expected_revision: 7, current_action: null, focus: "café", completed_work: [], current_work: null, pending_work: [], resume_point: null },
  { action: "review", expected_revision: 8, review: { verdict: "WARN", findings: ["finding"], safe_to_proceed: true } },
  { action: "verify", expected_revision: 9, verification: { evidence: ["tests"], sufficient: true } },
  { action: "continue", expected_revision: 10 },
  { action: "clarify", expected_revision: 11, answers: [{ question_id: "question-1", answer: "answer" }] },
  { action: "structure_material_clarification", expected_revision: 11, repository_id: "repo-1", feature_id: "feature-1", change_id: "change-1", legacy_detail: "legacy", decision: "select channel", options: ["email", "download"] },
  { action: "recover_legacy_non_material_detail", expected_revision: 11, repository_id: "repo-1", feature_id: "feature-1", change_id: "change-1", legacy_detail: "operational note" },
  { action: "converge", expected_revision: 12, assessment: { change_id: "change-1", product_truth_changed: false, product_truth_content: null, confidence: "HIGH", evidence: [{ source: "agent", reference: "tests", feature_id: null, living_spec_id: null }], rationale: "complete" } },
  { action: "authorize_irreversible", expected_revision: 25, operation_kind: "GIT_HISTORY_REWRITE", target_ref: "refs/heads/master", expected_remote_sha: "a".repeat(40), rewrite_policy: "NORMALIZE_MISMATCHED_IDENTITIES", target_identity_name: "Synthetic Contributor", target_identity_email: "security-researcher@example.invalid", expected_result_sha: "b".repeat(40), commit_rewrites: [{ old_sha: "a".repeat(40), new_sha: "b".repeat(40), identity_normalized: true }], purpose: "normalize approved author identity", force_with_lease: true },
  { action: "execute_irreversible", expected_revision: 26, authorization_id: "irreversible-1" },
  { action: "recover_discovery", expected_revision: 13, recovery_id: "recovery-1", candidate: "candidate-1", evidence_ids: ["evidence-1", "evidence-2"] },
  { action: "select_capability", expected_revision: 14, selection_id: "selection-1", choice: "CREATE_STANDALONE_CAPABILITY" },
  { action: "list_work", expected_revision: 20, filter: "actionable" },
  { action: "next_work", expected_revision: 21 },
  { action: "activate_work", expected_revision: 22, work_id: "work-change-1" },
  { action: "set_work_state", expected_revision: 23, work_id: "work-change-1", operational_state: "FROZEN", reason: "waiting" },
  { action: "remove_work_relation", expected_revision: 24, predecessor_id: "work-change-1", successor_id: "work-change-2", kind: "HARD" },
  { action: "context" },
  { action: "context" },
  { action: "context" },
  { action: "context" },
  { action: "context" },
  { action: "new", expected_revision: 13, intent: "Document assessed start", assessment: {
    operation: "NEW", classification: "FEATURE", confidence: "HIGH", capability_state: "PLANNED",
    selected_capability: "Document assessed start", candidate_capabilities: [], selected_feature_id: null,
    evidence: [{ source: "repository", reference: "evidence.md", feature_id: null, living_spec_id: null }],
    material_ambiguities: [], recommended_depth: { review: "NORMAL", verification: "NORMAL", convergence: "NORMAL" },
    rationale: "repository evidence supports a planned capability",
  } },
  { action: "context" },
  { action: "context" },
  { action: "context" },
  { action: "context" },
]), "typed helpers serialize exact provider-neutral protocol requests")
const expectedCliCommand = __EXPECTED_CLI_COMMAND__
const cliCommand = commands.find((item) => item.command.includes("--project"))
assert(JSON.stringify(cliCommand.command) === JSON.stringify([...expectedCliCommand, "--project", process.cwd(), "new", "launch café"]), "intake forwards session root and natural intent to CLI")
assert(commands.every((item) => item.cwd === process.cwd()), "all subprocesses execute in the current session project")
assert(commands.filter((item) => item.command.includes("--root")).every((item) => item.command.slice(-2).join(" ") === `--root ${process.cwd()}`), "protocol calls forward the current project root")
'''.replace("__EXPECTED_CLI_COMMAND__", json.dumps(list(_cli_command()))),
                encoding="utf-8",
            )
            completed = subprocess.run(
                ("bun", "run", str(runner)), cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_v2_plugin_spawn_hides_windows_children_and_preserves_protocol_io(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.ts").write_text(_resources()[PLUGIN], encoding="utf-8")
            (root / "continuation.mjs").write_text(_resources()[CONTINUATION], encoding="utf-8")
            runner = root / "spawn-runner.ts"
            runner.write_text(
                '''import plugin from "./plugin.ts"

const definitions = []
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const originalPlatform = Object.getOwnPropertyDescriptor(globalThis.process, "platform")
Object.defineProperty(globalThis.process, "platform", { ...originalPlatform, value: "win32" })
const runtime = {
  tool: {
    transform: async (callback) => callback({ add: (definition) => definitions.push(definition) }),
    list: async () => definitions.map((definition) => ({ id: definition.name })),
  },
  session: {
    get: async () => ({ location: { directory: process.cwd() } }),
    hook: async () => {},
  },
}
await plugin.setup(runtime)
let mode = "success"
let currentSignal
let cancellationObserved = false
let cancelSpawned = false
const spawnOptions = []
const writes = []
Object.defineProperty(Bun, "spawn", { value(_command, options) {
  spawnOptions.push(options)
  assert(options.cwd === process.cwd(), "spawn preserves the executing repository cwd")
  assert(options.signal === currentSignal, "spawn propagates the tool AbortSignal")
  assert(options.stdout === "pipe" && options.stderr === "pipe", "stdout and stderr remain separate pipes")
  assert(options.windowsHide === (globalThis.process.platform === "win32"), "windowsHide follows the simulated platform")
  if (options.stdin !== undefined) {
    assert(options.stdin instanceof Blob, "protocol requests remain stdin Blobs")
    writes.push(options.stdin.text())
  }
  if (mode === "cancel") {
    const signal = options.signal
    signal.addEventListener("abort", () => { cancellationObserved = true }, { once: true })
    const waitingStream = () => new ReadableStream({
      start(controller) {
        if (signal.aborted) controller.close()
        else signal.addEventListener("abort", () => controller.close(), { once: true })
      },
    })
    cancelSpawned = true
    return {
      stdout: waitingStream(),
      stderr: waitingStream(),
      exited: new Promise((resolve) => {
        if (signal.aborted) resolve(143)
        else signal.addEventListener("abort", () => resolve(143), { once: true })
      }),
    }
  }
  if (mode === "invalid-utf8") {
    return {
      stdout: new Blob([Uint8Array.of(0xc3, 0x28)]).stream(),
      stderr: new Blob([""]).stream(),
      exited: Promise.resolve(0),
    }
  }
  const stdout = mode === "failure" ? "stdout-only" : JSON.stringify({ context: { marker: "café" } })
  const stderr = mode === "failure" ? "stderr-only" : "diagnostic-only"
  return {
    stdout: new Blob([stdout]).stream(),
    stderr: new Blob([stderr]).stream(),
    exited: Promise.resolve(mode === "failure" ? 1 : 0),
  }
} })
const invoke = (name, args, signal) => {
  currentSignal = signal
  const definition = definitions.find((item) => item.name === name)
  assert(definition, "requested protocol tool is registered")
  return definition.execute(args, { sessionID: "ses-spawn", signal })
}
const windowsController = new AbortController()
const wireRequest = JSON.stringify({ action: "context", marker: "café" })
const successful = await invoke("evospec", { request: wireRequest }, windowsController.signal)
assert(successful.content === JSON.stringify({ context: { marker: "café" } }), "valid UTF-8 stdout is decoded and returned without stderr")
assert(!successful.content.includes("diagnostic-only"), "successful stdout does not include stderr")
assert(await spawnOptions[0].stdin.text() === wireRequest, "normal protocol request serializes exactly to stdin")

mode = "failure"
const failed = await invoke("evospec", { request: '{"action":"context"}' }, windowsController.signal)
assert(failed.content === "stderr-only" && !failed.content.includes("stdout-only"), "failure diagnostics remain separate from stdout")

mode = "invalid-utf8"
let invalidUtf8Rejected = false
try {
  await invoke("evospec", { request: '{"action":"context"}' }, windowsController.signal)
} catch { invalidUtf8Rejected = true }
assert(invalidUtf8Rejected, "malformed UTF-8 remains a strict decoding error")

mode = "cancel"
const cancellationController = new AbortController()
const pendingCancellation = invoke("evospec", { request: '{"action":"context"}' }, cancellationController.signal)
while (!cancelSpawned) await Promise.resolve()
cancellationController.abort()
const cancelled = await pendingCancellation
assert(cancellationObserved && cancelled.content === "", "aborting the propagated signal cancels the subprocess capture")

Object.defineProperty(globalThis.process, "platform", { ...originalPlatform, value: "linux" })
mode = "success"
const nonWindowsController = new AbortController()
const nonWindows = await invoke("evospec", { request: '{"action":"context"}' }, nonWindowsController.signal)
assert(nonWindows.content.includes("café"), "non-Windows protocol execution remains valid")
assert(spawnOptions.length === 5, "Windows, cancellation, and non-Windows calls all reached Bun.spawn")
Object.defineProperty(globalThis.process, "platform", originalPlatform)
await Promise.all(writes)
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                ("bun", "run", str(runner)), cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_v2_plugin_refreshes_stale_revision_and_never_replays_changed_legality(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.ts").write_text(_resources()[PLUGIN], encoding="utf-8")
            (root / "continuation.mjs").write_text(_resources()[CONTINUATION], encoding="utf-8")
            runner = root / "stale-runner.ts"
            runner.write_text(
                '''import plugin from "./plugin.ts"

const definitions = []
const requests = []
const writes = []
const automaticContext = (revision, legalActions) => ({
  context: {
    revision,
    initialization_state: "INITIALIZED",
    lifecycle: ["IN_PROGRESS", "IMPLEMENT"],
    legal_actions: legalActions,
    guidance: legalActions.map((action) => ({ action, allowed: true, intervention: "AUTOMATIC" })),
    completed_terminal: false,
    stale: false,
    change_id: "change-1",
  },
})
const responses = [
  automaticContext(4, ["progress"]),
  { error: { code: "STALE_REVISION", detail: "advanced" } },
  automaticContext(5, ["progress"]),
  { result: { code: "PROGRESS" }, ...automaticContext(6, ["review"]) },
  { error: { code: "STALE_REVISION", detail: "advanced again" } },
  automaticContext(7, ["progress"]),
]
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const runtime = {
  tool: {
    transform: async (callback) => callback({ add: (definition) => definitions.push(definition) }),
    list: async () => definitions.map((definition) => ({ id: definition.name })),
  },
  session: {
    get: async () => ({ location: { directory: process.cwd() } }),
    hook: async () => {},
  },
}
await plugin.setup(runtime)
let index = 0
Object.defineProperty(Bun, "spawn", { value(_command, options) {
  if (options.stdin) writes.push(options.stdin.text().then((value) => requests.push(JSON.parse(value))))
  const response = JSON.stringify(responses[index++])
  return { stdout: new Blob([response]).stream(), stderr: new Blob([""]).stream(), exited: Promise.resolve(0) }
} })
const call = async (name, args) => definitions.find((item) => item.name === name).execute(args, {
  sessionID: "ses-race", signal: new AbortController().signal,
})
await call("evospec_context", {})
const retried = await call("evospec_progress", {
  expected_revision: 4, current_action: "implement", focus: "race", completed_work: [],
  current_work: "work", pending_work: [], resume_point: "retry",
})
assert(JSON.parse(retried.content).result.code === "PROGRESS", "still-legal stale progress is reevaluated and retried at the refreshed revision")
const notReplayed = await call("evospec_review", {
  expected_revision: 6, verdict: "PASS", findings: [], safe_to_proceed: true,
})
assert(JSON.parse(notReplayed.content).error.code === "STALE_REVISION", "changed legality returns the fresh stale context without replay")
await Promise.all(writes)
assert(JSON.stringify(requests.map(({ action, expected_revision }) => [action, expected_revision])) === JSON.stringify([
  ["context", undefined], ["progress", 4], ["context", undefined], ["progress", 5],
  ["review", 6], ["context", undefined],
]), "the stale review was not executed after Core removed it from legal_actions")
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                ("bun", "run", str(runner)), cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_v2_plugin_initializes_only_for_explicit_task_in_the_executing_repository(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.ts").write_text(_resources()[PLUGIN], encoding="utf-8")
            (root / "continuation.mjs").write_text(_resources()[CONTINUATION], encoding="utf-8")
            runner = root / "init-runner.ts"
            runner.write_text(
                '''import plugin from "./plugin.ts"

const definitions = []
const requests = []
const writes = []
const outputs = [
  { context: {
    revision: 0, initialization_state: "UNINITIALIZED", lifecycle: null,
    legal_actions: ["init", "context"],
    guidance: [{ action: "init", allowed: true, intervention: "USER" }],
    completed_terminal: false, stale: false,
    outcome: { kind: "STRUCTURAL_DEFECT", message: "initialization required", material_decision: null },
  } },
  { context: {
    revision: 0, initialization_state: "UNINITIALIZED", lifecycle: null,
    legal_actions: ["init", "context"],
    guidance: [{ action: "init", allowed: true, intervention: "USER" }],
    completed_terminal: false, stale: false,
    outcome: { kind: "STRUCTURAL_DEFECT", message: "initialization required", material_decision: null },
  } },
  { result: { code: "INITIALIZED", revision: 1 }, context: {
    revision: 1, initialization_state: "INITIALIZED", lifecycle: null,
    legal_actions: ["context"], guidance: [], completed_terminal: false, stale: false,
    pending_intake: false, side_work_active: false,
    outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "initialized", material_decision: null },
  } },
  { context: {
    revision: 1, initialization_state: "INITIALIZED", lifecycle: null,
    legal_actions: ["context"], guidance: [], completed_terminal: false, stale: false,
    pending_intake: false, side_work_active: false,
    outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "initialized", material_decision: null },
  } },
]
const assert = (condition, message) => { if (!condition) throw new Error(message) }
const runtime = {
  tool: {
    transform: async (callback) => callback({ add: (definition) => definitions.push(definition) }),
    list: async () => definitions.map((definition) => ({ id: definition.name })),
  },
  session: {
    get: async () => ({ location: { directory: process.cwd() } }),
    hook: async () => {},
  },
}
await plugin.setup(runtime)
let index = 0
Object.defineProperty(Bun, "spawn", { value(command, options) {
  if (options.stdin) writes.push(options.stdin.text().then((value) => requests.push(JSON.parse(value))))
  const output = command.includes("--project") ? "PROPOSED | 2 | feature | RESEARCH" : JSON.stringify(outputs[index++])
  return { stdout: new Blob([output]).stream(), stderr: new Blob([""]).stream(), exited: Promise.resolve(0) }
} })
const call = async (name, args) => definitions.find((item) => item.name === name).execute(args, {
  sessionID: "ses-init", signal: new AbortController().signal,
})
const noTask = await call("evospec_continuation", { explicit_task: null, operation: null, paths: [], irreversible: false, external_status: null, failure_code: null, attempt: 0 })
assert(JSON.parse(noTask.content).directive === "FAIL_CLOSED", "unrelated uninitialized authority is not silently initialized")
const started = await call("evospec_start", { operation: "new", intent: "Explicitly add a smoke capability" })
assert(JSON.parse(started.content).intake === "PROPOSED | 2 | feature | RESEARCH", "explicit task intake continues after deterministic initialization")
await Promise.all(writes)
assert(JSON.stringify(requests.map(({ action, expected_revision }) => [action, expected_revision])) === JSON.stringify([
  ["context", undefined], ["context", undefined], ["init", 0], ["context", undefined],
]), "only the explicit session task causes the Core-legal initialization mutation")
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                ("bun", "run", str(runner)), cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_assessed_start_uses_the_revision_returned_by_initialization(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plugin.ts").write_text(_resources()[PLUGIN], encoding="utf-8")
            (root / "continuation.mjs").write_text(_resources()[CONTINUATION], encoding="utf-8")
            (root / "evidence.md").write_text("repository evidence\n", encoding="utf-8")
            runner = root / "assessed-init-runner.ts"
            runner.write_text(
                '''import plugin from "./plugin.ts"

const definitions = []
const requests = []
const writes = []
const uninitialized = {
  revision: 9, initialization_state: "UNINITIALIZED", lifecycle: null,
  legal_actions: ["init", "context"],
  guidance: [{ action: "init", allowed: true, intervention: "USER" }],
  completed_terminal: false, stale: false,
  outcome: { kind: "STRUCTURAL_DEFECT", message: "initialization required", material_decision: null },
}
const initialized = {
  revision: 10, initialization_state: "INITIALIZED", lifecycle: null,
  legal_actions: ["context", "new"], guidance: [],
  completed_terminal: false, stale: false, pending_intake: false,
  side_work_active: false, accepted_capabilities: [],
  outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "initialized", material_decision: null },
}
const started = {
  revision: 11, initialization_state: "INITIALIZED", lifecycle: ["IN_PROGRESS", "RESEARCH"],
  legal_actions: ["context"], guidance: [], completed_terminal: false, stale: false,
  pending_intake: false, side_work_active: false, accepted_capabilities: [],
  outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "started", material_decision: null },
}
const outputs = [
  { context: uninitialized },
  { result: { code: "INITIALIZED", revision: 10 }, context: initialized },
  { result: { code: "PROPOSED", revision: 11 }, context: started },
]
const runtime = {
  tool: {
    transform: async (callback) => callback({ add: (definition) => definitions.push(definition) }),
    list: async () => definitions.map((definition) => ({ id: definition.name })),
  },
  session: {
    get: async () => ({ location: { directory: process.cwd() } }),
    hook: async () => {},
  },
}
await plugin.setup(runtime)
Object.defineProperty(Bun, "spawn", { value(_command, options) {
  if (options.stdin) writes.push(options.stdin.text().then((value) => requests.push(JSON.parse(value))))
  return {
    stdout: new Blob([JSON.stringify(outputs.shift())]).stream(),
    stderr: new Blob([""]).stream(), exited: Promise.resolve(0),
  }
} })
const tool = definitions.find((definition) => definition.name === "evospec_assessed_start")
if (!tool) throw new Error("assessed-start tool was not registered")
const result = await tool.execute({
  expected_revision: 9,
  operation: "new",
  intent: "Explicitly document the capability",
  assessment: {
    operation: "NEW", classification: "FEATURE", confidence: "HIGH", capability_state: "PLANNED",
    selected_capability: "Document capability", candidate_capabilities: [], selected_feature_id: null,
    evidence: [{ source: "repository", reference: "evidence.md", feature_id: null, living_spec_id: null }],
    material_ambiguities: [],
    recommended_depth: { review: "NORMAL", verification: "NORMAL", convergence: "NORMAL" },
    rationale: "repository evidence supports a planned capability",
  },
}, { sessionID: "ses-assessed-init", signal: new AbortController().signal })
await Promise.all(writes)
if (result.isError === true) throw new Error(JSON.stringify(result))
if (requests.map((request) => request.action).join(",") !== "context,init,new") {
  throw new Error(`unexpected Core request sequence: ${JSON.stringify(requests)}`)
}
if (requests[2].expected_revision !== 10) {
  throw new Error(`assessed intake reused a stale revision: ${JSON.stringify(requests[2])}`)
}
''',
                encoding="utf-8",
            )
            completed = subprocess.run(
                ("bun", "run", str(runner)), cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_clean_install_writes_hashed_manifest_and_preserves_foreign_files_and_config(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "opencode.json"
            config_content = '{\n  "$schema": "https://opencode.ai/config.json",\n  "instructions": ["custom.md"],\n  "other": true\n}\n'
            config.write_text(config_content, encoding="utf-8")
            foreign = root / ".opencode" / "commands" / "speckit.plan.md"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("foreign Spec-Kit command\n", encoding="utf-8")
            specify = root / ".specify" / "presets" / "lean" / "README.md"
            specify.parent.mkdir(parents=True)
            specify.write_text("foreign Spec-Kit material\n", encoding="utf-8")
            before = {config: config.read_bytes(), foreign: foreign.read_bytes(), specify: specify.read_bytes()}

            result = self.registry.integrate(root, "opencode", dry_run=False, remove=False)

            self.assertEqual(result.conflicts, ())
            self.assertIn(PLUGIN, result.changed)
            self.assertIn(MANIFEST, result.changed)
            manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
            packaged = _resource_bytes()
            self.assertEqual(manifest["schema"], 1)
            self.assertEqual(manifest["integration"], "opencode")
            self.assertEqual(
                manifest["resources"],
                {path: sha256(content).hexdigest() for path, content in sorted(packaged.items())},
            )
            self.assertTrue((root / PLUGIN).is_file())
            self.assertTrue((root / SKILL).is_file())
            self.assertEqual(
                len(tuple((root / ".opencode" / "commands").glob("evospec.*.md"))),
                len(COMMANDS),
            )
            self.assertFalse((root / ".opencode" / "tools" / "evospec.ts").exists())
            self.assertFalse((root / ".opencode" / "instructions" / "evospec.md").exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(
                json.loads((integration_registry_path(root)).read_text(encoding="utf-8")),
                {"providers": ["opencode"], "schema": 1},
            )

    def test_install_is_idempotent_and_inspection_is_read_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            second = self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            inspection = self.provider.inspect(root)

            self.assertTrue(first.changed)
            self.assertEqual(second.changed, ())
            self.assertEqual(inspection.state, "CURRENT")
            self.assertEqual(inspection.manifest, "CURRENT")
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_dry_run_previews_manifest_and_registry_without_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            result = self.registry.integrate(root, "opencode", dry_run=True, remove=False)

            self.assertIn(MANIFEST, result.changed)
            self.assertIn(".evospec/integrations.json", result.changed)
            self.assertEqual(tuple(root.iterdir()), ())

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            integrate(root)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            result = self.registry.refresh(root, dry_run=True)

            self.assertEqual(result.changed, (".evospec/integrations.json",))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            relative_root = Path(os.path.relpath(root, Path.cwd()))

            result = self.registry.integrate(relative_root, "opencode", dry_run=True, remove=False)

            self.assertIn(".evospec/integrations.json", result.changed)
            self.assertEqual(tuple(root.iterdir()), ())

    def test_install_claims_exact_v2_resources_without_rewriting_them(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaged = _resource_bytes()
            tracked_resources = (SKILL, *(f".opencode/commands/{name}" for name in COMMANDS))
            before: dict[Path, bytes] = {}
            for target in tracked_resources:
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(packaged[target])
                before[path] = path.read_bytes()

            result = self.registry.integrate(root, "opencode", dry_run=False, remove=False)

            self.assertEqual(result.conflicts, ())
            self.assertIn(PLUGIN, result.changed)
            self.assertIn(MANIFEST, result.changed)
            self.assertNotIn(SKILL, result.changed)
            self.assertTrue(set(tracked_resources).isdisjoint(result.changed))
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(self.provider.inspect(root).state, "CURRENT")

    def test_refresh_restores_missing_manifest_owned_resource_and_preserves_foreign_files(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            target = root / PLUGIN
            target.unlink()
            foreign = root / ".opencode" / "tools" / "foreign.ts"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("export default {}\n", encoding="utf-8")

            result = self.registry.refresh(root, dry_run=False)

            self.assertEqual(result.conflicts, ())
            self.assertEqual(result.changed, (PLUGIN,))
            self.assertTrue(target.is_file())
            self.assertEqual(foreign.read_text(encoding="utf-8"), "export default {}\n")
            self.assertEqual(self.provider.inspect(root).state, "CURRENT")

    def test_refresh_updates_only_manifest_verified_previous_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            previous = _resource_bytes()
            updated = dict(previous)
            updated[PLUGIN] = previous[PLUGIN] + b"\n// V2 resource update\n"

            with patch("evospec.opencode_integration._resource_bytes", return_value=updated):
                result = self.registry.refresh(root, dry_run=False)
                inspection = self.provider.inspect(root)

            self.assertEqual(result.conflicts, ())
            self.assertIn(PLUGIN, result.changed)
            self.assertIn(b"V2 resource update", (root / PLUGIN).read_bytes())
            self.assertEqual(inspection.state, "CURRENT")

    def test_user_modified_managed_resource_blocks_refresh_and_remove(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            integrate(root)
            target = root / PLUGIN
            target.write_bytes(target.read_bytes() + b"\nuser edit\n")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            refreshed = integrate(root)
            removed = integrate(root, remove=True)
            inspection = self.provider.inspect(root)

            self.assertEqual(refreshed.conflicts, (PLUGIN,))
            self.assertEqual(removed.conflicts, (PLUGIN,))
            self.assertEqual(inspection.state, "CONFLICT")
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_unmanifested_v1_install_is_never_adopted_migrated_or_deleted(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_tool = root / ".opencode" / "tools" / "evospec.ts"
            legacy_tool.parent.mkdir(parents=True)
            legacy_tool.write_text("// EvoSpec-managed: opencode-v1\nexport default {}\n", encoding="utf-8")
            legacy_command = root / ".opencode" / "commands" / "evospec.start.md"
            legacy_command.parent.mkdir(parents=True, exist_ok=True)
            legacy_command.write_text("<!-- EvoSpec-managed: opencode-v1 -->\nlegacy command\n", encoding="utf-8")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            refreshed = self.registry.refresh(root, dry_run=False)
            installed = self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            removed = self.registry.integrate(root, "opencode", dry_run=False, remove=True)

            self.assertIn(".opencode/tools/evospec.ts", refreshed.conflicts)
            self.assertEqual(installed.conflicts, refreshed.conflicts)
            self.assertEqual(removed.conflicts, refreshed.conflicts)
            self.assertFalse((root / PLUGIN).exists())
            self.assertFalse((root / MANIFEST).exists())
            self.assertFalse(integration_registry_path(root).exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_unmanifested_v2_target_collision_and_unsafe_hardlink_are_preserved(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / ".opencode" / "commands" / COMMANDS[0]
            target.parent.mkdir(parents=True)
            target.write_text("user command\n", encoding="utf-8")
            original = target.read_bytes()

            result = integrate(root)

            self.assertEqual(result.conflicts, (target.relative_to(root).as_posix(),))
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse((root / MANIFEST).exists())

        with TemporaryDirectory() as temporary, TemporaryDirectory() as external_temporary:
            root = Path(temporary)
            external = Path(external_temporary) / "external.ts"
            external.write_text("user-owned\n", encoding="utf-8")
            target = root / PLUGIN
            target.parent.mkdir(parents=True)
            os.link(external, target)

            result = integrate(root)

            self.assertEqual(result.conflicts, (PLUGIN,))
            self.assertEqual(external.read_text(encoding="utf-8"), "user-owned\n")
            self.assertFalse((root / MANIFEST).exists())

    def test_existing_opencode_json_is_never_read_or_mutated(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "opencode.json"
            original = "{ malformed but user-owned\n"
            config.write_text(original, encoding="utf-8")

            result = integrate(root)

            self.assertEqual(result.conflicts, ())
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertFalse((root / ".opencode" / "instructions" / "evospec.md").exists())

    def test_remove_deletes_only_manifest_owned_resources_and_preserves_foreign_material(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.registry.integrate(root, "opencode", dry_run=False, remove=False)
            foreign_paths = {
                root / ".opencode" / "commands" / "speckit.plan.md": "foreign Spec-Kit command\n",
                root / ".opencode" / "skills" / "foreign" / "SKILL.md": "foreign skill\n",
                root / ".opencode" / "tools" / "foreign.ts": "foreign tool\n",
                root / ".specify" / "presets" / "lean" / "README.md": "foreign Spec-Kit material\n",
            }
            for path, content in foreign_paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            config = root / "opencode.json"
            config.write_text('{"instructions":["user.md"],"other":true}\n', encoding="utf-8")
            before = {path: path.read_bytes() for path in (*foreign_paths, config)}

            result = self.registry.integrate(root, "opencode", dry_run=False, remove=True)

            self.assertEqual(result.conflicts, ())
            self.assertIn(MANIFEST, result.changed)
            self.assertFalse((root / PLUGIN).exists())
            self.assertFalse((root / MANIFEST).exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(
                json.loads(integration_registry_path(root).read_text(encoding="utf-8")),
                {"providers": [], "schema": 1},
            )

    def test_corrupt_or_unsafe_manifest_blocks_all_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            integrate(root)
            manifest = root / MANIFEST
            manifest.write_text('{"schema":1,"integration":"opencode","resources":{"../README.md":"' + "0" * 64 + '"}}', encoding="utf-8")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            result = integrate(root)

            self.assertEqual(result.conflicts, (MANIFEST,))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_manifest_write_failure_rolls_back_resources_and_created_directories(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original_write_bytes = Path.write_bytes

            def fail_manifest(path: Path, data: bytes) -> int:
                if path == root / MANIFEST:
                    raise OSError("injected manifest write failure")
                return original_write_bytes(path, data)

            with (
                patch.object(Path, "write_bytes", new=fail_manifest),
                self.assertRaisesRegex(OSError, "injected manifest write failure"),
            ):
                integrate(root)

            self.assertEqual(tuple(root.iterdir()), ())

    def test_registry_write_failure_rolls_back_resources_manifest_and_directories(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = integration_registry_path(root)
            original_write_bytes = Path.write_bytes

            def fail_registry(path: Path, data: bytes) -> int:
                if path == registry_path:
                    raise OSError("injected registry write failure")
                return original_write_bytes(path, data)

            with (
                patch.object(Path, "write_bytes", new=fail_registry),
                self.assertRaisesRegex(OSError, "injected registry write failure"),
            ):
                self.registry.integrate(root, "opencode", dry_run=False, remove=False)

            self.assertEqual(tuple(root.iterdir()), ())

    def test_remove_failure_rolls_back_every_deleted_file(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            integrate(root)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            fail_path = root / f".opencode/commands/{COMMANDS[0]}"
            original_unlink = Path.unlink

            def fail_first_delete(path: Path, missing_ok: bool = False) -> None:
                if path == fail_path:
                    raise OSError("injected resource delete failure")
                original_unlink(path, missing_ok=missing_ok)

            with (
                patch.object(Path, "unlink", new=fail_first_delete),
                self.assertRaisesRegex(OSError, "injected resource delete failure"),
            ):
                integrate(root, remove=True)

            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_remove_is_noop_for_empty_or_unrelated_roots(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(integrate(root, remove=True).changed, ())
            self.assertEqual(tuple(root.iterdir()), ())
            config = root / "opencode.json"
            original = '{\n  "other": true\n}\n'
            config.write_text(original, encoding="utf-8")
            self.assertEqual(integrate(root, remove=True).changed, ())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_context_projects_only_core_offered_pending_resolution_identifiers(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "evidence-one.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            (root / "evidence-two.md").write_text("External Blocker Reconciliation", encoding="utf-8")
            overflow = root / "overflow"
            overflow.mkdir()
            for index in range(129):
                (overflow / f"z-{index:03}.txt").write_text("noise", encoding="utf-8")
            workflow = WorkflowService.open(root)
            workflow.init()
            workflow.new("External Blocker Reconciliation")
            recovery = CoreService.open(root).recover().pending_discovery_recovery
            if recovery is None:
                self.fail("expected Core-issued discovery recovery")

            projected_recovery = context_json(project_context(workflow))["pending_discovery_recovery"]

            self.assertEqual(
                projected_recovery,
                {
                    "recovery_id": str(recovery.id),
                    "candidates": [
                        {
                            "candidate": candidate,
                            "evidence_ids": [
                                str(item.id) for item in recovery.evidence if item.candidate == candidate
                            ],
                        }
                        for candidate in recovery.candidates
                    ],
                },
            )
            self.assertNotIn("intent", json.dumps(projected_recovery))
            self.assertNotIn("path", json.dumps(projected_recovery))
            self.assertNotIn("fingerprint", json.dumps(projected_recovery))

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            workflow.init()
            core = CoreService.open(root)
            state = core.record_feature(
                core.recover(), Feature(FeatureId("feature-export-a"), "Export delivery", LivingSpecId("spec-export-a")),
            )
            core.record_feature(
                state, Feature(FeatureId("feature-export-b"), "Export delivery", LivingSpecId("spec-export-b")),
            )
            workflow.change("Change export delivery behavior")
            selection = CoreService.open(root).recover().pending_capability_selection
            if selection is None:
                self.fail("expected Core-issued capability selection")

            projected_selection = context_json(project_context(workflow))["pending_capability_selection"]

            self.assertEqual(
                projected_selection,
                {
                    "selection_id": str(selection.id),
                    "choices": ["feature-export-a", "feature-export-b", "CREATE_STANDALONE_CAPABILITY"],
                },
            )
            self.assertNotIn("intent", json.dumps(projected_selection))
            self.assertNotIn("provenance", json.dumps(projected_selection))
