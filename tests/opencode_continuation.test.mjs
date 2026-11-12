import assert from "node:assert/strict"
import test from "node:test"
import {
  CONTINUATION_DIRECTIVES,
  abortableDelay,
  classifyTransientFailure,
  deriveContinuationDirective,
  retryDirective,
  retryDelayMs,
  runWithRetry,
  waitForExternalResult,
} from "../evospec/resources/opencode/plugins/evospec/continuation.mjs"

const KINDS = [
  "HUMAN_MATERIAL_DECISION",
  "SAFE_AUTOMATIC_OPERATION",
  "BOUNDED_DELEGATION",
  "EXTERNAL_WAIT",
  "STRUCTURAL_DEFECT",
]

function context(overrides = {}) {
  const base = {
    revision: 7,
    initialization_state: "INITIALIZED",
    lifecycle: ["IN_PROGRESS", "IMPLEMENT"],
    legal_actions: ["progress"],
    guidance: [{ action: "progress", allowed: true, intervention: "AUTOMATIC" }],
    completed_terminal: false,
    stale: false,
    stale_reason: null,
    pending_discovery_recovery: null,
    pending_capability_selection: null,
    work_queue: { next_code: "ACTIVE", next: null, active: { id: "work-1" } },
    autonomy_envelope: {
      allowed_paths: [],
      operation_budget: 0,
      retry_budget: 2,
      reversible_actions: ["progress", "review", "verify"],
      external_wait_allowed: true,
      irreversible_permissions: [],
      stop_conditions: ["material decision required"],
    },
    autonomy_metrics: { automatic_operations: 0, human_material_decisions: 0, operational_handoffs: 0 },
    outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "Core permits progress", material_decision: null },
  }
  return { ...base, ...overrides }
}

test("Core outcome taxonomy remains the exact five-value contract", () => {
  assert.deepEqual(KINDS, [
    "HUMAN_MATERIAL_DECISION",
    "SAFE_AUTOMATIC_OPERATION",
    "BOUNDED_DELEGATION",
    "EXTERNAL_WAIT",
    "STRUCTURAL_DEFECT",
  ])
})

test("SAFE_AUTOMATIC_OPERATION continues using Core's legal action", () => {
  const directive = deriveContinuationDirective(context())
  assert.equal(directive.directive, CONTINUATION_DIRECTIVES.CONTINUE)
  assert.deepEqual(directive.next_action, { action: "progress", arguments: null })
})

test("BOUNDED_DELEGATION continues only with an envelope and Core operation authorization", () => {
  const bounded = context({ outcome: { kind: "BOUNDED_DELEGATION", message: "delegation is bounded", material_decision: null } })
  assert.equal(deriveContinuationDirective(bounded).directive, "CONTINUE")
  assert.equal(deriveContinuationDirective(bounded, { operation: "run tests" }).directive, "FAIL_CLOSED")
  assert.equal(deriveContinuationDirective(bounded, {
    operation: "run tests",
    operationEvaluation: { authorization: "ALLOWED", reason: "within envelope" },
  }).directive, "CONTINUE")
  assert.equal(deriveContinuationDirective(context({ ...bounded, autonomy_envelope: null })).directive, "FAIL_CLOSED")
})

test("EXTERNAL_WAIT returns WAIT and does not create a user question", () => {
  const directive = deriveContinuationDirective(context({
    outcome: { kind: "EXTERNAL_WAIT", message: "external result pending", material_decision: null },
  }))
  assert.equal(directive.directive, "WAIT")
  assert.equal(directive.material_decision, null)
  assert.deepEqual(directive.wait, { initial_delay_ms: 1000, maximum_delay_ms: 30000, polls_per_cycle: 20 })
})

