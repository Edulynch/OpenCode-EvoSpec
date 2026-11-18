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
  const defaultCriteriaRevision = { id: revisionId, outcome: "The agreed task outcome is visible", criteria }
  const acceptedSlice = {
    feature_agreement_ref: "feature-ref-private",
    slice_agreement_ref: "slice-ref-private",
    feature_title: featureTitle,
    slice_title: sliceTitle,
    task_id: "TASK-private",
    criteria_status: "RECORDED",
    criteria_revision: criteriaRevision === undefined ? defaultCriteriaRevision : criteriaRevision,
  }
  const projectedCriteriaRevision = criteriaRevision === undefined ? defaultCriteriaRevision : criteriaRevision
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
      receipt: {
        id: receiptId,
        repository_id: "private-repository-id",
        slice_agreement_ref: acceptedSlice.slice_agreement_ref,
        criteria_revision_id: revisionId,
        input_fingerprint: "private-hash",
        evidence_receipt_ids: ["private-evidence"],
      },
    }
    : { status: outcome, latest_receipt_id: null, receipt: null, blockers: ["private-technical-blocker"] }
  const context = {
    revision,
    repository_id: "private-repository-id",
    accepted_slice: acceptedSlice,
    slice_acceptance_inventory: {
      source: "CORE_AGREEMENTS",
      total: 1,
      complete: true,
      truncated: false,
      history_scope: "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE",
      items: [{
        slice_agreement_ref: acceptedSlice.slice_agreement_ref,
        feature_id: "feature-1",
        feature_agreement_ref: acceptedSlice.feature_agreement_ref,
        feature_title: acceptedSlice.feature_title,
        slice_title: acceptedSlice.slice_title,
        authority_status: "CANONICAL",
        scope_status: "ACTIVE",
        provenance: "ACTIVE_CONTEXT",
        operational_state: null,
        criteria_status: projectedCriteriaRevision === null ? "NOT_RECORDED" : "RECORDED",
        criteria_revision: projectedCriteriaRevision,
        historical_receipt: false,
      }],
    },
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

function statusBadges(app) {
  return descendants(app.ids.get("active-slice-content")).filter((element) => element.className === "status-label")
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
  assert.ok(content.includes(`Agreed outcome: ${accepted.criteria_revision.outcome}`))
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
  const technicalRevision = descendants(app.ids.get("technical-content")).find((element) => element.id === "technical-slice-agreements")
  assert.ok(technicalRevision?.textContent.includes(`criteria revision ${accepted.criteria_revision.id}`))
})

test("READY and completed Tasks never become Slice acceptance", async () => {
  const context = makeContext({ outcome: "READY" })
  const app = await loadedDashboard(context)
  assert.equal(context.work_queue.complete, true)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
  assert.notEqual(app.ids.get("active-slice-status").textContent, "Slice accepted")
  assert.ok(app.ids.get("active-slice-content").textContent.includes("not yet accepted"))

  const noSlice = await loadedDashboard({
    ...context,
    accepted_slice: null,
    slice_acceptance_inventory: { ...context.slice_acceptance_inventory, total: 0, items: [] },
    slice_outcome: makeContext({ outcome: "CURRENT" }).slice_outcome,
  })
  assert.equal(noSlice.ids.get("active-slice-status").textContent, "No active Slice")
  assert.ok(noSlice.ids.get("active-slice-content").textContent.includes("No active accepted Slice."))
})

