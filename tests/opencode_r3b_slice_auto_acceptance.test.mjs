// Synthetic host-boundary tests: no native OpenCode session or real permission prompt.
import test from "node:test"
import assert from "node:assert/strict"
import { readFile, writeFile, copyFile, mkdtemp, realpath, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { pathToFileURL, fileURLToPath } from "node:url"

const source = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/index.ts", import.meta.url)))
const continuation = resolve(fileURLToPath(new URL("../evospec/resources/opencode/plugins/evospec/continuation.mjs", import.meta.url)))

function snapshot(fingerprint = "a".repeat(64)) {
  return {
    version: "r3-root-input-v2",
    policy_id: "r3-root-input-v2",
    policy_digest: "1".repeat(64),
    repository_id: "repo-r3b",
    fingerprint,
    file_count: 12,
    directory_count: 3,
    total_bytes: 1024,
  }
}

function policy() {
  return {
    coverage_contract_version: "r3-known-input-contract-v1",
    input_version: "r3-root-input-v2",
    input_policy_id: "r3-root-input-v2",
    input_policy_digest: "1".repeat(64),
    admission_policy_id: "r3-objective-test-v1",
    admission_policy_digest: "2".repeat(64),
    environment_profile_id: "evospec-sanitized-test-env-v1",
    environment_profile_policy_digest: "3".repeat(64),
    coverage_contract: "bounded root inputs; trusted-local evidence, not an OS sandbox",
  }
}

function approvedCoverage(fingerprint = "a".repeat(64)) {
  return {
    id: "coverage-0123456789abcdefabcd",
    contract_digest: "4".repeat(64),
    repository_id: "repo-r3b",
    slice_agreement_ref: "slice-r3b",
    criteria_revision_id: "criteria-r3b",
    criterion_id: "AC-1",
    test_kind: "node-test",
    test_target: "tests/r3b-native.test.mjs",
    known_inputs: [{ kind: "root-file", path: "tests/r3b-native.test.mjs" }],
    root_fingerprint: fingerprint,
    input_policy_id: "r3-root-input-v2",
    input_policy_digest: "1".repeat(64),
    admission_policy_id: "r3-objective-test-v1",
    admission_policy_digest: "2".repeat(64),
    environment_profile_id: "evospec-sanitized-test-env-v1",
    environment_profile_policy_digest: "3".repeat(64),
    statement: `repository: repo-r3b; Slice: slice-r3b; criterios: criteria-r3b; root-input-fingerprint: ${fingerprint};`,
    source_reference: "tty:0123456789ab",
    source: "INTERACTIVE_TERMINAL",
    source_revision: 6,
    recorded_revision: 7,
  }
}

function context(overrides = {}) {
  return {
    revision: 7,
    repository_id: "repo-r3b",
    r3_schema_ready: true,
    r3b_schema_ready: true,
    r3b_policy: policy(),
    input_snapshot: snapshot(),
    r3b_coverage_approvals: [approvedCoverage()],
    accepted_slice: {
      slice_agreement_ref: "slice-r3b",
      criteria_revision: {
        id: "criteria-r3b",
        criteria: [{
          id: "AC-1",
          expected_evidence: "node-test:tests/r3b-native.test.mjs",
        }],
      },
    },
    slice_outcome: { status: "READY", latest_receipt_id: null, blockers: [] },
    ...overrides,
  }
}

function reopenedHistoricalContext(cycleState = "OPEN") {
  // Core's terminal archive must be explicit; an arbitrary Slice ID is not authority.
  const active = context().accepted_slice
  return context({
    accepted_slice: null,
    slice_outcome: null,
    feature_completion: { feature_id: "feature-r3b", cycle_id: 1, cycle_state: cycleState },
    slice_acceptance_inventory: { items: [{
      slice_agreement_ref: active.slice_agreement_ref,
      feature_id: "feature-r3b",
      scope_status: "HISTORICAL",
      provenance: "TERMINAL_ARCHIVE",
      authority_status: "CANONICAL",
      criteria_status: "RECORDED",
      criteria_revision: active.criteria_revision,
    }] },
  })
}

function stream(value = "") {
  return new ReadableStream({
    start(controller) {
      if (value) controller.enqueue(new TextEncoder().encode(value))
      controller.close()
    },
  })
}

test("R3-B test binds permission to exact inputs, executes sanitized runtime, and records fresh evidence", async () => {
  const root = await mkdtemp(join(tmpdir(), "evospec-r3b-host-"))
  const commands = []
  const permissions = []
  const sentRequests = []
  let permissionGuard
  let decision = async () => {}
  let sessionPath = root
  let current = context()
  try {
    // Compare canonical identity while allowing the session path to retain Windows 8.3 aliases.
    const canonicalRoot = await realpath(root)
    const content = (await readFile(source, "utf8"))
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(root, "index.mjs"), content)
    await copyFile(continuation, join(root, "continuation.mjs"))
    globalThis.Bun = {
      which: (bin) => bin === "node" ? process.execPath : null,
      spawn(command, options) {
        commands.push({ command, options })
        if (command[1] === "--version") {
          return { stdout: stream(process.version), stderr: stream(), exited: Promise.resolve(0) }
        }
        if (command[1] === "--test") {
          return { stdout: stream("R3-B test passed\n"), stderr: stream(), exited: Promise.resolve(0) }
        }
        if (command.includes("--root")) {
          const payload = options.stdin
          return (async () => {
            const request = JSON.parse(await payload.text())
            sentRequests.push(request)
            const response = request.action === "context"
              ? { context: current }
              : {
                  result: { code: "CRITERION_TEST_RECORDED", revision: 8 },
                  context: { ...current, revision: 8 },
                }
            return {
              stdout: stream(JSON.stringify(response)),
              stderr: stream(),
              exited: Promise.resolve(0),
            }
          })()
        }
        throw new Error(`unexpected child command: ${command.join(" ")}`)
      },
    }
    // Bun.spawn is synchronous; resolve the fake protocol's async input before returning its process shape.
    const originalSpawn = globalThis.Bun.spawn
    globalThis.Bun.spawn = (command, options) => {
      const result = originalSpawn(command, options)
      if (result && typeof result.then === "function") {
        return {
          stdout: new ReadableStream({
            async start(controller) {
              const process = await result
              const reader = process.stdout.getReader()
              for (;;) {
                const { value, done } = await reader.read()
                if (done) break
                controller.enqueue(value)
              }
              controller.close()
            },
          }),
          stderr: stream(),
          exited: result.then((process) => process.exited).then((value) => value),
        }
      }
      return result
    }
    const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
    const definitions = []
    const runtime = {
      tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })) },
      session: { get: async () => ({ location: { directory: sessionPath } }), hook: async () => {} },
      permission: {
        hook: async (name, handler) => {
          assert.equal(name, "evaluate")
          permissionGuard = handler
        },
        assert: async (request) => {
          // V2's default allow must become a real ask for the native human gate.
          assert.ok(permissionGuard, "the host permission guard must be installed")
          const event = { action: request.action, resources: request.resources, effect: "allow" }
          await permissionGuard(event)
          assert.equal(event.effect, "ask")
          permissions.push(request)
          await decision(request)
        },
      },
    }
    await plugin.setup(runtime)
    const unrelated = { action: "read", resources: ["*"], effect: "allow" }
    await permissionGuard(unrelated)
    assert.equal(unrelated.effect, "allow", "R3-B rules must not rewrite unrelated permissions")
    const forbidden = { action: "evospec_criterion_test_r3b", resources: ["*"], effect: "deny" }
    await permissionGuard(forbidden)
    assert.equal(forbidden.effect, "deny", "a configured deny remains final")
    const testTool = definitions.find((item) => item.name === "evospec_criterion_test_r3b")
    const generic = definitions.find((item) => item.name === "evospec")
    assert.ok(testTool)
    assert.deepEqual(testTool.input.required, Object.keys(testTool.input.properties))
    const args = {
      expected_revision: 7,
      repository_id: "repo-r3b",
      slice_agreement_ref: "slice-r3b",
      criteria_revision_id: "criteria-r3b",
      criterion_id: "AC-1",
      test_kind: "node-test",
      test_target: "tests/r3b-native.test.mjs",
    }
    const call = {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "call-r3b",
      signal: new AbortController().signal,
    }

    const blockedGeneric = await generic.execute({
      request: JSON.stringify({ action: "criterion_test_result_r3b", ...args }),
    }, call)
    assert.equal(JSON.parse(blockedGeneric.content).error.code, "DEDICATED_TOOL_REQUIRED")
    assert.equal(commands.length, 0)

    await assert.rejects(testTool.execute({ ...args, expected_revision: 6 }, call), /authority changed/)
    assert.equal(permissions.length, 0)
    assert.equal(commands.length, 1, "stale authority permits only the Core context read")
    commands.length = 0

    decision = async () => { throw new Error("permission denied") }
    await assert.rejects(testTool.execute(args, call), /permission denied/)
    assert.equal(permissions.length, 1)
    assert.equal(commands.length, 1, "denial prevents runtime/version/test execution")
    commands.length = 0
    decision = async () => {}

    const success = await testTool.execute(args, call)
    const parsed = JSON.parse(success.content)
    assert.equal(parsed.test.exit_code, 0)
    assert.equal(parsed.recording.result.code, "CRITERION_TEST_RECORDED")
    const resource = JSON.parse(permissions.at(-1).resources[0])
    assert.equal(resource.operation, "run_criterion_test_r3b")
    assert.equal(resource.repository_root, canonicalRoot)
    assert.equal(permissions.at(-1).resources[1], canonicalRoot)
    assert.equal(permissions.at(-1).metadata.repository_root, canonicalRoot)
    assert.equal(resource.input_fingerprint, snapshot().fingerprint)
    assert.equal(resource.coverage_approval.id, approvedCoverage().id)
    assert.equal(resource.coverage_approval.contract_digest, approvedCoverage().contract_digest)
    assert.deepEqual(resource.command.slice(1), ["--test", args.test_target])
    const childTest = commands.find((item) => item.command[1] === "--test")
    assert.deepEqual(childTest.options.env.PATH, resolve(process.execPath, ".."))
    assert.equal(childTest.options.env.TEMP, canonicalRoot)
    assert.equal(childTest.options.env.TMP, canonicalRoot)
    assert.equal(childTest.options.env.HOME, undefined)
    assert.equal(childTest.options.env.API_KEY, undefined)
    assert.equal(sentRequests.at(-1).action, "criterion_test_result_r3b")
    assert.equal(Object.hasOwn(sentRequests.at(-1), "external_inputs"), false)
    assert.equal(sentRequests.at(-1).input_fingerprint_before, snapshot().fingerprint)
    assert.equal(sentRequests.at(-1).input_fingerprint_after, snapshot().fingerprint)
    assert.equal(sentRequests.at(-1).coverage_approval_id, approvedCoverage().id)
    assert.equal(sentRequests.at(-1).coverage_contract_digest, approvedCoverage().contract_digest)

    // The same host permission must also protect an archived Slice after a human reopen.
    current = reopenedHistoricalContext()
    const historical = await testTool.execute(args, call)
    assert.equal(JSON.parse(historical.content).recording.result.code, "CRITERION_TEST_RECORDED")
    assert.equal(JSON.parse(permissions.at(-1).resources[0]).slice_agreement_ref, "slice-r3b")
    current = reopenedHistoricalContext("CLOSED")
    const historicalPermissions = permissions.length
    await assert.rejects(testTool.execute(args, call), /historical evidence requires/)
    assert.equal(permissions.length, historicalPermissions, "closed cycles cannot request host test permission")
  } finally {
    delete globalThis.Bun
    await rm(root, { recursive: true, force: true })
  }
})

