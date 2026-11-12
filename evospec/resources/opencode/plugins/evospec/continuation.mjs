// EvoSpec-managed: opencode-v2

export const CONTINUATION_DIRECTIVES = Object.freeze({
  CONTINUE: "CONTINUE",
  WAIT: "WAIT",
  ASK_USER: "ASK_USER",
  FAIL_CLOSED: "FAIL_CLOSED",
  COMPLETE: "COMPLETE",
})

const CORE_OUTCOME_KINDS = new Set([
  "HUMAN_MATERIAL_DECISION",
  "SAFE_AUTOMATIC_OPERATION",
  "BOUNDED_DELEGATION",
  "EXTERNAL_WAIT",
  "STRUCTURAL_DEFECT",
])

const TRANSIENT_FAILURE_CODES = new Set([
  "EAGAIN",
  "EBUSY",
  "ETXTBSY",
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNREFUSED",
  "ENETUNREACH",
  "EHOSTUNREACH",
  "RUNNER_UNAVAILABLE",
  "DELEGATED_WORKER_TRANSIENT",
  "TRANSPORT_TEMPORARY",
])

const failClosed = (reason, refreshContext = false) => ({
  directive: CONTINUATION_DIRECTIVES.FAIL_CLOSED,
  refresh_context: refreshContext,
  next_action: null,
  wait: null,
  retry: { eligible: false, attempt: 0, budget: 0 },
  reason,
  material_decision: null,
})

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function validateMaterialDecision(value) {
  if (!isRecord(value) || typeof value.decision !== "string" || !value.decision.trim()) return null
  if (!Array.isArray(value.options) || value.options.length < 2) return null
  if (value.options.some((option) => typeof option !== "string" || !option.trim())) return null
  if (new Set(value.options).size !== value.options.length) return null
  return { decision: value.decision, options: [...value.options] }
}

function offeredNextAction(context) {
  const legal = context.legal_actions
  const guidance = context.guidance
  if (!Array.isArray(legal) || !Array.isArray(guidance)) return null

  const queue = context.work_queue
  if (isRecord(queue) && queue.next_code === "AMBIGUOUS") return { ambiguous: true }
  if (isRecord(queue) && queue.next_code === "SELECTED") {
    if (typeof queue.next !== "string" || !legal.includes("activate_work")) return null
    return { action: "activate_work", arguments: { work_id: queue.next } }
  }

  // Core owns action ordering and eligibility; the adapter only filters to
  // actions Core explicitly permits without user intervention.
  for (const action of legal) {
    if (action === "context") continue
    const offered = guidance.find((item) => item?.action === action)
    if (offered?.allowed === true && offered.intervention === "AUTOMATIC") {
      return { action, arguments: null }
    }
  }
  return null
}

