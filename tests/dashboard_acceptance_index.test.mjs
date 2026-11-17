import assert from "node:assert/strict"
import test from "node:test"
import { readFile } from "node:fs/promises"
import { runInNewContext } from "node:vm"

const dashboardSource = await readFile(new URL("../evospec/resources/dashboard/dashboard.js", import.meta.url), "utf8")

class Element {
  constructor(tagName) {
    this.tagName = tagName
    this.children = []
    this.dataset = {}
    this.attributes = new Map()
    this.listeners = new Map()
    this.className = ""
    this.hidden = false
    this.tabIndex = -1
    this._textContent = ""
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("")
  }

  set textContent(value) {
    this._textContent = String(value)
    this.children = []
  }

  append(...nodes) {
    this.children.push(...nodes)
  }

  replaceChildren(...nodes) {
    this._textContent = ""
    this.children = []
    this.append(...nodes)
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value))
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null
  }

  addEventListener(type, callback) {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(callback)
    this.listeners.set(type, listeners)
  }

  click() {
    for (const callback of this.listeners.get("click") ?? []) callback({ type: "click", target: this })
  }

  focus() {}
}

function makeContext({
  revision = 7,
  featureTitle = "Personal task list",
  sliceTitle = "Restore saved tasks",
  criteriaTitles = ["Saved tasks reappear after reopening", "An empty list has a clear message"],
  verifications,
  outcome = "READY",
  sliceOutcome,
  criteriaRevision,
  contextOverrides = {},
} = {}) {
  const revisionId = `criteria-${revision}`
  const criteria = criteriaTitles.map((expected_result, index) => ({
    id: `AC-${index + 1}`,
    expected_result,
    expected_evidence: "python-unittest:private-test-target",
  }))
  const acceptedSlice = {
    feature_agreement_ref: "feature-ref-private",
    slice_agreement_ref: "slice-ref-private",
    feature_title: featureTitle,
    slice_title: sliceTitle,
    task_id: "TASK-private",
    criteria_status: "RECORDED",
    criteria_revision: criteriaRevision === undefined ? { id: revisionId, criteria } : criteriaRevision,
  }
  const criterionVerification = verifications === undefined
    ? criteria.map((criterion) => ({
      criterion_id: criterion.id,
      criteria_revision_id: revisionId,
      status: "UNVERIFIED",
      fresh_for_current_workspace: false,
      last_test: { target: "private-test-target", receipt_id: "private-receipt" },
    }))
    : verifications.map((verification, index) => ({
      criterion_id: criteria[index]?.id,
      criteria_revision_id: revisionId,
      ...verification,
    }))
  const receiptId = `receipt-${revision}`
  const defaultOutcome = outcome === null ? null : outcome === "CURRENT"
    ? {
      status: "CURRENT",
      latest_receipt_id: receiptId,
      receipt: { id: receiptId, input_fingerprint: "private-hash", evidence_receipt_ids: ["private-evidence"] },
    }
    : { status: outcome, latest_receipt_id: null, receipt: null, blockers: ["private-technical-blocker"] }
  const context = {
    revision,
    repository_id: "private-repository-id",
    accepted_slice: acceptedSlice,
    criterion_verification: criterionVerification,
    slice_outcome: sliceOutcome === undefined ? defaultOutcome : sliceOutcome,
    accepted_capabilities: [{ feature_id: "feature-1", title: featureTitle }],
    work_queue: {
      active: null,
      all: [{ id: "TASK-completed-private", feature_id: "feature-1", operational_state: "DONE", lifecycle: [] }],
      blocked: [],
      complete: true,
      total: 1,
      relations: [],
    },
    progress: {},
  }
  Object.assign(context, contextOverrides)
  return context
}

function makeResponse(context, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => context }
}

