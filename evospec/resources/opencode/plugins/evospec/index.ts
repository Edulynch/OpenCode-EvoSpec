// EvoSpec-managed: opencode-v2

import { lstat, realpath } from "node:fs/promises"
import { isAbsolute, relative, resolve } from "node:path"
import {
  abortableDelay,
  deriveContinuationDirective,
  retryDirective,
} from "./continuation.mjs"

const AGENT_COMMAND = __EVOSPEC_AGENT_COMMAND__
const CLI_COMMAND = __EVOSPEC_CLI_COMMAND__
const canonicalSnapshots = new Map()

const string = { type: "string" }
const nullableString = { type: ["string", "null"] }
const boolean = { type: "boolean" }
const nonnegativeInteger = { type: "integer", minimum: 0 }
const stringArray = { type: "array", items: string }
const commitRewrite = object({ old_sha: string, new_sha: string, identity_normalized: boolean })

function object(properties, required = Object.keys(properties)) {
  return { type: "object", properties, required, additionalProperties: false }
}

async function drain(stream, maximumCharacters = 65536) {
  const decoder = new TextDecoder("utf-8", { fatal: true })
  const reader = stream.getReader()
  let output = ""
  let truncated = false
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    const text = decoder.decode(value, { stream: true })
    const remaining = Math.max(0, maximumCharacters - output.length)
    if (text.length > remaining) truncated = true
    if (remaining > 0) output += text.slice(0, remaining)
  }
  const final = decoder.decode()
  if (output.length < maximumCharacters) output += final.slice(0, maximumCharacters - output.length)
  if (final.length > 0 && output.length >= maximumCharacters) truncated = true
  return `${output.trim()}${truncated ? "\n[diagnostics truncated]" : ""}`
}

async function capture(process) {
  const settled = await Promise.allSettled([
    drain(process.stdout),
    drain(process.stderr),
    process.exited,
  ])
  const failure = settled.find((item) => item.status === "rejected")
  if (failure) throw failure.reason
  const [output, error, exitStatus] = settled.map((item) => item.value)
  return {
    output,
    error,
    exitStatus,
    state: exitStatus === 0 ? "SUCCEEDED" : "FAILED",
  }
}

function outcome(result, retainOutputOnFailure = false) {
  return result.exitStatus === 0 || (retainOutputOnFailure && result.output)
    ? result.output
    : result.error
}

async function sessionRoot(runtime, context) {
  if (typeof context.sessionID !== "string" || !context.sessionID) {
    throw new Error("OpenCode V2 did not provide the executing session ID")
  }
  const session = await runtime.session.get({ sessionID: context.sessionID })
  return session.location.directory
}

async function spawnCommand(command, root, context, input) {
  const options = {
    cwd: root,
    signal: context.signal,
    windowsHide: globalThis.process.platform === "win32",
    stdout: "pipe",
    stderr: "pipe",
  }
  if (input !== undefined) options.stdin = new Blob([new TextEncoder().encode(input)])
  const process = Bun.spawn(command, options)
  return capture(process)
}

async function rawExecuteRequest(runtime, request, context) {
  const root = await sessionRoot(runtime, context)
  const payload = typeof request === "string" ? request : JSON.stringify(request)
  const result = await spawnCommand([...AGENT_COMMAND, "--root", root], root, context, payload)
  const output = outcome(result)
  try {
    const response = JSON.parse(output)
    const canonical = contextFromResponse(response)
    if (canonical) canonicalSnapshots.set(resolve(root), canonical)
  } catch {}
  return output
}

const SAFE_STALE_REPLAY_ACTIONS = new Set([
  "autonomy_evaluate", "progress", "assess_locks", "review", "verify", "converge",
  "clarify", "continue", "resume", "activate_work", "set_work_state",
  "add_work_relation", "remove_work_relation", "recover_discovery", "select_capability",
  "new", "change",
])

