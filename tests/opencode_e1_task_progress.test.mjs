// Synthetic adapter contract tests use controlled OpenCode and Bun stubs; they do
// not establish native host permission behavior or Core integration.

import test from "node:test"
import assert from "node:assert/strict"
import { readFile, writeFile, copyFile, mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { pathToFileURL, fileURLToPath } from "node:url"

const pluginSource = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/index.ts", import.meta.url)))
const continuationSource = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/continuation.mjs", import.meta.url)))

test("synthetic E1 tool gates accepted Task dispatch with OpenCode permission and never retries stale reports", async () => {
  const directory = await mkdtemp(join(tmpdir(), "evospec-e1c-native-"))
  const recorded = []
  const permissionRequests = []
  let permissionDecision = async () => {}
  let sessionDirectory = directory
  try {
    const raw = await readFile(pluginSource, "utf8")
    const content = raw
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(directory, "index.mjs"), content)
    await copyFile(continuationSource, join(directory, "continuation.mjs"))

    // Bun is the production OpenCode spawn interface. These streams allow us to
    // inspect the exact request and stale behavior without a global install.
    globalThis.Bun = {
      spawn(command, options) {
        recorded.push({ command, options })
        const response = JSON.stringify({ error: {
          code: "STALE_REVISION", expected_revision: 2, actual_revision: 3,
        } })
        const output = new TextEncoder().encode(response)
        return {
          stdout: new ReadableStream({ start(controller) { controller.enqueue(output); controller.close() } }),
          stderr: new ReadableStream({ start(controller) { controller.close() } }),
          exited: Promise.resolve(0),
        }
      },
    }
    const plugin = (await import(pathToFileURL(join(directory, "index.mjs")).href)).default
    const definitions = []
    const runtime = {
      tool: {
        transform: async (callback) => callback({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })),
      },
      session: {
        get: async ({ sessionID }) => {
          assert.equal(sessionID, "ses-e1c-native")
          return { location: { directory: sessionDirectory } }
        },
        hook: async () => {},
      },
      permission: {
        assert: async (request) => {
          permissionRequests.push(request)
          return permissionDecision(request)
        },
      },
    }
    await plugin.setup(runtime)
    const generic = definitions.find((item) => item.name === "evospec")
    const dedicated = definitions.find((item) => item.name === "evospec_accepted_task_progress")
    assert.ok(generic, "the original generic transport stays available")
    assert.ok(dedicated, "the guarded native tool is registered")
    assert.deepEqual(dedicated.input.required, Object.keys(dedicated.input.properties))
    assert.equal(dedicated.input.additionalProperties, false)
    const contextTool = definitions.find((item) => item.name === "evospec_context")
    assert.ok(contextTool, "the read-only context route stays available")
    assert.deepEqual(contextTool.options, { codemode: false }, "canonical recovery stays directly visible to the model")
    assert.equal(dedicated.options?.codemode, undefined, "guarded Task mutations retain Code Mode registration")

    const args = {
      expected_revision: 2, repository_id: "repo-1", task_id: "task-1",
      feature_agreement_ref: "agreement-feature-1", slice_agreement_ref: "agreement-slice-1",
      checkpoint: "Progress observed", statement: "Local tests now run",
      evidence_refs: ["tests/task.test.js"],
    }
    const context = {
      sessionID: "ses-e1c-native",
      agent: "e1-test-agent",
      messageID: "msg-e1c-native",
      id: "call-e1c-native",
      signal: new AbortController().signal,
    }
    const genericOutput = await generic.execute({ request: JSON.stringify({ action: "accepted_task_progress", ...args }) }, context)
    assert.equal(JSON.parse(genericOutput.content).error.code, "DEDICATED_TOOL_REQUIRED")
    assert.equal(recorded.length, 0, "generic tool never invokes the protocol for this mutation")
    assert.equal(permissionRequests.length, 0, "the generic guard does not request this permission")

    const permission = runtime.permission
    runtime.permission = undefined
    const readOutput = await contextTool.execute({}, context)
    assert.equal(JSON.parse(readOutput.content).error.code, "STALE_REVISION")
    assert.equal(recorded.length, 1, "read-only context remains available without the permission API")
    const beforeDenied = recorded.length
    await assert.rejects(
      dedicated.execute(args, context),
      /OpenCode V2 permission API is unavailable/,
    )
    assert.equal(recorded.length, beforeDenied, "missing permission API fails closed before Core")
    runtime.permission = permission

    const missingContextPermissions = permissionRequests.length
    await assert.rejects(
      dedicated.execute(args, { ...context, id: " " }),
      /complete accepted Task permission context/,
    )
    assert.equal(permissionRequests.length, missingContextPermissions, "incomplete host context cannot request permission or dispatch")

    sessionDirectory = null
    await assert.rejects(
      dedicated.execute(args, context),
      /absolute executing repository root/,
    )
    assert.equal(permissionRequests.length, missingContextPermissions, "missing root context cannot request permission or dispatch")
    assert.equal(recorded.length, beforeDenied, "missing root context fails closed before Core")
    sessionDirectory = directory

    const deniedError = new Error("host denied accepted Task progress")
    permissionDecision = async () => { throw deniedError }
    await assert.rejects(dedicated.execute(args, context), (error) => error === deniedError)
    assert.equal(recorded.length, beforeDenied, "denied permissions spawn no Core process")

    let rejectAsk
    permissionDecision = () => new Promise((_, reject) => { rejectAsk = reject })
    const pendingAsk = dedicated.execute(args, context)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(typeof rejectAsk, "function")
    assert.equal(recorded.length, beforeDenied, "pending permission cannot dispatch")
    const rejectedAsk = new Error("user rejected the permission request")
    rejectAsk(rejectedAsk)
    await assert.rejects(pendingAsk, (error) => error === rejectedAsk)
    assert.equal(recorded.length, beforeDenied, "rejected ask propagates without dispatch")

    let allowAsk
    permissionDecision = () => new Promise((resolve) => { allowAsk = resolve })
    const pendingRootCheck = dedicated.execute(args, context)
    await new Promise((resolve) => setImmediate(resolve))
    sessionDirectory = join(directory, "changed-root")
    allowAsk()
    await assert.rejects(pendingRootCheck, /repository changed while accepted Task permission was pending/)
    assert.equal(recorded.length, beforeDenied, "a changed session root fails closed before dispatch")
    sessionDirectory = directory

    permissionDecision = async () => {}
    const dedicatedOutput = await dedicated.execute(args, context)
    assert.equal(JSON.parse(dedicatedOutput.content).error.code, "STALE_REVISION")
    assert.equal(recorded.length, beforeDenied + 1, "allowed Task progress dispatches exactly once")
    assert.equal(recorded.at(-1).options.cwd, directory)
    assert.deepEqual(JSON.parse(await recorded.at(-1).options.stdin.text()), {
      ...args, action: "accepted_task_progress",
    })
    assert.deepEqual(permissionRequests.at(-1), {
      sessionID: context.sessionID,
      agent: context.agent,
      source: { type: "tool", messageID: context.messageID, id: context.id },
      action: "evospec_accepted_task_progress",
      resources: [JSON.stringify({
        operation: "accepted_task_progress",
        repository_root: resolve(directory),
        repository_id: args.repository_id,
        task_id: args.task_id,
      })],
    })
    assert.ok(!recorded.at(-1).command.includes("--unsafe"))
    assert.equal(permissionRequests.at(-1).save, undefined, "permission is not saved or broadened")
  } finally {
    delete globalThis.Bun
    await rm(directory, { recursive: true, force: true })
  }
})