function makeDashboard(fetcher) {
  const ids = new Map([
    "load-status", "project-summary", "current-work-section", "current-work-title",
    "current-work-status", "current-work-explanation", "active-slice-status",
    "active-slice-content", "blocked-section", "blocked-list", "features-list",
    "tab-features", "tab-technical", "features-panel", "technical-panel", "technical-content",
  ].map((id) => [id, new Element(id)]))
  ids.get("active-slice-status").textContent = "Loading"
  ids.get("active-slice-content").append(Object.assign(new Element("p"), {
    className: "empty-state",
    textContent: "Loading the active Slice and acceptance criteria.",
  }))
  const filters = ["all", "completed", "blocked"].map((filter) => {
    const button = new Element("button")
    button.dataset.featureFilter = filter
    return button
  })
  const document = {
    getElementById: (id) => ids.get(id) ?? null,
    querySelectorAll: (selector) => selector === "[data-feature-filter]" ? filters : [],
    createElement: (tagName) => new Element(tagName),
    createTextNode: (text) => Object.assign(new Element("#text"), { textContent: text }),
  }
  const windowListeners = new Map()
  const window = {
    addEventListener(type, callback) {
      const listeners = windowListeners.get(type) ?? []
      listeners.push(callback)
      windowListeners.set(type, listeners)
    },
  }
  const fetchCalls = []
  const sources = []
  class EventSource {
    static OPEN = 1
    static CLOSED = 2

    constructor(url) {
      this.url = url
      this.readyState = EventSource.OPEN
      this.listeners = new Map()
      sources.push(this)
    }

    addEventListener(type, callback) {
      const listeners = this.listeners.get(type) ?? []
      listeners.push(callback)
      this.listeners.set(type, listeners)
    }

    emit(type, event = {}) {
      for (const callback of this.listeners.get(type) ?? []) callback({ type, ...event })
    }

    close() {
      this.readyState = EventSource.CLOSED
    }
  }
  let nextInterval = 0
  const intervals = new Map()
  const fetch = (url, options) => {
    fetchCalls.push({ url, options })
    return fetcher(url, options, fetchCalls.length)
  }
  runInNewContext(dashboardSource, {
    document,
    window,
    EventSource,
    fetch,
    setInterval: (callback) => {
      const id = ++nextInterval
      intervals.set(id, callback)
      return id
    },
    clearInterval: (id) => intervals.delete(id),
  }, { filename: "dashboard.js" })
  return { ids, filters, fetchCalls, sources, windowListeners, intervals }
}

function descendants(root) {
  return [root, ...root.children.flatMap(descendants)]
}

function criterionRows(app) {
  return descendants(app.ids.get("active-slice-content")).filter((element) => element.className === "active-slice-criterion")
}

function rowStatus(row) {
  return descendants(row).find((element) => element.className === "status-label")?.textContent
}

function rowResult(row) {
  return descendants(row).find((element) => element.className === "active-slice-criterion-result")?.textContent
}

async function waitFor(predicate, message = "dashboard did not settle") {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  assert.fail(message)
}

async function loadedDashboard(context) {
  const app = makeDashboard(async () => makeResponse(context))
  await waitFor(() => app.fetchCalls.length === 1 && app.ids.get("active-slice-status").textContent !== "Loading")
  return app
}

test("renders Feature, active Slice, and current criteria from the canonical GET snapshot", async (t) => {
  const serialized = process.env.EVOSPEC_DASHBOARD_ACCEPTANCE_CONTEXT
  if (!serialized) {
    t.skip("Python dashboard wrapper supplies a canonical GET /api/context snapshot")
    return
  }
  const context = JSON.parse(serialized)
  const accepted = context.accepted_slice
  assert.ok(accepted?.criteria_revision)
  assert.ok(accepted.criteria_revision.criteria.length >= 2)
  const app = await loadedDashboard(context)
  const content = app.ids.get("active-slice-content").textContent
  assert.ok(content.includes(accepted.feature_title))
  assert.ok(content.includes(accepted.slice_title))
  assert.deepEqual(criterionRows(app).map(rowResult), accepted.criteria_revision.criteria.map((criterion) => criterion.expected_result))
  assert.equal(criterionRows(app).length, accepted.criteria_revision.criteria.length)
  assert.deepEqual(criterionRows(app).map(rowStatus), accepted.criteria_revision.criteria.map(() => "Pending"))
  for (const privateValue of [
    accepted.feature_agreement_ref,
    accepted.slice_agreement_ref,
    accepted.task_id,
    accepted.criteria_revision.id,
    ...accepted.criteria_revision.criteria.map((criterion) => criterion.id),
    ...accepted.criteria_revision.criteria.map((criterion) => criterion.expected_evidence),
  ]) {
    assert.equal(content.includes(privateValue), false, `primary index leaked ${privateValue}`)
  }
  assert.equal(app.fetchCalls.length, 1)
  assert.equal(app.fetchCalls[0].url, "/api/context")
  assert.equal(app.fetchCalls[0].options.method, undefined)
})