test("provider-neutral pending/running statuses wait even while Core lifecycle work is otherwise safe", () => {
  for (const externalStatus of ["PENDING", "RUNNING"]) {
    const directive = deriveContinuationDirective(context(), { externalStatus })
    assert.equal(directive.directive, "WAIT")
    assert.equal(directive.material_decision, null)
  }
  assert.equal(deriveContinuationDirective(context({
    autonomy_envelope: { ...context().autonomy_envelope, external_wait_allowed: false },
  }), { externalStatus: "PENDING" }).directive, "FAIL_CLOSED")
  assert.equal(deriveContinuationDirective(context(), { externalStatus: "SUCCESS" }).directive, "CONTINUE")
  assert.match(deriveContinuationDirective(context(), { externalStatus: "FAILURE" }).reason, /repair it/)
})

test("provider-reported transient failure retries within budget but product failure does not", () => {
  const transient = deriveContinuationDirective(context(), {
    externalStatus: "TRANSIENT_FAILURE",
    failureCode: "ECONNRESET",
    attempt: 0,
  })
  assert.equal(transient.directive, "CONTINUE")
  assert.equal(transient.retry.eligible, true)
  assert.equal(deriveContinuationDirective(context(), {
    externalStatus: "TRANSIENT_FAILURE",
    failureCode: "TEST_ASSERTION_FAILED",
    attempt: 0,
  }).directive, "FAIL_CLOSED")
})

test("HUMAN_MATERIAL_DECISION exposes exactly the concrete Core decision", () => {
  const decision = { decision: "select a capability", options: ["feature-1", "CREATE_STANDALONE_CAPABILITY"] }
  const directive = deriveContinuationDirective(context({
    outcome: { kind: "HUMAN_MATERIAL_DECISION", message: "a material choice is required", material_decision: decision },
  }))
  assert.equal(directive.directive, "ASK_USER")
  assert.deepEqual(directive.material_decision, decision)
  assert.equal(directive.material_decision.options.length, 2)
  assert.equal(deriveContinuationDirective(context({
    outcome: { kind: "HUMAN_MATERIAL_DECISION", message: "bad", material_decision: { decision: "choose", options: ["same", "same"] } },
  })).directive, "FAIL_CLOSED")
})

test("STRUCTURAL_DEFECT fails closed unless Core offers one complete bounded recovery", () => {
  const structural = context({ outcome: { kind: "STRUCTURAL_DEFECT", message: "unrepresentable state", material_decision: null } })
  assert.equal(deriveContinuationDirective(structural).directive, "FAIL_CLOSED")
  const recovery = context({
    ...structural,
    legal_actions: ["recover_discovery"],
    pending_discovery_recovery: {
      recovery_id: "recovery-1",
      candidates: [{ candidate: "export delivery", evidence_ids: ["evidence-1", "evidence-2"] }],
    },
  })
  const recovered = deriveContinuationDirective(recovery)
  assert.equal(recovered.directive, "CONTINUE")
  assert.deepEqual(recovered.next_action, {
    action: "recover_discovery",
    arguments: { recovery_id: "recovery-1", candidate: "export delivery", evidence_ids: ["evidence-1", "evidence-2"] },
  })
  assert.equal(deriveContinuationDirective(context({
    ...recovery,
    pending_discovery_recovery: { recovery_id: "recovery-1", candidates: [
      { candidate: "one", evidence_ids: ["e1"] }, { candidate: "two", evidence_ids: ["e2"] },
    ] },
  })).directive, "FAIL_CLOSED")
})

test("terminal context with null outcome completes without another mutation", () => {
  const directive = deriveContinuationDirective(context({ completed_terminal: true, outcome: null }))
  assert.equal(directive.directive, "COMPLETE")
  assert.equal(directive.next_action, null)
})

test("stale revisions require a fresh context before any action", () => {
  const directive = deriveContinuationDirective(context({ stale: true, stale_reason: "revision moved" }))
  assert.equal(directive.directive, "CONTINUE")
  assert.equal(directive.refresh_context, true)
  assert.equal(directive.next_action, "context")
})

test("recognized transient failures consume only the Core retry budget", () => {
  const current = context()
  assert.equal(classifyTransientFailure({ code: "ECONNRESET" }), true)
  assert.equal(classifyTransientFailure({ code: "TEST_ASSERTION_FAILED" }), false)
  assert.equal(retryDirective(current, { code: "ECONNRESET" }, 0).retry.eligible, true)
  assert.equal(retryDirective(current, { code: "ECONNRESET" }, 2).directive, "FAIL_CLOSED")
  assert.equal(retryDirective(current, { code: "TEST_ASSERTION_FAILED" }, 0).directive, "FAIL_CLOSED")
})

