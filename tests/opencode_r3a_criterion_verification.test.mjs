// Synthetic host-boundary tests: no real model, host session, or permission grants.
import test from "node:test"
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile, writeFile, copyFile, mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { pathToFileURL, fileURLToPath } from "node:url"

const source = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/index.ts", import.meta.url)))
const continuation = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/continuation.mjs", import.meta.url)))

test("R3 native test requires exact criterion authority and host permission before deterministic command", async () => {
  const root = await mkdtemp(join(tmpdir(), "evospec-r3a-host-"))
  const commands = []
  const permissions = []
  let decision = async () => {}
  let sessionRoot = root
  let revision = 3
  let expectedEvidence = "python-unittest:tests.test_criteria"
  try {
    const content = (await readFile(source, "utf8"))
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(root, "index.mjs"), content)
    await copyFile(continuation, join(root, "continuation.mjs"))
    globalThis.Bun = {
      spawn(command) {
        commands.push(command)
        const action = command.includes("unittest") ? "test" : commands.filter((item) => item[0] === "python" && item.includes("--root")).length === 3 ? "record" : "context"
        const payload = action === "test"
          ? "One test passed\n" : JSON.stringify(action === "record"
            ? { result: { code: "CRITERION_TEST_RECORDED" }, context: { revision: 4 } }
            : { context: { revision, r3_schema_ready: true, repository_id: "repo-r3",
              accepted_slice: { slice_agreement_ref: "slice-r3", criteria_revision: {
                id: "criteria-r3", criteria: [{ id: "AC-1", expected_evidence: expectedEvidence }],
              } } } })
        return {
          stdout: new ReadableStream({ start(controller) {
            controller.enqueue(new TextEncoder().encode(payload)); controller.close()
          } }),
          stderr: new ReadableStream({ start(controller) { controller.close() } }),
          exited: Promise.resolve(0),
        }
      },
    }
    const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
    const definitions = []
    const runtime = {
      tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })) },
      session: { get: async () => ({ location: { directory: sessionRoot } }), hook: async () => {} },
      permission: { assert: async (request) => { permissions.push(request); await decision(request) } },
    }
    await plugin.setup(runtime)
    const generic = definitions.find((item) => item.name === "evospec")
    const dedicated = definitions.find((item) => item.name === "evospec_criterion_test")
    assert.ok(dedicated)
    assert.deepEqual(dedicated.input.required, Object.keys(dedicated.input.properties))
    const args = { expected_revision: 3, repository_id: "repo-r3", slice_agreement_ref: "slice-r3",
      criteria_revision_id: "criteria-r3", criterion_id: "AC-1", test_kind: "python-unittest",
      test_target: "tests.test_criteria" }
    const call = { runtime, sessionID: "ses-r3", agent: "tester", messageID: "msg-r3", id: "call-r3",
      signal: new AbortController().signal }

    const genericResponse = await generic.execute({ request: JSON.stringify({ action: "criterion_test_result", ...args,
      exit_code: 0, output_sha256: "0".repeat(64) }) }, call)
    assert.equal(JSON.parse(genericResponse.content).error.code, "DEDICATED_TOOL_REQUIRED")
    assert.equal(commands.length, 0)

    const unavailable = runtime.permission
    runtime.permission = undefined
    await assert.rejects(dedicated.execute(args, call), /permission\/session API/)
    runtime.permission = unavailable
    assert.equal(commands.length, 0)

    await assert.rejects(dedicated.execute({ ...args, test_target: "../unsafe" }, call), /approved deterministic/)
    assert.equal(commands.length, 0)

    revision = 4
    await assert.rejects(dedicated.execute(args, call), /stale or differs/)
    assert.equal(permissions.length, 0)
    assert.equal(commands.length, 1, "stale authority permits only the read-only context process")
    commands.length = 0
    revision = 3

    decision = async () => { throw new Error("user denied test execution") }
    await assert.rejects(dedicated.execute(args, call), /denied/)
    assert.equal(permissions.length, 1)
    assert.equal(commands.length, 1, "denied permission must never run tests or publish results")
    commands.length = 0
    decision = async () => {}

    sessionRoot = join(root, "changed-root")
    decision = async () => { sessionRoot = root }
    // Permission context may not rebind a request after the host changes roots.
    sessionRoot = root
    decision = async () => { sessionRoot = join(root, "changed-root") }
    await assert.rejects(dedicated.execute(args, call), /root changed/)
    assert.equal(commands.length, 1)
    commands.length = 0
    sessionRoot = root
    decision = async () => {}

    const successful = await dedicated.execute(args, call)
    const parsed = JSON.parse(successful.content)
    assert.equal(parsed.test.exit_code, 0)
    assert.equal(parsed.recording.result.code, "CRITERION_TEST_RECORDED")
    assert.deepEqual(commands[0].slice(0, 3), ["python", "-m", "evospec.agent_protocol"])
    assert.deepEqual(commands[2], ["python", "-m", "unittest", "-q", "tests.test_criteria"])
    assert.equal(commands.length, 4, "two authority reads, one test, one recording; no stale retry")
    assert.equal(parsed.test.output_sha256,
      createHash("sha256").update("One test passed\n").digest("hex"))
    const resource = JSON.parse(permissions.at(-1).resources[0])
    assert.equal(resource.repository_root, root)
    assert.deepEqual(resource.command, commands[2])
  } finally {
    delete globalThis.Bun
    await rm(root, { recursive: true, force: true })
  }
})