test("only a CURRENT outcome with its matching canonical receipt says Slice accepted", async () => {
  const accepted = await loadedDashboard(makeContext({ outcome: "CURRENT" }))
  assert.equal(accepted.ids.get("active-slice-status").textContent, "Slice accepted")
  const primary = accepted.ids.get("active-slice-content").textContent
  const activeCard = descendants(accepted.ids.get("active-slice-content")).find((element) => element.className === "acceptance-slice")
  assert.ok(activeCard?.textContent.includes("Agreed scope"))
  assert.ok(activeCard?.textContent.includes("Agreed outcome: The agreed task outcome is visible"))
  assert.ok(activeCard?.textContent.includes("Outcome acceptance"))
  assert.ok(activeCard?.textContent.includes("Criterion verification"))
  assert.ok(activeCard?.textContent.includes("Slice accepted"))
  for (const privateValue of ["private-hash", "private-evidence", "private-receipt", "TASK-private", "AC-1", "private-test-target", "feature-ref-private", "slice-ref-private"]) {
    assert.equal(primary.includes(privateValue), false, `primary index leaked ${privateValue}`)
  }

  const mismatch = makeContext({ outcome: "CURRENT" })
  mismatch.slice_outcome.receipt.id = "different-receipt"
  const unavailable = await loadedDashboard(mismatch)
  assert.equal(unavailable.ids.get("active-slice-status").textContent, "Acceptance status unavailable")
  assert.equal(statusBadges(unavailable).some((badge) => badge.dataset.status === "accepted"), false)

  const missingReceipt = makeContext({ outcome: "CURRENT" })
  missingReceipt.slice_outcome.receipt = null
  assert.equal((await loadedDashboard(missingReceipt)).ids.get("active-slice-status").textContent, "Acceptance status unavailable")

  const wrongCriteriaReceipt = makeContext({ outcome: "CURRENT" })
  wrongCriteriaReceipt.slice_outcome.receipt.criteria_revision_id = "another-slice-revision"
  assert.equal((await loadedDashboard(wrongCriteriaReceipt)).ids.get("active-slice-status").textContent, "Acceptance status unavailable")
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
  assert.equal((await loadedDashboard(emptyCriteria)).ids.get("active-slice-status").textContent, "Status unavailable")

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

test("groups accepted Slices by canonical Feature identity and never evaluates inactive criteria", async () => {
  const context = makeContext({ outcome: "READY" })
  const active = context.slice_acceptance_inventory.items[0]
  const makeItem = (overrides) => ({
    ...active,
    slice_agreement_ref: "slice-extra",
    feature_title: "Same Feature, renamed scope title",
    slice_title: "Extra Slice",
    scope_status: "INACTIVE",
    provenance: "AGREEMENT_ONLY",
    criteria_status: "RECORDED",
    criteria_revision: {
      id: "criteria-extra",
      outcome: "extra approved outcome",
      criteria: [{ id: "AC-extra", expected_result: "extra-only result", expected_evidence: "private extra evidence" }],
    },
    historical_receipt: false,
    ...overrides,
  })
  context.slice_acceptance_inventory = {
    ...context.slice_acceptance_inventory,
    total: 6,
    items: [
      active,
      makeItem({
        slice_agreement_ref: "slice-parked",
        slice_title: "Parked Slice",
        scope_status: "PARKED",
        provenance: "PARKED_CONTEXT",
        criteria_revision: {
          id: "criteria-parked",
          outcome: "parked approved outcome",
          criteria: [{ id: "AC-parked", expected_result: "parked-only result", expected_evidence: "private parked evidence" }],
        },
        historical_receipt: true,
      }),
      makeItem({
        slice_agreement_ref: "slice-inactive",
        slice_title: "Agreement-only Slice",
        scope_status: "INACTIVE",
        provenance: "AGREEMENT_ONLY",
        criteria_status: "NOT_RECORDED",
        criteria_revision: null,
      }),
      makeItem({
        slice_agreement_ref: "slice-preserved",
        slice_title: "Preserved Slice",
        scope_status: "PRESERVED",
        provenance: "PRESERVED_WORK_ITEM",
      }),
      makeItem({
        slice_agreement_ref: "slice-historical",
        slice_title: "Historical Slice",
        scope_status: "HISTORICAL",
        provenance: "TERMINAL_ARCHIVE",
      }),
      makeItem({
        slice_agreement_ref: "slice-other-feature",
        feature_id: "feature-2",
        feature_agreement_ref: "feature-ref-2",
        feature_title: "Different Feature",
        slice_title: "Queued Slice",
        scope_status: "QUEUED",
        provenance: "WORK_QUEUE",
        operational_state: "READY",
        criteria_revision: {
          id: "criteria-queued",
          outcome: "queued approved outcome",
          criteria: [{ id: "AC-queued", expected_result: "queued-only result", expected_evidence: "private queued evidence" }],
        },
      }),
    ],
  }
  const app = await loadedDashboard(context)
  const groups = descendants(app.ids.get("active-slice-content")).filter((element) => element.className === "acceptance-feature-group")
  assert.equal(groups.length, 2)
  assert.deepEqual(groups.map((group) => group.dataset.featureId), ["feature-1", "feature-2"])
  assert.equal(descendants(groups[0]).filter((element) => element.className === "acceptance-slice").length, 5)
  assert.equal(descendants(groups[1]).filter((element) => element.className === "acceptance-slice").length, 1)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
  const card = (title) => descendants(app.ids.get("active-slice-content")).find((element) =>
    element.className === "acceptance-slice" && element.textContent.includes(title)
  )
  const parked = card("Parked Slice")
  assert.ok(parked?.textContent.includes("Agreed outcome: parked approved outcome"))
  assert.ok(parked?.textContent.includes("Parked — current acceptance not evaluated"))
  assert.ok(parked?.textContent.includes("historical outcome receipt"))
  assert.ok(parked?.textContent.includes("parked-only result"))
  assert.equal(parked?.textContent.includes("private parked evidence"), false)
  const inactive = card("Agreement-only Slice")
  assert.ok(inactive?.textContent.includes("Current acceptance not evaluated"))
  assert.ok(inactive?.textContent.includes("Acceptance criteria are not recorded"))
  assert.ok(card("Preserved Slice")?.textContent.includes("Current acceptance not evaluated"))
  assert.ok(card("Historical Slice")?.textContent.includes("Current acceptance not evaluated"))
  const queued = card("Queued Slice")
  assert.ok(queued?.textContent.includes("Agreed outcome: queued approved outcome"))
  assert.ok(queued?.textContent.includes("Current acceptance not evaluated"))
  assert.ok(queued?.textContent.includes("queued-only result"))
  assert.ok(app.ids.get("active-slice-content").textContent.includes("Archived/history coverage is not exhaustive"))
})

test("inventory truncation, malformed associations, and unknown states fail closed visibly", async () => {
  const truncated = makeContext()
  truncated.slice_acceptance_inventory.total = 40
  truncated.slice_acceptance_inventory.complete = false
  truncated.slice_acceptance_inventory.truncated = true
  const truncatedApp = await loadedDashboard(truncated)
  assert.ok(truncatedApp.ids.get("active-slice-content").textContent.includes("truncated"))
  assert.ok(truncatedApp.ids.get("active-slice-status").textContent === "Pending" || truncatedApp.ids.get("active-slice-status").textContent === "Ready for acceptance")

  const malformed = makeContext()
  malformed.slice_acceptance_inventory.items[0].authority_status = "UNAVAILABLE"
  assert.equal((await loadedDashboard(malformed)).ids.get("active-slice-status").textContent, "Status unavailable")

  const unknown = makeContext()
  unknown.slice_acceptance_inventory.items[0].scope_status = "FUTURE_SCOPE"
  assert.equal((await loadedDashboard(unknown)).ids.get("active-slice-status").textContent, "Status unavailable")

  const invalidShape = makeContext()
  invalidShape.slice_acceptance_inventory.history_scope = "UNKNOWN"
  assert.equal((await loadedDashboard(invalidShape)).ids.get("active-slice-status").textContent, "Status unavailable")
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

test("repository identity changes discard stale responses and stale errors before rendering", async () => {
  let resolveStaleError
  let resolveLatest
  const staleError = new Promise((resolve) => { resolveStaleError = resolve })
  const latest = new Promise((resolve) => { resolveLatest = resolve })
  const initial = makeContext({ revision: 19, featureTitle: "Prior root", outcome: "CURRENT" })
  const current = makeContext({ revision: 2, featureTitle: "New root", sliceTitle: "New-root Slice", outcome: "READY" })
  current.repository_id = "repository-new"
  const app = makeDashboard((_url, _options, requestNumber) => {
    if (requestNumber === 1) return Promise.resolve(makeResponse(initial))
    if (requestNumber === 2) return staleError
    if (requestNumber === 3) return latest
    throw new Error("unexpected extra context request")
  })
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Slice accepted")
  const source = app.sources[0]
  source.emit("open")
  await waitFor(() => app.fetchCalls.length === 2)
  source.emit("revision", { data: JSON.stringify({ repository_id: "repository-new", revision: 2 }) })
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  resolveStaleError(makeResponse({}, false))
  await waitFor(() => app.fetchCalls.length === 3)
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("could not be loaded"), false)
  resolveLatest(makeResponse(current))
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Ready for acceptance")
  const content = app.ids.get("active-slice-content").textContent
  assert.ok(content.includes("New root"))
  assert.equal(content.includes("Prior root"), false)
})

test("explicit null repository identity invalidates lower-revision acceptance until the null-root context arrives", async () => {
  let resolveObsoleteSuccess
  let rejectObsoleteError
  let resolveNullContext
  const obsoleteSuccess = new Promise((resolve) => { resolveObsoleteSuccess = resolve })
  const obsoleteError = new Promise((_resolve, reject) => { rejectObsoleteError = reject })
  const nullContext = new Promise((resolve) => { resolveNullContext = resolve })
  const initial = makeContext({
    revision: 19,
    featureTitle: "Prior root",
    outcome: "CURRENT",
    verifications: [
      { status: "VERIFIED", fresh_for_current_workspace: true },
      { status: "VERIFIED", fresh_for_current_workspace: true },
    ],
  })
  const obsolete = makeContext({ revision: 19, featureTitle: "Obsolete response", outcome: "CURRENT" })
  const next = makeContext({
    revision: 3,
    outcome: null,
    contextOverrides: {
      repository_id: null,
      accepted_slice: null,
      slice_acceptance_inventory: {
        ...makeContext().slice_acceptance_inventory,
        total: 0,
        items: [],
      },
    },
  })
  const app = makeDashboard((_url, _options, requestNumber) => {
    if (requestNumber === 1) return Promise.resolve(makeResponse(initial))
    if (requestNumber === 2) return obsoleteSuccess
    if (requestNumber === 3) return obsoleteError
    if (requestNumber === 4) return nullContext
    throw new Error("unexpected extra context request")
  })
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Slice accepted")
  assert.ok(statusBadges(app).some((badge) => badge.textContent === "Verified (current)"))
  const source = app.sources[0]
  source.emit("open")
  await waitFor(() => app.fetchCalls.length === 2)
  source.emit("revision", { data: JSON.stringify({ repository_id: null, revision: 2 }) })
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.ok(statusBadges(app).every((badge) => badge.textContent === "Updating"))
  assert.equal(statusBadges(app).some((badge) => badge.textContent === "Slice accepted" || badge.textContent === "Verified (current)"), false)

  resolveObsoleteSuccess(makeResponse(obsolete))
  await waitFor(() => app.fetchCalls.length === 3)
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Obsolete response"), false)
  source.emit("revision", { data: JSON.stringify({ repository_id: null, revision: 3 }) })
  rejectObsoleteError(new Error("obsolete old-root fetch error"))
  await waitFor(() => app.fetchCalls.length === 4)
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("could not be loaded"), false)
  assert.equal(statusBadges(app).some((badge) => badge.textContent === "Slice accepted" || badge.textContent === "Verified (current)"), false)

  resolveNullContext(makeResponse(next))
  await waitFor(() => app.ids.get("active-slice-status").textContent === "No active Slice")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Prior root"), false)
  assert.equal(statusBadges(app).some((badge) => badge.textContent === "Slice accepted" || badge.textContent === "Verified (current)"), false)
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