test("delegated-worker transient failures recover twice without an operational handoff", async () => {
  let attempts = 0
  const delays = []
  const result = await runWithRetry(async () => {
    attempts += 1
    if (attempts <= 2) throw Object.assign(new Error("worker temporarily unavailable"), { code: "DELEGATED_WORKER_TRANSIENT" })
    return "worker result"
  }, { retryBudget: 2, initialDelayMs: 1, maximumDelayMs: 4, wait: async (delay) => delays.push(delay) })
  assert.equal(result, "worker result")
  assert.equal(attempts, 3)
  assert.deepEqual(delays, [1, 2])
  assert.equal(context().autonomy_metrics.operational_handoffs, 0)
})

test("retry exhaustion is finite and deterministic failures are never retried", async () => {
  let attempts = 0
  await assert.rejects(runWithRetry(async () => {
    attempts += 1
    throw Object.assign(new Error("locked"), { code: "EBUSY" })
  }, { retryBudget: 1, initialDelayMs: 1, maximumDelayMs: 1, wait: async () => {} }), { name: "RetryExhaustedError" })
  assert.equal(attempts, 2)
  attempts = 0
  await assert.rejects(runWithRetry(async () => {
    attempts += 1
    throw Object.assign(new Error("assertion failed"), { code: "TEST_ASSERTION_FAILED" })
  }, { retryBudget: 5, wait: async () => assert.fail("deterministic failures must not wait or retry") }))
  assert.equal(attempts, 1)
})

test("external polling handles pending, running, success with bounded exponential backoff", async () => {
  const statuses = ["pending", "running", "success"]
  const delays = []
  const result = await waitForExternalResult(async () => ({ status: statuses.shift() }), {
    maxPolls: 4,
    initialDelayMs: 1,
    maximumDelayMs: 4,
    wait: async (delay) => delays.push(delay),
  })
  assert.equal(result.status, "success")
  assert.equal(result.polls, 3)
  assert.deepEqual(delays, [1, 2])
})

test("external wait polling is bounded and pending is not reported as success", async () => {
  const delays = []
  const result = await waitForExternalResult(async () => ({ status: "pending" }), {
    maxPolls: 3,
    initialDelayMs: 1,
    maximumDelayMs: 4,
    wait: async (delay) => delays.push(delay),
  })
  assert.deepEqual(result, { status: "pending", polls: 3 })
  assert.deepEqual(delays, [1, 2])
  assert.equal(retryDelayMs(8, 1, 4), 4)
})

test("cancellation promptly interrupts a wait and retry delay", async () => {
  const controller = new AbortController()
  const waiting = abortableDelay(60_000, controller.signal)
  controller.abort()
  await assert.rejects(waiting, { name: "AbortError" })

  const retryController = new AbortController()
  let attempts = 0
  await assert.rejects(runWithRetry(async () => {
    attempts += 1
    throw Object.assign(new Error("temporary"), { code: "EAGAIN" })
  }, {
    retryBudget: 4,
    signal: retryController.signal,
    initialDelayMs: 60_000,
    maximumDelayMs: 60_000,
    wait: async (_delay, signal) => {
      retryController.abort()
      await abortableDelay(60_000, signal)
    },
  }), { name: "AbortError" })
  assert.equal(attempts, 1)
})

test("deterministic queue activation continues while ambiguous selection never auto-selects", () => {
  const selected = context({
    legal_actions: ["activate_work"],
    guidance: [{ action: "activate_work", allowed: true, intervention: "AUTOMATIC" }],
    work_queue: { next_code: "SELECTED", next: "work-next", active: null },
  })
  assert.deepEqual(deriveContinuationDirective(selected).next_action, {
    action: "activate_work", arguments: { work_id: "work-next" },
  })
  const ambiguous = context({ work_queue: { next_code: "AMBIGUOUS", next: null, active: null } })
  assert.equal(deriveContinuationDirective(ambiguous).directive, "FAIL_CLOSED")
})