export function deriveContinuationDirective(context, options = {}) {
  if (!isRecord(context) || !Number.isSafeInteger(context.revision) || context.revision < 0) {
    return failClosed("canonical context projection is missing or malformed")
  }

  if (context.completed_terminal === true && context.outcome === null) {
    return {
      directive: CONTINUATION_DIRECTIVES.COMPLETE,
      refresh_context: false,
      next_action: null,
      wait: null,
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: "authoritative Core context is terminal",
      material_decision: null,
    }
  }
  if (context.completed_terminal === true && context.outcome !== null) {
    return failClosed("terminal Core context unexpectedly contains a non-null outcome")
  }

  if (context.stale === true) {
    return {
      directive: CONTINUATION_DIRECTIVES.CONTINUE,
      refresh_context: true,
      next_action: "context",
      wait: null,
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: typeof context.stale_reason === "string" ? context.stale_reason : "Core context is stale",
      material_decision: null,
    }
  }

  const legal = context.legal_actions
  if (!Array.isArray(legal)) return failClosed("Core legal_actions projection is missing")

  if (context.initialization_state === "UNINITIALIZED") {
    const init = Array.isArray(context.guidance)
      ? context.guidance.find((item) => item?.action === "init")
      : null
    if (
      typeof options.explicitTask === "string" && options.explicitTask.trim() &&
      options.sessionRootMatches === true && legal.includes("init") && init?.allowed === true
    ) {
      return {
        directive: CONTINUATION_DIRECTIVES.CONTINUE,
        refresh_context: false,
        next_action: "init",
        wait: null,
        retry: { eligible: false, attempt: 0, budget: 0 },
        reason: "the explicit task and executing session identify this repository; Core permits initialization",
        material_decision: null,
      }
    }
    return failClosed("repository initialization requires an explicit task for the executing repository")
  }

  const outcome = context.outcome
  if (!isRecord(outcome) || !CORE_OUTCOME_KINDS.has(outcome.kind)) {
    return failClosed("nonterminal Core context has no recognized authoritative outcome")
  }

  if (outcome.kind === "HUMAN_MATERIAL_DECISION") {
    const decision = validateMaterialDecision(outcome.material_decision)
    if (decision === null) return failClosed("Core material decision does not contain one concrete decision and distinct choices")
    return {
      directive: CONTINUATION_DIRECTIVES.ASK_USER,
      refresh_context: false,
      next_action: null,
      wait: null,
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: outcome.message,
      material_decision: decision,
    }
  }

  if (outcome.kind === "STRUCTURAL_DEFECT") {
    const recovery = context.pending_discovery_recovery
    if (isRecord(recovery) && legal.includes("recover_discovery") && Array.isArray(recovery.candidates)) {
      if (recovery.candidates.length === 1) {
        const candidate = recovery.candidates[0]
        if (
          typeof recovery.recovery_id === "string" && typeof candidate?.candidate === "string" &&
          Array.isArray(candidate.evidence_ids) && candidate.evidence_ids.length > 0
        ) {
          return {
            directive: CONTINUATION_DIRECTIVES.CONTINUE,
            refresh_context: false,
            next_action: {
              action: "recover_discovery",
              arguments: {
                recovery_id: recovery.recovery_id,
                candidate: candidate.candidate,
                evidence_ids: [...candidate.evidence_ids],
              },
            },
            wait: null,
            retry: { eligible: false, attempt: 0, budget: 0 },
            reason: "Core offered exactly one bounded discovery recovery candidate",
            material_decision: null,
          }
        }
      }
    }
    return failClosed(outcome.message || "Core reports an unrecoverable structural defect")
  }

  const externalStatus = options.externalStatus
  if (externalStatus !== undefined && externalStatus !== null &&
      !["PENDING", "RUNNING", "SUCCESS", "TRANSIENT_FAILURE", "FAILURE"].includes(externalStatus)) {
    return failClosed("external result status is not a recognized provider-neutral state")
  }
  if (externalStatus === "PENDING" || externalStatus === "RUNNING") {
    if (context.autonomy_envelope?.external_wait_allowed !== true) {
      return failClosed("external work is pending outside the active Core wait authority")
    }
    return {
      directive: CONTINUATION_DIRECTIVES.WAIT,
      refresh_context: false,
      next_action: null,
      wait: { initial_delay_ms: 1000, maximum_delay_ms: 30000, polls_per_cycle: 20 },
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: "provider reports external work is still pending",
      material_decision: null,
    }
  }
  if (externalStatus === "TRANSIENT_FAILURE") {
    const retry = retryDirective(context, { code: options.failureCode }, options.attempt ?? 0)
    if (retry.retry.eligible && options.operation !== undefined &&
        (!isRecord(options.operationEvaluation) || options.operationEvaluation.authorization !== "ALLOWED")) {
      return failClosed("Core has not authorized retrying the requested external operation")
    }
    return retry
  }

  if (outcome.kind === "EXTERNAL_WAIT" &&
      (externalStatus === "SUCCESS" || externalStatus === "FAILURE")) {
    const next = offeredNextAction(context)
    if (next === null || next.ambiguous) {
      return failClosed("external result is terminal, but Core exposes no unambiguous legal continuation action")
    }
    return {
      directive: CONTINUATION_DIRECTIVES.CONTINUE,
      refresh_context: false,
      next_action: next,
      wait: null,
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: externalStatus === "SUCCESS"
        ? "external operation succeeded; continue with the current Core-authorized lifecycle"
        : "external operation completed with a product or validation failure; repair it, then rerun",
      material_decision: null,
    }
  }

  if (outcome.kind === "EXTERNAL_WAIT") {
    if (context.autonomy_envelope?.external_wait_allowed !== true) {
      return failClosed("Core outcome requires an external wait outside the active envelope")
    }
    return {
      directive: CONTINUATION_DIRECTIVES.WAIT,
      refresh_context: false,
      next_action: null,
      wait: { initial_delay_ms: 1000, maximum_delay_ms: 30000, polls_per_cycle: 20 },
      retry: { eligible: false, attempt: 0, budget: 0 },
      reason: outcome.message,
      material_decision: null,
    }
  }

  const next = offeredNextAction(context)
  if (next?.ambiguous) return failClosed("Core work queue reports an ambiguous selection; no candidate was auto-selected")
  if (next === null) return failClosed("Core outcome permits continuation but exposes no automatically legal next action")

  if (outcome.kind === "BOUNDED_DELEGATION") {
    if (!isRecord(context.autonomy_envelope)) return failClosed("bounded delegation has no active Core Autonomy Envelope")
    if (options.operation !== undefined) {
      const evaluation = options.operationEvaluation
      if (!isRecord(evaluation) || evaluation.authorization !== "ALLOWED") {
        return failClosed("Core has not authorized the requested delegated operation")
      }
    }
  }

  return {
    directive: CONTINUATION_DIRECTIVES.CONTINUE,
    refresh_context: false,
    next_action: next,
    wait: null,
    retry: { eligible: false, attempt: 0, budget: 0 },
    reason: options.externalStatus === "FAILURE"
      ? "external operation completed with a product or validation failure; repair it, then rerun"
      : outcome.message,
    material_decision: null,
  }
}