test("READY and completed Tasks never become Slice acceptance", async () => {
  const context = makeContext({ outcome: "READY" })
  const app = await loadedDashboard(context)
  assert.equal(context.work_queue.complete, true)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
  assert.notEqual(app.ids.get("active-slice-status").textContent, "Slice accepted")
  assert.ok(app.ids.get("active-slice-content").textContent.includes("not yet accepted"))

  const noSlice = await loadedDashboard({ ...context, accepted_slice: null, slice_outcome: makeContext({ outcome: "CURRENT" }).slice_outcome })
  assert.equal(noSlice.ids.get("active-slice-status").textContent, "No active Slice")
  assert.ok(noSlice.ids.get("active-slice-content").textContent.includes("No active accepted Slice."))
})

test("only a CURRENT outcome with its matching canonical receipt says Slice accepted", async () => {
  const accepted = await loadedDashboard(makeContext({ outcome: "CURRENT" }))
  assert.equal(accepted.ids.get("active-slice-status").textContent, "Slice accepted")
  const primary = accepted.ids.get("active-slice-content").textContent
  for (const privateValue of ["private-hash", "private-evidence", "private-receipt", "TASK-private", "AC-1", "private-test-target", "feature-ref-private", "slice-ref-private"]) {
    assert.equal(primary.includes(privateValue), false, `primary index leaked ${privateValue}`)
  }

  const mismatch = makeContext({ outcome: "CURRENT" })
  mismatch.slice_outcome.receipt.id = "different-receipt"
  const unavailable = await loadedDashboard(mismatch)
  assert.equal(unavailable.ids.get("active-slice-status").textContent, "Acceptance status unavailable")

  const missingReceipt = makeContext({ outcome: "CURRENT" })
  missingReceipt.slice_outcome.receipt = null
  assert.equal((await loadedDashboard(missingReceipt)).ids.get("active-slice-status").textContent, "Acceptance status unavailable")
})

test("criterion states remain distinct and legacy VERIFIED freshness is not current", async () => {
  const specifications = [
    { status: "UNVERIFIED", label: "Pending" },
    { status: "FAILED", fresh_for_current_workspace: false, label: "Failed" },
    { status: "STALE", fresh_for_current_workspace: false, label: "Revalidate" },
    { status: "REVALIDATION_REQUIRED", fresh_for_current_workspace: false, label: "Revalidate" },
    { status: "VERIFIED", fresh_for_current_workspace: true, label: "Verified (current)" },
    { status: "VERIFIED", fresh_for_current_workspace: false, label: "Verified (freshness unavailable)" },
    { status: "VERIFIED", label: "Verified (freshness unavailable)" },
    { status: "FUTURE_STATE", fresh_for_current_workspace: true, label: "Status unavailable" },
  ]
  const context = makeContext({
    criteriaTitles: specifications.map((_, index) => `Criterion ${index + 1}`),
    verifications: specifications.map(({ label: _label, ...verification }) => verification),
    outcome: null,
  })
  const app = await loadedDashboard(context)
  assert.deepEqual(criterionRows(app).map(rowStatus), specifications.map(({ label }) => label))
  assert.equal(app.ids.get("active-slice-status").textContent, "Acceptance status unavailable")
})