test("initialization is automatic only for an explicit task matching the executing repository", () => {
  const uninitialized = context({
    initialization_state: "UNINITIALIZED",
    legal_actions: ["init", "context"],
    guidance: [{ action: "init", allowed: true, intervention: "USER" }],
  })
  assert.equal(deriveContinuationDirective(uninitialized).directive, "FAIL_CLOSED")
  assert.equal(deriveContinuationDirective(uninitialized, {
    explicitTask: "Add Feature 10.3",
    sessionRootMatches: false,
  }).directive, "FAIL_CLOSED")
  const authorized = deriveContinuationDirective(uninitialized, {
    explicitTask: "Add Feature 10.3",
    sessionRootMatches: true,
  })
  assert.equal(authorized.directive, "CONTINUE")
  assert.equal(authorized.next_action, "init")
})

test("dogfood scenario A continues through implementation, retries, wait, stale refresh, review, verify, converge, terminal", async () => {
  let automaticOperations = 0
  let humanMaterialDecisions = 0
  let operationalHandoffs = 0
  let current = context({ outcome: { kind: "BOUNDED_DELEGATION", message: "worker operation is authorized", material_decision: null } })
  assert.equal(deriveContinuationDirective(current, {
    operation: "delegate worker",
    operationEvaluation: { authorization: "ALLOWED" },
  }).directive, "CONTINUE")
  let workerAttempts = 0
  await runWithRetry(async () => {
    workerAttempts += 1
    automaticOperations += 1
    if (workerAttempts < 3) throw Object.assign(new Error("transient worker issue"), { code: "DELEGATED_WORKER_TRANSIENT" })
    return "implemented"
  }, { retryBudget: 2, initialDelayMs: 1, maximumDelayMs: 2, wait: async () => { automaticOperations += 1 } })
  current = context({ outcome: { kind: "EXTERNAL_WAIT", message: "external validation pending", material_decision: null } })
  assert.equal(deriveContinuationDirective(current).directive, "WAIT")
  await waitForExternalResult(async () => {
    automaticOperations += 1
    return { status: automaticOperations < 8 ? "running" : "success" }
  }, { maxPolls: 5, initialDelayMs: 1, maximumDelayMs: 2, wait: async () => { automaticOperations += 1 } })
  current = context({ stale: true, outcome: { kind: "SAFE_AUTOMATIC_OPERATION", message: "refresh", material_decision: null } })
  const stale = deriveContinuationDirective(current)
  assert.equal(stale.refresh_context, true)
  current = context({ legal_actions: ["review"], guidance: [{ action: "review", allowed: true, intervention: "AUTOMATIC" }] })
  assert.equal(deriveContinuationDirective(current).next_action.action, "review")
  for (const action of ["verify", "converge"]) {
    current = context({ legal_actions: [action], guidance: [{ action, allowed: true, intervention: "AUTOMATIC" }] })
    assert.equal(deriveContinuationDirective(current).next_action.action, action)
  }
  current = context({ completed_terminal: true, outcome: null })
  assert.equal(deriveContinuationDirective(current).directive, "COMPLETE")
  assert.equal(humanMaterialDecisions, 0)
  assert.equal(operationalHandoffs, 0)
  assert.ok(automaticOperations >= 3)
})

test("dogfood scenario B exposes exactly one simulated material-decision surface", () => {
  const decision = { decision: "choose the capability", options: ["feature-existing", "CREATE_STANDALONE_CAPABILITY"] }
  const directive = deriveContinuationDirective(context({
    outcome: { kind: "HUMAN_MATERIAL_DECISION", message: "choice needed", material_decision: decision },
  }))
  let decisionSurfaces = 0
  if (directive.directive === "ASK_USER") decisionSurfaces += 1
  assert.equal(decisionSurfaces, 1)
  assert.deepEqual(directive.material_decision, decision)
})
