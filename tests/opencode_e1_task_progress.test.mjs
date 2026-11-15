// Exercise the packaged OpenCode V2 tool contract without a mock of Core authority.
// The adapter is run with a synthetic native runtime; Core integration is covered in Python.

import test from "node:test"
import assert from "node:assert/strict"
import { readFile, writeFile, copyFile, mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { pathToFileURL, fileURLToPath } from "node:url"

const pluginSource = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/index.ts", import.meta.url)))
const continuationSource = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/continuation.mjs", import.meta.url)))

test("E1 dedicated native tool blocks generic dispatch and never retries stale reports", async () => {
  const directory = await mkdtemp(join(tmpdir(), "evospec-e1c-native-"))
  const recorded = []
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
          return { location: { directory } }
        },
        hook: async () => {},
      },
    }
    await plugin.setup(runtime)
    const generic = definitions.find((item) => item.name === "evospec")
    const dedicated = definitions.find((item) => item.name === "evospec_accepted_task_progress")
    assert.ok(generic, "the original generic transport stays available")
    assert.ok(dedicated, "the guarded native tool is registered")
    assert.deepEqual(dedicated.input.required, Object.keys(dedicated.input.properties))
    assert.equal(dedicated.input.additionalProperties, false)

    const args = {
      expected_revision: 2, repository_id: "repo-1", task_id: "task-1",
      feature_agreement_ref: "agreement-feature-1", slice_agreement_ref: "agreement-slice-1",
      checkpoint: "Progress observed", statement: "Local tests now run",
      evidence_refs: ["tests/task.test.js"],
    }
    const context = { sessionID: "ses-e1c-native", signal: new AbortController().signal }
    const genericOutput = await generic.execute({ request: JSON.stringify({ action: "accepted_task_progress", ...args }) }, context)
    assert.equal(JSON.parse(genericOutput.content).error.code, "DEDICATED_TOOL_REQUIRED")
    assert.equal(recorded.length, 0, "generic tool never invokes the protocol for this mutation")

    const dedicatedOutput = await dedicated.execute(args, context)
    assert.equal(JSON.parse(dedicatedOutput.content).error.code, "STALE_REVISION")
    assert.equal(recorded.length, 1, "stale Task reports cannot be replayed silently")
    assert.equal(recorded[0].options.cwd, directory)
    assert.deepEqual(JSON.parse(await recorded[0].options.stdin.text()), {
      action: "accepted_task_progress", ...args,
    })
    assert.ok(!recorded[0].command.includes("--unsafe"))
  } finally {
    delete globalThis.Bun
    await rm(directory, { recursive: true, force: true })
  }
})