test("R3-B refuses to run when the host cannot install an explicit permission guard", async () => {
  const root = await mkdtemp(join(tmpdir(), "evospec-r3b-no-ask-"))
  try {
    const content = (await readFile(source, "utf8"))
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(root, "index.mjs"), content)
    await copyFile(continuation, join(root, "continuation.mjs"))
    const definitions = []
    const runtime = {
      tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })) },
      session: { get: async () => ({ location: { directory: root } }), hook: async () => {} },
      // A host may expose assert but lack the hook that forces a visible ask.
      permission: { assert: async () => { throw new Error("must never ask without an enforceable guard") } },
    }
    const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
    await plugin.setup(runtime)
    const testTool = definitions.find((item) => item.name === "evospec_criterion_test_r3b")
    const outcomeTool = definitions.find((item) => item.name === "evospec_accept_slice_outcome")
    const call = {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "no-ask",
      signal: new AbortController().signal,
    }
    await assert.rejects(testTool.execute({}, call), /explicit human permission guard is unavailable/)
    await assert.rejects(outcomeTool.execute({}, call), /explicit human permission guard is unavailable/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test("R3-B test rejects scope or fingerprint changes while host permission is pending", async () => {
  const root = await mkdtemp(join(tmpdir(), "evospec-r3b-recheck-"))
  const commands = []
  const permissions = []
  let current = context()
  let mutateDuringPermission = false
  let sessionPath = root
  try {
    const content = (await readFile(source, "utf8"))
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(root, "index.mjs"), content)
    await copyFile(continuation, join(root, "continuation.mjs"))
    globalThis.Bun = {
      which: () => process.execPath,
      spawn(command, options) {
        commands.push(command)
        if (command.includes("--root")) {
          return {
            stdout: new ReadableStream({ async start(controller) {
              const request = JSON.parse(await options.stdin.text())
              controller.enqueue(new TextEncoder().encode(JSON.stringify({ context: current })))
              controller.close()
            } }),
            stderr: stream(),
            exited: Promise.resolve(0),
          }
        }
        throw new Error("runtime must not run before the permission recheck")
      },
    }
    const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
    const definitions = []
    const runtime = {
      tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })) },
      session: { get: async () => ({ location: { directory: sessionPath } }), hook: async () => {} },
      permission: { hook: async () => {}, assert: async (request) => {
        permissions.push(request)
        if (mutateDuringPermission) current = context({ input_snapshot: snapshot("b".repeat(64)) })
      } },
    }
    await plugin.setup(runtime)
    const tool = definitions.find((item) => item.name === "evospec_criterion_test_r3b")
    const args = {
      expected_revision: 7, repository_id: "repo-r3b",
      slice_agreement_ref: "slice-r3b", criteria_revision_id: "criteria-r3b",
      criterion_id: "AC-1", test_kind: "node-test",
      test_target: "tests/r3b-native.test.mjs",
    }
    const call = {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "call-r3b",
      signal: new AbortController().signal,
    }
    mutateDuringPermission = true
    await assert.rejects(tool.execute(args, call), /input snapshot changed while permission was pending/)
    assert.equal(permissions.length, 1)
    assert.equal(commands.length, 2, "only pre- and post-permission Core reads are allowed")
  } finally {
    delete globalThis.Bun
    await rm(root, { recursive: true, force: true })
  }
})