test("missing Slice, criteria, old schema, unknown state, and context errors fail closed", async () => {
  const base = makeContext({ outcome: "CURRENT" })
  const oldSchema = { revision: 2, work_queue: base.work_queue, accepted_capabilities: base.accepted_capabilities }
  const old = await loadedDashboard(oldSchema)
  assert.equal(old.ids.get("active-slice-status").textContent, "Status unavailable")
  assert.ok(old.ids.get("active-slice-content").textContent.includes("not available in this schema"))

  const noCriteria = makeContext({ criteriaRevision: null, outcome: "CURRENT" })
  const absent = await loadedDashboard(noCriteria)
  assert.equal(absent.ids.get("active-slice-status").textContent, "Criteria not recorded")
  assert.ok(absent.ids.get("active-slice-content").textContent.includes("not recorded yet"))

  const emptyCriteria = makeContext({ criteriaRevision: { id: "criteria-empty", criteria: [] } })
  assert.equal((await loadedDashboard(emptyCriteria)).ids.get("active-slice-status").textContent, "Criteria not recorded")

  for (const [outcome, label] of [["BLOCKED", "Blocked"], ["STALE", "Revalidate"], ["REVALIDATION_REQUIRED", "Revalidate"]]) {
    const app = await loadedDashboard(makeContext({ outcome }))
    assert.equal(app.ids.get("active-slice-status").textContent, label)
  }
  const unknown = await loadedDashboard(makeContext({ outcome: "FUTURE_OUTCOME" }))
  assert.equal(unknown.ids.get("active-slice-status").textContent, "Acceptance status unavailable")

  const error = await loadedDashboard({ ...base, dashboard_error: "private/path must not be shown" })
  assert.equal(error.ids.get("active-slice-status").textContent, "Status unavailable")
  assert.ok(error.ids.get("active-slice-content").textContent.includes("could not be loaded"))
  assert.equal(error.ids.get("active-slice-content").textContent.includes("private/path"), false)
})

test("SSE revision during an in-flight GET hides stale acceptance until a coherent refetch", async () => {
  let resolveIntermediate
  let resolveLatest
  const intermediate = new Promise((resolve) => { resolveIntermediate = resolve })
  const latest = new Promise((resolve) => { resolveLatest = resolve })
  const initial = makeContext({ revision: 19, featureTitle: "Prior feature", outcome: "CURRENT" })
  const oldResponse = makeContext({ revision: 20, featureTitle: "Intermediate feature", outcome: "CURRENT" })
  const current = makeContext({ revision: 21, featureTitle: "Latest feature", sliceTitle: "Latest active Slice", outcome: "READY" })
  const app = makeDashboard((_url, _options, requestNumber) => {
    if (requestNumber === 1) return Promise.resolve(makeResponse(initial))
    if (requestNumber === 2) return intermediate
    if (requestNumber === 3) return latest
    throw new Error("unexpected extra context request")
  })
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Slice accepted")
  const source = app.sources[0]
  source.emit("open")
  await waitFor(() => app.fetchCalls.length === 2)
  source.emit("revision", { data: JSON.stringify({ revision: 21 }) })
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.ok(criterionRows(app).every((row) => rowStatus(row) === "Updating"))

  resolveIntermediate(makeResponse(oldResponse))
  await waitFor(() => app.fetchCalls.length === 3)
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Intermediate feature"), false)
  assert.notEqual(app.ids.get("active-slice-status").textContent, "Slice accepted")

  resolveLatest(makeResponse(current))
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Ready for acceptance")
  const content = app.ids.get("active-slice-content").textContent
  assert.ok(content.includes("Latest feature"))
  assert.ok(content.includes("Latest active Slice"))
  assert.equal(content.includes("Prior feature"), false)
  assert.equal(content.includes("Intermediate feature"), false)
  assert.deepEqual(app.fetchCalls.map((call) => call.url), ["/api/context", "/api/context", "/api/context"])
})

test("refresh and dashboard navigation make read-only context requests", async () => {
  const app = await loadedDashboard(makeContext({ outcome: "CURRENT" }))
  app.ids.get("tab-technical").click()
  assert.equal(app.ids.get("technical-panel").hidden, false)
  app.ids.get("tab-features").click()
  assert.equal(app.ids.get("features-panel").hidden, false)
  app.filters[1].click()
  assert.equal(app.sources[0].url, "/api/events")
  app.sources[0].emit("revision", { data: JSON.stringify({ revision: 7 }) })
  await waitFor(() => app.fetchCalls.length === 2)
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Slice accepted")
  assert.ok(app.fetchCalls.every((call) => call.url === "/api/context" && (call.options.method === undefined || call.options.method === "GET")))
  assert.ok(app.fetchCalls.every((call) => call.options.headers.Accept === "application/json"))
})