function staleRequest(payload) {
  try {
    const parsed = typeof payload === "string" ? JSON.parse(payload) : payload
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

function contextFromResponse(value) {
  return value && typeof value === "object" && value.context && typeof value.context === "object"
    ? value.context
    : null
}

function requestStillAuthorized(request, current, original) {
  if (request.action === "autonomy_evaluate") return true
  if (!Array.isArray(current?.legal_actions) || !current.legal_actions.includes(request.action)) return false
  if (!original || original.revision !== request.expected_revision) return false
  const sameWork = original.feature_id === current.feature_id && original.change_id === current.change_id &&
    original.side_work === current.side_work && original.side_work_active === current.side_work_active
  if (["new", "change"].includes(request.action)) {
    return original.lifecycle === null && current.lifecycle === null && current.pending_intake === false && current.side_work_active === false
  }
  if (request.action === "select_capability") {
    const pending = current.pending_capability_selection
    const prior = original.pending_capability_selection
    return prior?.selection_id === request.selection_id && pending?.selection_id === request.selection_id &&
      JSON.stringify(pending.choices) === JSON.stringify(prior.choices) &&
      pending.choices?.includes(request.choice) === true && prior.choices?.includes(request.choice) === true
  }
  if (request.action === "recover_discovery") {
    const pending = current.pending_discovery_recovery
    const priorRecovery = original.pending_discovery_recovery
    const candidate = pending?.candidates?.find((item) => item.candidate === request.candidate)
    const priorCandidate = priorRecovery?.candidates?.find((item) => item.candidate === request.candidate)
    return priorRecovery?.recovery_id === request.recovery_id && pending?.recovery_id === request.recovery_id &&
      candidate !== undefined && priorCandidate !== undefined &&
      JSON.stringify(candidate.evidence_ids) === JSON.stringify(request.evidence_ids) &&
      JSON.stringify(priorCandidate.evidence_ids) === JSON.stringify(request.evidence_ids)
  }
  if (request.action === "converge") {
    return sameWork && current.change_id === request.assessment?.change_id && current.lifecycle !== null
  }
  if (request.action === "resume") {
    const item = current.work_queue?.all?.find((value) => value.id === request.change_id || value.change_id === request.change_id)
    const prior = original.work_queue?.all?.find((value) => value.id === request.change_id || value.change_id === request.change_id)
    return original.work_queue?.active === null && current.work_queue?.active === null &&
      prior?.operational_state === item?.operational_state && item?.activation_eligible === true
  }
  if (request.action === "clarify") {
    const offered = new Map((current.questions || []).map((question) => [question.id, question]))
    const prior = new Map((original.questions || []).map((question) => [question.id, question]))
    return sameWork && current.lifecycle !== null && Array.isArray(request.answers) &&
      offered.size === prior.size && request.answers.length === offered.size && request.answers.length > 0 &&
      request.answers.every((answer) => {
        const now = offered.get(answer.question_id)
        const then = prior.get(answer.question_id)
        return now && then && now.prompt === then.prompt &&
          Array.isArray(now.options) && now.options.length >= 2 &&
          JSON.stringify(now.options) === JSON.stringify(then.options) && now.options.includes(answer.answer)
      })
  }
  if (request.action === "set_work_state") {
    const item = current.work_queue?.all?.find((value) => value.id === request.work_id)
    const prior = original.work_queue?.all?.find((value) => value.id === request.work_id)
    return item !== undefined && prior?.operational_state === item.operational_state &&
      !["ACTIVE", "DONE"].includes(item.operational_state)
  }
  if (request.action === "activate_work") {
    const item = current.work_queue?.all?.find((value) => value.id === request.work_id)
    const prior = original.work_queue?.all?.find((value) => value.id === request.work_id)
    return original.work_queue?.active === null && current.work_queue?.active === null &&
      prior?.operational_state === item?.operational_state && item?.activation_eligible === true && item.operational_state === "READY"
  }
  if (["add_work_relation", "remove_work_relation"].includes(request.action)) {
    if (original.work_queue?.complete !== true || current.work_queue?.complete !== true) return false
    const known = (queue) => new Set([
      ...(queue.active?.id ? [queue.active.id] : []),
      ...queue.all.map((item) => item.id),
    ])
    const priorIds = known(original.work_queue)
    const currentIds = known(current.work_queue)
    if (!priorIds.has(request.predecessor_id) || !priorIds.has(request.successor_id) ||
        !currentIds.has(request.predecessor_id) || !currentIds.has(request.successor_id)) return false
    const tuple = (item) => item.predecessor_id === request.predecessor_id &&
      item.successor_id === request.successor_id && item.kind === request.kind
    const existed = original.work_queue.relations.some(tuple)
    const exists = current.work_queue.relations.some(tuple)
    return request.action === "add_work_relation" ? !existed && !exists : existed && exists
  }
  if (["review", "verify", "continue", "progress", "assess_locks"].includes(request.action)) {
    return sameWork && original.lifecycle !== null && current.lifecycle !== null
  }
  return true
}

async function executeRequest(runtime, request, context, options = {}) {
  const decodedRequest = staleRequest(request)
  const root = await sessionRoot(runtime, context)
  const baseline = decodedRequest?.expected_revision === undefined
    ? null
    : canonicalSnapshots.get(resolve(root))
  const output = await rawExecuteRequest(runtime, request, context)
  if (options.retryStale === false) return output
  if (decodedRequest === null || !SAFE_STALE_REPLAY_ACTIONS.has(decodedRequest.action)) return output
  let response
  try { response = JSON.parse(output) } catch { return output }
  if (response?.error?.code !== "STALE_REVISION") return output

  const refreshedText = await rawExecuteRequest(runtime, { action: "context" }, context)
  let refreshed
  try { refreshed = JSON.parse(refreshedText) } catch { return output }
  const current = contextFromResponse(refreshed)
  if (!current || !requestStillAuthorized(decodedRequest, current, baseline)) {
    return JSON.stringify({
      error: { code: "STALE_REVISION", detail: "canonical context refreshed; the stale request was not replayed because its authority changed" },
      context: current,
    })
  }
  if (options.validateStaleReplay && !(await options.validateStaleReplay(root, decodedRequest, current))) {
    return JSON.stringify({
      error: { code: "STALE_REVISION", detail: "canonical context refreshed; assessed intake evidence or Core identity no longer validates" },
      context: current,
    })
  }
  const retry = { ...decodedRequest, expected_revision: current.revision }
  return rawExecuteRequest(runtime, retry, context)
}

function parseContextResponse(responseText) {
  const response = JSON.parse(responseText)
  const canonical = contextFromResponse(response)
  if (!canonical) throw new Error("EvoSpec did not return canonical context")
  return canonical
}

async function ensureInitializedForExplicitTask(runtime, root, task, context) {
  let canonical = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  if (canonical.initialization_state !== "UNINITIALIZED") return canonical
  const directive = deriveContinuationDirective(canonical, {
    explicitTask: task,
    sessionRootMatches: resolve(root) === resolve(await sessionRoot(runtime, context)),
  })
  if (directive.directive !== "CONTINUE" || directive.next_action !== "init") {
    throw new Error(directive.reason)
  }
  let result = await rawExecuteRequest(runtime, { action: "init", expected_revision: canonical.revision }, context)
  let parsed
  try { parsed = JSON.parse(result) } catch { throw new Error(result) }
  if (parsed?.error?.code === "STALE_REVISION") {
    canonical = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
    if (canonical.initialization_state !== "UNINITIALIZED" || !canonical.legal_actions?.includes("init")) return canonical
    result = await rawExecuteRequest(runtime, { action: "init", expected_revision: canonical.revision }, context)
    parsed = JSON.parse(result)
  }
  if (parsed?.error) throw new Error(parsed.error.detail || "Core initialization failed")
  return parseContextResponse(result)
}

async function executeStart(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  await ensureInitializedForExplicitTask(runtime, root, args.intent, context)
  const result = await spawnCommand(
    [...CLI_COMMAND, "--project", root, args.operation, args.intent],
    root,
    context,
  )
  const intake = outcome(result, true)
  if (result.exitStatus !== 0) return intake
  return JSON.stringify({
    intake,
    context: JSON.parse(await executeRequest(runtime, { action: "context" }, context)),
  })
}

async function validateEvidencePaths(root, assessment, currentContext) {
  if (!Array.isArray(assessment.evidence) || assessment.evidence.length === 0) {
    throw new Error("assessed intake requires attributable repository evidence")
  }
  const resolvedRoot = await realpath(root)
  for (const evidence of assessment.evidence) {
    const reference = evidence?.reference
    if (typeof reference !== "string" || !reference || reference.includes("\\") || isAbsolute(reference)) {
      throw new Error("assessment evidence references must be repository-relative POSIX paths")
    }
    const segments = reference.split("/")
    if (segments.some((segment) => !segment || segment === "." || segment === ".." || segment.includes(":"))) {
      throw new Error("assessment evidence reference is not canonical")
    }
    const candidatePath = resolve(root, ...segments)
    const candidateRelative = relative(root, candidatePath)
    if (candidateRelative === ".." || candidateRelative.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(candidateRelative)) {
      throw new Error("assessment evidence escapes the executing repository")
    }
    const entry = await lstat(candidatePath)
    if (entry.isSymbolicLink() || !entry.isFile()) throw new Error("assessment evidence must be a regular repository file")
    const actualPath = await realpath(candidatePath)
    const actualRelative = relative(resolvedRoot, actualPath)
    if (actualRelative === ".." || actualRelative.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(actualRelative)) {
      throw new Error("assessment evidence resolves outside the executing repository")
    }
  }
  if (assessment.selected_feature_id !== null) {
    const established = currentContext.accepted_capabilities?.find((item) => item.feature_id === assessment.selected_feature_id)
    if (!established || assessment.capability_state !== "ESTABLISHED" || established.title !== assessment.selected_capability) {
      throw new Error("assessed intake cannot claim a capability identity absent from canonical Core context")
    }
  } else if (assessment.capability_state === "ESTABLISHED") {
    throw new Error("established capability assessment requires an existing Core feature identity")
  }
  if (assessment.capability_state === "PLANNED" && assessment.selected_feature_id !== null) {
    throw new Error("planned capability assessment cannot select an established feature")
  }
}

async function executeAssessedStart(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  if (args.assessment.operation !== args.operation.toUpperCase()) {
    throw new Error("assessment operation must exactly match NEW or CHANGE intake")
  }
  const initialized = await ensureInitializedForExplicitTask(runtime, root, args.intent, context)
  await validateEvidencePaths(root, args.assessment, initialized)
  const action = args.operation
  const request = {
    action,
    expected_revision: initialized.revision,
    intent: args.intent,
    assessment: { ...args.assessment, operation: action.toUpperCase() },
  }
  return executeRequest(runtime, request, context, {
    validateStaleReplay: async (sessionRootPath, staleRequest, refreshed) => {
      if (staleRequest.assessment?.operation !== staleRequest.action.toUpperCase()) return false
      await validateEvidencePaths(sessionRootPath, staleRequest.assessment, refreshed)
      return true
    },
  })
}

async function executeContinuation(runtime, args, context) {
  const canonical = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  let operationEvaluation = null
  let current = canonical
  if (typeof args.operation === "string" && args.operation.trim() && canonical.autonomy_envelope !== null) {
    const evaluationText = await executeRequest(runtime, {
      action: "autonomy_evaluate",
      expected_revision: canonical.revision,
      operation: args.operation,
      paths: args.paths,
      irreversible: args.irreversible,
    }, context)
    const evaluationResponse = JSON.parse(evaluationText)
    operationEvaluation = evaluationResponse.evaluation
    current = contextFromResponse(evaluationResponse) || canonical
  }
  return JSON.stringify(deriveContinuationDirective(current, {
    explicitTask: args.explicit_task,
    sessionRootMatches: true,
    ...(args.operation ? { operation: args.operation, operationEvaluation } : {}),
    externalStatus: args.external_status,
    failureCode: args.failure_code,
    attempt: args.attempt,
  }))
}

async function executeWait(runtime, args, context) {
  if (!Number.isSafeInteger(args.delay_ms) || args.delay_ms < 0 || args.delay_ms > 30000) {
    throw new Error("wait delay must be between 0 and 30000 milliseconds")
  }
  const canonical = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  const waitDirective = deriveContinuationDirective(canonical, { externalStatus: args.external_status })
  if (waitDirective.directive !== "WAIT") {
    return JSON.stringify({
      directive: waitDirective,
      reason: waitDirective.reason,
      context: canonical,
    })
  }
  await abortableDelay(args.delay_ms, context.signal)
  const refreshed = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  const directive = deriveContinuationDirective(refreshed, { externalStatus: args.external_status })
  return JSON.stringify({
    directive,
    context: refreshed,
    waited_ms: args.delay_ms,
    state: directive.directive === "WAIT" ? "PENDING" : "REEVALUATE",
  })
}

async function executeRetry(runtime, args, context) {
  const canonical = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  let evaluation = null
  if (typeof args.operation === "string" && args.operation.trim() && canonical.autonomy_envelope !== null) {
    const evaluated = JSON.parse(await executeRequest(runtime, {
      action: "autonomy_evaluate",
      expected_revision: canonical.revision,
      operation: args.operation,
      paths: args.paths,
      irreversible: args.irreversible,
    }, context))
    evaluation = evaluated.evaluation
  }
  const directive = retryDirective(canonical, { code: args.failure_code }, args.attempt)
  if (directive.retry.eligible && args.operation && evaluation?.authorization !== "ALLOWED") {
    return JSON.stringify({
      ...directive,
      directive: "FAIL_CLOSED",
      retry: { ...directive.retry, eligible: false },
      reason: "Core has not authorized retrying the requested operation",
    })
  }
  return JSON.stringify(directive)
}

function protocolTool(name, description, input, execute) {
  return {
    name,
    description,
    input,
    execute: async (args, context) => ({ content: await execute(args, context) }),
  }
}

const evidence = object({
  source: string,
  reference: string,
  feature_id: nullableString,
  living_spec_id: nullableString,
})

const assessedEvidence = object({
  source: string,
  reference: string,
  feature_id: nullableString,
  living_spec_id: nullableString,
})

const assessedDepth = object({
  review: { type: "string", enum: ["TARGETED", "LIGHT", "NORMAL", "DEEP", "FULL"] },
  verification: { type: "string", enum: ["TARGETED", "LIGHT", "NORMAL", "DEEP", "FULL"] },
  convergence: { type: "string", enum: ["TARGETED", "LIGHT", "NORMAL", "DEEP", "FULL"] },
})

const intentAssessment = object({
  operation: { type: "string", enum: ["NEW", "CHANGE"] },
  classification: { type: "string", enum: ["TUNE", "PATCH", "FEATURE", "FOUNDATION", "EXPERIMENT"] },
  confidence: { type: "string", enum: ["LOW", "MEDIUM", "HIGH"] },
  capability_state: { type: "string", enum: ["PLANNED", "ESTABLISHED", "PROJECT_ESTABLISHED", "UNKNOWN"] },
  selected_capability: nullableString,
  candidate_capabilities: stringArray,
  selected_feature_id: nullableString,
  evidence: { type: "array", items: assessedEvidence, minItems: 1 },
  material_ambiguities: { type: "array", items: object({ decision: string, options: { type: "array", items: string, minItems: 2 } }) },
  recommended_depth: assessedDepth,
  rationale: string,
})

const tools = [
  protocolTool(
    "evospec",
    "Read EvoSpec context or submit one provider-neutral protocol request for the executing session's repository.",
    object({ request: string }),
    (args, context) => executeRequest(context.runtime, args.request, context),
  ),
  protocolTool(
    "evospec_start",
    "Start natural EvoSpec NEW or CHANGE intake for the executing session repository; initialize only when Core permits and the explicit task identifies this repository.",
    object({ operation: { type: "string", enum: ["new", "change"] }, intent: string }),
    (args, context) => executeStart(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_assessed_start",
    "Use after bounded natural discovery is exhausted only when real repository evidence supports this honest typed assessment; Core remains authoritative and may reject it.",
    object({
      expected_revision: nonnegativeInteger,
      operation: { type: "string", enum: ["new", "change"] },
      intent: string,
      assessment: intentAssessment,
    }),
    (args, context) => executeAssessedStart(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_continuation",
    "Read canonical context and derive one deterministic OpenCode continuation directive; Core remains authoritative for legal actions and operation evaluation.",
    object({
      explicit_task: { type: ["string", "null"] },
      operation: { type: ["string", "null"] },
      paths: stringArray,
      irreversible: boolean,
      external_status: { type: ["string", "null"], enum: ["PENDING", "RUNNING", "SUCCESS", "TRANSIENT_FAILURE", "FAILURE", null] },
      failure_code: nullableString,
      attempt: nonnegativeInteger,
    }),
    (args, context) => executeContinuation(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_wait",
    "Wait once for a bounded interval only when canonical Core context classifies work as EXTERNAL_WAIT; cancellation interrupts promptly and fresh context is returned.",
    object({
      delay_ms: nonnegativeInteger,
      external_status: { type: "string", enum: ["PENDING", "RUNNING"] },
    }),
    (args, context) => executeWait(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_retry",
    "Derive bounded retry eligibility from the current Core retry budget and a recognized transient failure code; deterministic failures are not retried.",
    object({
      failure_code: string,
      attempt: nonnegativeInteger,
      operation: nullableString,
      paths: stringArray,
      irreversible: boolean,
    }),
    (args, context) => executeRetry(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_context",
    "Read canonical EvoSpec context and guidance for the executing session's repository.",
    object({}),
    (_, context) => executeRequest(context.runtime, { action: "context" }, context),
  ),
  protocolTool(
    "evospec_work_list",
    "List bounded EvoSpec work queue state using the revision returned by canonical context.",
    object({ expected_revision: nonnegativeInteger, filter: { type: "string", enum: ["all", "pending", "actionable", "blocked", "frozen", "waiting", "active", "next"] } }),
    (args, context) => executeRequest(context.runtime, { action: "list_work", ...args }, context),
  ),
  protocolTool(
    "evospec_work_next",
    "Select the deterministic next EvoSpec work item without activating it.",
    object({ expected_revision: nonnegativeInteger }),
    (args, context) => executeRequest(context.runtime, { action: "next_work", ...args }, context),
  ),
  protocolTool(
    "evospec_work_activate",
    "Activate a stable work ID when canonical context shows no active work and the item is eligible.",
    object({ expected_revision: nonnegativeInteger, work_id: string }),
    (args, context) => executeRequest(context.runtime, { action: "activate_work", ...args }, context),
  ),
  protocolTool(
    "evospec_work_state",
    "Set a non-active work item's operational state at the current revision.",
    object({
      expected_revision: nonnegativeInteger,
      work_id: string,
      operational_state: { type: "string", enum: ["READY", "BLOCKED", "FROZEN", "WAITING"] },
      reason: nullableString,
    }),
    (args, context) => executeRequest(context.runtime, { action: "set_work_state", ...args }, context),
  ),
  protocolTool(
    "evospec_work_relation",
    "Add or remove one strict relation between stable work IDs at the current revision.",
    object({
      expected_revision: nonnegativeInteger,
      operation: { type: "string", enum: ["add", "remove"] },
      predecessor_id: string,
      successor_id: string,
      kind: { type: "string", enum: ["HARD", "SOFT", "CONFLICT"] },
    }),
    (args, context) => {
      const { operation, ...relation } = args
      return executeRequest(context.runtime, {
        action: operation === "add" ? "add_work_relation" : "remove_work_relation",
        ...relation,
      }, context)
    },
  ),
  protocolTool(
    "evospec_recover_discovery",
    "Forward exactly one Core-offered bounded discovery recovery tuple from canonical context.",
    object({
      expected_revision: nonnegativeInteger,
      recovery_id: string,
      candidate: string,
      evidence_ids: stringArray,
    }),
    (args, context) => executeRequest(context.runtime, { action: "recover_discovery", ...args }, context),
  ),
  protocolTool(
    "evospec_select_capability",
    "Forward one explicit human choice offered by Core for a pending capability selection.",
    object({ expected_revision: nonnegativeInteger, selection_id: string, choice: string }),
    (args, context) => executeRequest(context.runtime, { action: "select_capability", ...args }, context),
  ),
  protocolTool(
    "evospec_progress",
    "Record complete implementation progress at the context revision.",
    object({
      expected_revision: nonnegativeInteger,
      current_action: nullableString,
      focus: nullableString,
      completed_work: stringArray,
      current_work: nullableString,
      pending_work: stringArray,
      resume_point: nullableString,
    }),
    (args, context) => executeRequest(context.runtime, { action: "progress", ...args }, context),
  ),
  protocolTool(
    "evospec_review",
    "Record the caller's product review at the context revision.",
    object({
      expected_revision: nonnegativeInteger,
      verdict: { type: "string", enum: ["PASS", "WARN", "BLOCKED"] },
      findings: stringArray,
      safe_to_proceed: boolean,
    }),
    (args, context) => {
      const { expected_revision, ...review } = args
      return executeRequest(context.runtime, { action: "review", expected_revision, review }, context)
    },
  ),
  protocolTool(
    "evospec_verify",
    "Record technical verification evidence at the context revision.",
    object({ expected_revision: nonnegativeInteger, evidence: stringArray, sufficient: boolean }),
    (args, context) => {
      const { expected_revision, ...verification } = args
      return executeRequest(context.runtime, { action: "verify", expected_revision, verification }, context)
    },
  ),
  protocolTool(
    "evospec_continue",
    "Resume EvoSpec work when current context permits it.",
    object({ expected_revision: nonnegativeInteger }),
    (args, context) => executeRequest(context.runtime, { action: "continue", ...args }, context),
  ),
  protocolTool(
    "evospec_clarify",
    "Submit only answers to question IDs returned by canonical context.",
    object({
      expected_revision: nonnegativeInteger,
      answers: { type: "array", items: object({ question_id: string, answer: string }) },
    }),
    (args, context) => executeRequest(context.runtime, { action: "clarify", ...args }, context),
  ),
  protocolTool(
    "evospec_structure_material_clarification",
    "Structure one exact legacy unresolved clarification without choosing its answer. Requires current repository, Feature, Change and revision.",
    object({ expected_revision: nonnegativeInteger, repository_id: string, feature_id: string,
      change_id: string, legacy_detail: string, decision: string,
      options: { type: "array", items: string, minItems: 2 } }),
    (args, context) => executeRequest(context.runtime, { action: "structure_material_clarification", ...args }, context),
  ),
  protocolTool(
    "evospec_recover_legacy_non_material_detail",
    "Audit and remove one exact legacy detail explicitly confirmed as non-material. Requires current repository, Feature, Change, detail and revision; accepts no replacement prose.",
    object({ expected_revision: nonnegativeInteger, repository_id: string, feature_id: string,
      change_id: string, legacy_detail: string }),
    (args, context) => executeRequest(context.runtime, { action: "recover_legacy_non_material_detail", ...args }, context),
  ),
  protocolTool(
    "evospec_converge",
    "Submit caller-authored convergence assessment; Product Truth content is required and nonblank when product_truth_changed is true, otherwise provide null.",
    object({
      expected_revision: nonnegativeInteger,
      change_id: string,
      product_truth_changed: boolean,
      product_truth_content: nullableString,
      confidence: { type: "string", enum: ["LOW", "MEDIUM", "HIGH"] },
      evidence: { type: "array", items: evidence },
      rationale: string,
    }),
    (args, context) => {
      const { expected_revision, ...assessment } = args
      return executeRequest(context.runtime, { action: "converge", expected_revision, assessment }, context)
    },
  ),
  protocolTool(
    "evospec_irreversible_authorize",
    "Record the user's explicit, exact, one-shot Core authorization for one Git history rewrite; this does not execute Git.",
    object({
      expected_revision: nonnegativeInteger,
      operation_kind: { type: "string", enum: ["GIT_HISTORY_REWRITE"] },
      target_ref: string,
      expected_remote_sha: string,
      rewrite_policy: { type: "string", enum: ["NORMALIZE_MISMATCHED_IDENTITIES"] },
      target_identity_name: string,
      target_identity_email: string,
      expected_result_sha: string,
      commit_rewrites: { type: "array", items: commitRewrite, minItems: 1 },
      purpose: string,
      force_with_lease: { type: "boolean", enum: [true] },
    }),
    (args, context) => executeRequest(context.runtime, { action: "authorize_irreversible", ...args }, context),
  ),
  protocolTool(
    "evospec_irreversible_execute",
    "Execute the persisted one-shot Git rewrite in Core with an explicit force-with-lease and consume it only after verifying the remote result.",
    object({
      expected_revision: nonnegativeInteger,
      authorization_id: string,
    }),
    (args, context) => executeRequest(context.runtime, { action: "execute_irreversible", ...args }, context),
  ),
]

export default {
  id: "evospec",
  async setup(runtime) {
    const runtimeTools = tools.map((definition) => ({
      ...definition,
      execute: (args, context) => definition.execute(args, { ...context, runtime }),
    }))
    await runtime.tool.transform((editor) => {
      for (const definition of runtimeTools) editor.add(definition)
    })
    const registered = await runtime.tool.list()
    const names = new Set(registered.map((tool) => tool.id))
    const missing = runtimeTools.map((tool) => tool.name).filter((name) => !names.has(name))
    if (missing.length) throw new Error(`EvoSpec V2 tools were not registered: ${missing.join(", ")}`)
    await runtime.session.hook("context", (event) => {
      event.system.push({
        type: "text",
        text: "For EvoSpec lifecycle work, load the evospec skill and call evospec_context before repository inspection, tests, or lifecycle mutations. Then use evospec_continuation and follow its deterministic adapter directive: SAFE_AUTOMATIC_OPERATION and in-envelope BOUNDED_DELEGATION continue; EXTERNAL_WAIT waits; HUMAN_MATERIAL_DECISION asks exactly Core's decision; STRUCTURAL_DEFECT fails closed; terminal with outcome null completes. Re-read context after every Core mutation, reevaluate STALE_REVISION before any retry, and never edit .evospec manually.",
      })
    })
  },
}