test("R3-B test submits no evidence when requester or session root changes during execution", async (t) => {
  for (const mutation of ["requester", "session-root"]) {
    await t.test(mutation, async () => {
      const root = await mkdtemp(join(tmpdir(), "evospec-r3b-posttest-"))
      const alternateRoot = mutation === "session-root"
        ? await mkdtemp(join(tmpdir(), "evospec-r3b-posttest-root-"))
        : root
      const requests = []
      let sessionPath = root
      let requesterMutated = false
      let call
      try {
        const content = (await readFile(source, "utf8"))
          .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
          .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
        await writeFile(join(root, "index.mjs"), content)
        await copyFile(continuation, join(root, "continuation.mjs"))
        globalThis.Bun = {
          which: () => process.execPath,
          spawn(command, options) {
            if (command[1] === "--version") {
              return { stdout: stream(process.version), stderr: stream(), exited: Promise.resolve(0) }
            }
            if (command[1] === "--test") {
              if (mutation === "requester") requesterMutated = true
              else sessionPath = alternateRoot
              return { stdout: stream("test finished\n"), stderr: stream(), exited: Promise.resolve(0) }
            }
            if (command.includes("--root")) {
              return {
                stdout: new ReadableStream({ async start(controller) {
                  const request = JSON.parse(await options.stdin.text())
                  requests.push(request)
                  controller.enqueue(new TextEncoder().encode(JSON.stringify({
                    context: request.action === "context" ? context() : context({ revision: 8 }),
                  })))
                  controller.close()
                } }),
                stderr: stream(),
                exited: Promise.resolve(0),
              }
            }
            throw new Error(`unexpected child command: ${command.join(" ")}`)
          },
        }
        const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
        const definitions = []
        const runtime = {
          tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
            list: async () => definitions.map((item) => ({ id: item.name })) },
          session: { get: async () => ({ location: { directory: sessionPath } }), hook: async () => {} },
          permission: { hook: async () => {}, assert: async () => {} },
        }
        await plugin.setup(runtime)
        const tool = definitions.find((item) => item.name === "evospec_criterion_test_r3b")
        call = {
          runtime, sessionID: "ses-r3b",
          get agent() { return requesterMutated ? "changed-agent" : "tester" },
          messageID: "msg-r3b", id: "post-test",
          signal: new AbortController().signal,
        }
        await assert.rejects(tool.execute({
          expected_revision: 7,
          repository_id: "repo-r3b",
          slice_agreement_ref: "slice-r3b",
          criteria_revision_id: "criteria-r3b",
          criterion_id: "AC-1",
          test_kind: "node-test",
          test_target: "tests/r3b-native.test.mjs",
        }, call), /post-test (requester|session root) changed/)
        assert.equal(
          requests.some((request) => request.action === "criterion_test_result_r3b"),
          false,
        )
      } finally {
        delete globalThis.Bun
        await rm(root, { recursive: true, force: true })
        if (alternateRoot !== root) await rm(alternateRoot, { recursive: true, force: true })
      }
    })
  }
})