export function classifyTransientFailure(failure) {
  const code = typeof failure === "string" ? failure : failure?.code
  return typeof code === "string" && TRANSIENT_FAILURE_CODES.has(code.toUpperCase())
}

export function retryDirective(context, failure, attempt) {
  const envelope = context?.autonomy_envelope
  const budget = envelope?.retry_budget
  if (!Number.isSafeInteger(attempt) || attempt < 0 || !Number.isSafeInteger(budget) || budget < 0) {
    return failClosed("Core retry budget or retry attempt is malformed")
  }
  if (!classifyTransientFailure(failure)) return failClosed("failure is deterministic or is not a recognized transient infrastructure condition")
  if (attempt >= budget) return failClosed("Core retry budget is exhausted")
  const base = deriveContinuationDirective(context)
  if (base.directive !== CONTINUATION_DIRECTIVES.CONTINUE && base.directive !== CONTINUATION_DIRECTIVES.WAIT) return base
  return {
    ...base,
    directive: CONTINUATION_DIRECTIVES.CONTINUE,
    retry: { eligible: true, attempt: attempt + 1, budget },
    reason: `retrying a recognized transient failure within Core retry budget (${attempt + 1}/${budget})`,
  }
}

export function retryDelayMs(attempt, initialDelayMs = 1000, maximumDelayMs = 30000) {
  if (!Number.isSafeInteger(attempt) || attempt < 1) throw new RangeError("attempt must be a positive integer")
  if (!Number.isFinite(initialDelayMs) || initialDelayMs < 0 || !Number.isFinite(maximumDelayMs) || maximumDelayMs < initialDelayMs) {
    throw new RangeError("retry delay bounds are invalid")
  }
  return Math.min(maximumDelayMs, initialDelayMs * (2 ** Math.min(attempt - 1, 30)))
}

function abortError(signal) {
  if (signal?.reason instanceof Error) return signal.reason
  const error = new Error("operation cancelled")
  error.name = "AbortError"
  return error
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError(signal)
}

export function abortableDelay(delayMs, signal, setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout) {
  throwIfAborted(signal)
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      clearTimer(timer)
      signal?.removeEventListener("abort", onAbort)
      callback(value)
    }
    const onAbort = () => finish(reject, abortError(signal))
    const timer = setTimer(() => finish(resolve), delayMs)
    signal?.addEventListener("abort", onAbort, { once: true })
    if (signal?.aborted) onAbort()
  })
}

export async function runWithRetry(operation, {
  retryBudget,
  signal,
  wait = abortableDelay,
  classify = classifyTransientFailure,
  initialDelayMs = 1000,
  maximumDelayMs = 30000,
} = {}) {
  if (!Number.isSafeInteger(retryBudget) || retryBudget < 0) throw new RangeError("retryBudget must be a nonnegative integer")
  let retries = 0
  for (;;) {
    throwIfAborted(signal)
    try {
      return await operation()
    } catch (error) {
      if (!classify(error)) throw error
      if (retries >= retryBudget) {
        const exhausted = new Error(`transient operation exhausted retry budget ${retryBudget}`)
        exhausted.name = "RetryExhaustedError"
        exhausted.cause = error
        throw exhausted
      }
      retries += 1
      await wait(retryDelayMs(retries, initialDelayMs, maximumDelayMs), signal)
    }
  }
}

export async function waitForExternalResult(poll, {
  signal,
  maxPolls = 20,
  retryBudget = 0,
  initialDelayMs = 1000,
  maximumDelayMs = 30000,
  wait = abortableDelay,
} = {}) {
  if (!Number.isSafeInteger(maxPolls) || maxPolls < 1 || maxPolls > 1000) throw new RangeError("maxPolls must be between 1 and 1000")
  if (!Number.isSafeInteger(retryBudget) || retryBudget < 0) throw new RangeError("retryBudget must be a nonnegative integer")
  let retries = 0
  for (let count = 1; count <= maxPolls; count += 1) {
    throwIfAborted(signal)
    let result
    try {
      result = await poll()
    } catch (error) {
      if (!classifyTransientFailure(error)) throw error
      if (retries >= retryBudget) throw error
      retries += 1
      await wait(retryDelayMs(retries, initialDelayMs, maximumDelayMs), signal)
      count -= 1
      continue
    }
    if (!isRecord(result) || !["pending", "running", "success", "failure"].includes(result.status)) {
      throw new TypeError("external poll must return pending, running, success, or failure")
    }
    if (result.status === "success" || result.status === "failure") return { ...result, polls: count }
    if (count === maxPolls) return { status: result.status, polls: count }
    await wait(retryDelayMs(count, initialDelayMs, maximumDelayMs), signal)
  }
  return { status: "pending", polls: maxPolls }
}