test("Slice outcome tool binds the exact current scope and returns Core's immutable receipt", async () => {
  const root = await mkdtemp(join(tmpdir(), "evospec-r3b-accept-"))
  const permissions = []
  let permissionGuard
  const requests = []
  let current = context()
  let mutateDuringPermission = false
  try {
    const content = (await readFile(source, "utf8"))
      .replace("__EVOSPEC_AGENT_COMMAND__", JSON.stringify(["python", "-m", "evospec.agent_protocol"]))
      .replace("__EVOSPEC_CLI_COMMAND__", JSON.stringify(["python", "-m", "evospec.cli"]))
    await writeFile(join(root, "index.mjs"), content)
    await copyFile(continuation, join(root, "continuation.mjs"))
    globalThis.Bun = {
      spawn(command, options) {
        return {
          stdout: new ReadableStream({ async start(controller) {
            const request = JSON.parse(await options.stdin.text())
            requests.push(request)
            const response = request.action === "context"
              ? { context: current }
              : {
                  result: {
                    code: "ALREADY_ACCEPTED",
                    revision: 7,
                    slice_outcome_receipt: {
                      id: "slice-outcome-canonical",
                      source: "CORE_OBJECTIVE_ACCEPTANCE",
                    },
                  },
                  context: current,
                }
            controller.enqueue(new TextEncoder().encode(JSON.stringify(response)))
            controller.close()
          } }),
          stderr: stream(),
          exited: Promise.resolve(0),
        }
      },
    }
    const plugin = (await import(pathToFileURL(join(root, "index.mjs")).href)).default
    const definitions = []
    const runtime = {
      tool: { transform: async (register) => register({ add: (item) => definitions.push(item) }),
        list: async () => definitions.map((item) => ({ id: item.name })) },
      session: { get: async () => ({ location: { directory: root } }), hook: async () => {} },
      permission: {
        hook: async (name, handler) => {
          assert.equal(name, "evaluate")
          permissionGuard = handler
        },
        assert: async (request) => {
          const event = { action: request.action, resources: request.resources, effect: "allow" }
          await permissionGuard(event)
          assert.equal(event.effect, "ask", "outcome acceptance also requires an explicit prompt")
          permissions.push(request)
          if (mutateDuringPermission) current = context({ input_snapshot: snapshot("b".repeat(64)) })
        },
      },
    }
    await plugin.setup(runtime)
    const tool = definitions.find((item) => item.name === "evospec_accept_slice_outcome")
    const generic = definitions.find((item) => item.name === "evospec")
    assert.ok(tool)
    const genericResult = await generic.execute({
      request: JSON.stringify({ action: "accept_slice_outcome", expected_revision: 7 }),
    }, {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "generic-r3b",
      signal: new AbortController().signal,
    })
    assert.equal(JSON.parse(genericResult.content).error.code, "DEDICATED_TOOL_REQUIRED")
    const bypass = await generic.execute({
      request: JSON.stringify({ action: "accept_historical_slice_outcome", expected_revision: 7,
        slice_agreement_ref: "slice-r3b", input_snapshot: snapshot() }),
    }, {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "generic-r3b-history",
      signal: new AbortController().signal,
    })
    assert.equal(JSON.parse(bypass.content).error.code, "DEDICATED_TOOL_REQUIRED")
    assert.equal(permissions.length, 0)
    const result = await tool.execute({ expected_revision: 7 }, {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "accept-r3b",
      signal: new AbortController().signal,
    })
    const response = JSON.parse(result.content)
    assert.equal(response.result.code, "ALREADY_ACCEPTED")
    assert.equal(response.result.slice_outcome_receipt.id, "slice-outcome-canonical")
    const resource = JSON.parse(permissions[0].resources[0])
    assert.equal(permissions[0].resources[1], await realpath(root))
    assert.equal(permissions[0].metadata.repository_root, await realpath(root))
    assert.equal(resource.operation, "accept_slice_outcome")
    assert.equal(resource.repository_id, "repo-r3b")
    assert.equal(resource.slice_agreement_ref, "slice-r3b")
    assert.equal(resource.criteria_revision_id, "criteria-r3b")
    assert.equal(resource.input_fingerprint, snapshot().fingerprint)
    assert.equal(resource.coverage_approvals.length, 1)
    assert.equal(resource.coverage_approvals[0].id, approvedCoverage().id)
    assert.equal(requests.at(-1).action, "accept_slice_outcome")
    assert.deepEqual(requests.at(-1).input_snapshot, snapshot())
    const requestCount = requests.length
    mutateDuringPermission = true
    await assert.rejects(
      tool.execute({ expected_revision: 7 }, {
        runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "accept-r3b-drift",
        signal: new AbortController().signal,
      }),
      /scope, policy, or inputs changed while permission was pending/,
    )
    assert.equal(
      requests.slice(requestCount).some((request) => request.action === "accept_slice_outcome"),
      false,
      "changed inputs prevent the Core acceptance request",
    )

    mutateDuringPermission = false
    current = reopenedHistoricalContext()
    const historical = await tool.execute({ expected_revision: 7, slice_agreement_ref: "slice-r3b" }, {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "historical-r3b",
      signal: new AbortController().signal,
    })
    assert.equal(JSON.parse(historical.content).result.code, "ALREADY_ACCEPTED")
    assert.equal(requests.at(-1).action, "accept_historical_slice_outcome")
    assert.equal(requests.at(-1).slice_agreement_ref, "slice-r3b")
    assert.equal(JSON.parse(permissions.at(-1).resources[0]).slice_agreement_ref, "slice-r3b")
    current = reopenedHistoricalContext("CLOSED")
    const permissionCount = permissions.length
    await assert.rejects(tool.execute({ expected_revision: 7, slice_agreement_ref: "slice-r3b" }, {
      runtime, sessionID: "ses-r3b", agent: "tester", messageID: "msg-r3b", id: "blocked-r3b",
      signal: new AbortController().signal,
    }), /historical evidence requires/)
    assert.equal(permissions.length, permissionCount)
  } finally {
    delete globalThis.Bun
    await rm(root, { recursive: true, force: true })
  }
})
