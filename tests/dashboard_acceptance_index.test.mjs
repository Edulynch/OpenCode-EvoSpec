import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import test from "node:test"
import { readFile } from "node:fs/promises"
import { runInNewContext } from "node:vm"

const dashboardSource = await readFile(new URL("../evospec/resources/dashboard/dashboard.js", import.meta.url), "utf8")
const dashboardHtml = await readFile(new URL("../evospec/resources/dashboard/index.html", import.meta.url), "utf8")

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
        historical_acceptance_status: "NOT_RECORDED",
        historical_acceptance: null,
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

const formattedProposalId = (seed) => `ps-${createHash("sha256").update(seed).digest("hex")}`

function makeProvisionalItem({
  proposalId = formattedProposalId("proposal-private"),
  featureId = "feature-1",
  repositoryId = "private-repository-id",
  title = "Future Slice",
  provenance = "AGENT_SUGGESTION",
  expectedResult = "The proposed result is visible",
  headRevision = 1,
} = {}) {
  return {
    proposal_id: proposalId,
    repository_id: repositoryId,
    feature_id: featureId,
    title,
    status: "PROVISIONAL",
    provenance,
    head: { revision_id: `${proposalId}-r${headRevision}`, revision: headRevision, digest: "a".repeat(64) },
    criteria: [{
      id: "c-1",
      expected_result: expectedResult,
      expected_evidence: "private-proposal-evidence",
    }],
  }
}

function makeProvisionalInventory(items = [], overrides = {}) {
  const total = overrides.total ?? items.length
  const truncated = overrides.truncated ?? false
  return {
    source: "CORE_PROVISIONAL_SLICE_HEADS",
    availability: "AVAILABLE",
    total,
    complete: !truncated,
    truncated,
    items,
    ...overrides,
  }
}

function makeDashboard(fetcher) {
  const ids = new Map([
    "load-status", "project-summary", "refresh-context", "current-work-section", "current-work-title",
    "current-work-status", "current-work-explanation", "active-slice-status",
    "active-slice-content", "blocked-section", "blocked-list", "features-list",
    "technical-content",
  ].map((id) => [id, new Element(id)]))
  ids.get("active-slice-status").textContent = "Loading"
  ids.get("active-slice-content").append(Object.assign(new Element("p"), {
    className: "empty-state",
    textContent: "Loading the active Slice and acceptance criteria.",
  }))
  const document = {
    getElementById: (id) => ids.get(id) ?? null,
    querySelectorAll: () => [],
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
  return { ids, fetchCalls, sources, windowListeners, intervals }
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

test("acceptance is the primary view and workflow/capability diagnostics are disclosed separately", () => {
  const primary = dashboardHtml.split(/<details id="technical-inspection"/, 1)[0]
  const diagnostics = dashboardHtml.match(/<details id="technical-inspection"[\s\S]*?<\/details>/)?.[0] ?? ""
  assert.match(primary, /id="active-slice-section"/)
  assert.doesNotMatch(primary, /current-work-section|blocked-section|features-list|Published features/)
  assert.match(diagnostics, /Technical inspection/)
  assert.match(diagnostics, /current-work-section/)
  assert.match(diagnostics, /blocked-section/)
  assert.match(diagnostics, /features-list/)
  assert.match(diagnostics, /do not establish Feature completion or Slice acceptance/i)
})

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
  const provisional = context.provisional_slice_inventory
  assert.equal(provisional?.source, "CORE_PROVISIONAL_SLICE_HEADS")
  assert.equal(provisional?.availability, "AVAILABLE")
  assert.ok(provisional.items.length > 0)
  assert.ok(content.includes("Provisional proposals"))
  assert.ok(content.includes(provisional.items[0].title))
  assert.ok(content.includes("Provisional"))
  assert.deepEqual(criterionRows(app).map(rowResult), accepted.criteria_revision.criteria.map((criterion) => criterion.expected_result))
  assert.equal(criterionRows(app).length, accepted.criteria_revision.criteria.length)
  assert.deepEqual(criterionRows(app).map(rowStatus), accepted.criteria_revision.criteria.map(() => "Pending verification"))
  for (const privateValue of [
    accepted.feature_agreement_ref,
    accepted.slice_agreement_ref,
    accepted.task_id,
    accepted.criteria_revision.id,
    ...accepted.criteria_revision.criteria.map((criterion) => criterion.id),
    ...accepted.criteria_revision.criteria.map((criterion) => criterion.expected_evidence),
    "ACTIVE_CONTEXT",
    "CANONICAL",
  ]) {
    assert.equal(content.includes(privateValue), false, `primary index leaked ${privateValue}`)
  }
  assert.equal(app.fetchCalls.length, 1)
  assert.equal(app.fetchCalls[0].url, "/api/context")
  assert.equal(app.fetchCalls[0].options.method, undefined)
  const technicalRevision = descendants(app.ids.get("technical-content")).find((element) => element.id === "technical-slice-agreements")
  assert.ok(technicalRevision?.textContent.includes(`criteria revision ${accepted.criteria_revision.id}`))
  assert.ok(technicalRevision?.textContent.includes("ACTIVE_CONTEXT"), "technical provenance remains inspectable")
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

test("a completed Task does not project Feature completion in capability diagnostics", async () => {
  const app = await loadedDashboard(makeContext({ outcome: "READY" }))
  const capability = descendants(app.ids.get("features-list")).find((element) => element.dataset.featureId === "feature-1")
  assert.ok(capability)
  assert.equal(descendants(capability).some((element) => element.className === "status-label"), false)
  assert.ok(capability.textContent.includes("Personal task list"))
})

test("only a CURRENT outcome with its matching canonical receipt says Slice accepted", async () => {
  const accepted = await loadedDashboard(makeContext({ outcome: "CURRENT" }))
  assert.equal(accepted.ids.get("active-slice-status").textContent, "Slice accepted")
  const primary = accepted.ids.get("active-slice-content").textContent
  const activeCard = descendants(accepted.ids.get("active-slice-content")).find((element) => element.className === "acceptance-slice")
  assert.ok(activeCard?.textContent.includes("Slice status"))
  assert.ok(activeCard?.textContent.includes("Agreed outcome: The agreed task outcome is visible"))
  assert.ok(activeCard?.textContent.includes("Outcome acceptance"))
  assert.ok(activeCard?.textContent.includes("Agreed acceptance criteria"))
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

test("legacy acceptance rejects a CURRENT receipt from another repository, Slice, or criteria revision", async () => {
  const mismatches = [
    ["repository_id", "another-repository"],
    ["slice_agreement_ref", "another-slice"],
    ["criteria_revision_id", "another-criteria-revision"],
  ]
  for (const [field, value] of mismatches) {
    const context = makeContext({ outcome: "CURRENT" })
    delete context.slice_acceptance_inventory
    context.slice_outcome.receipt[field] = value
    const app = await loadedDashboard(context)
    assert.equal(
      app.ids.get("active-slice-status").textContent,
      "Acceptance status unavailable",
      `${field} mismatch must not claim current acceptance`,
    )
    assert.equal(statusBadges(app).some((badge) => badge.textContent === "Slice accepted"), false)
  }
})

test("criterion states remain distinct and legacy VERIFIED freshness is not current", async () => {
  const specifications = [
    { status: "UNVERIFIED", label: "Pending verification" },
    { status: "FAILED", fresh_for_current_workspace: false, label: "Verification failed" },
    { status: "STALE", fresh_for_current_workspace: false, label: "Revalidation required" },
    { status: "REVALIDATION_REQUIRED", fresh_for_current_workspace: false, label: "Revalidation required" },
    { status: "VERIFIED", fresh_for_current_workspace: true, label: "Verified (current)" },
    { status: "VERIFIED", fresh_for_current_workspace: false, label: "Verified (currentness unavailable)" },
    { status: "VERIFIED", label: "Verified (currentness unavailable)" },
    { status: "FUTURE_STATE", fresh_for_current_workspace: true, label: "Verification unavailable" },
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

  for (const [outcome, label] of [["BLOCKED", "Acceptance blocked"], ["STALE", "Revalidation required"], ["REVALIDATION_REQUIRED", "Revalidation required"]]) {
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
        slice_agreement_ref: "slice-blocked",
        slice_title: "Blocked parked Slice",
        scope_status: "PARKED",
        operational_state: "BLOCKED",
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
  context.slice_acceptance_inventory.items.unshift(context.slice_acceptance_inventory.items.pop())
  context.slice_acceptance_inventory.total = context.slice_acceptance_inventory.items.length
  const app = await loadedDashboard(context)
  const groups = descendants(app.ids.get("active-slice-content")).filter((element) => element.className === "acceptance-feature-group")
  assert.equal(groups.length, 2)
  const featureTitle = (group) => descendants(group).find((element) => element.className === "acceptance-feature-title").textContent
  assert.deepEqual(groups.map(featureTitle), ["Different Feature", "Personal task list"])
  assert.ok(groups.every((group) => group.dataset.featureId === undefined), "primary Feature groups must not expose technical IDs")
  const featureOne = groups.find((group) => featureTitle(group) === "Personal task list")
  const featureTwo = groups.find((group) => featureTitle(group) === "Different Feature")
  assert.equal(descendants(featureOne).filter((element) => element.className === "acceptance-slice").length, 6)
  assert.equal(descendants(featureTwo).filter((element) => element.className === "acceptance-slice").length, 1)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
  const card = (title) => descendants(app.ids.get("active-slice-content")).find((element) =>
    element.className === "acceptance-slice" && element.textContent.includes(title)
  )
  const parked = card("Parked Slice")
  assert.ok(parked?.textContent.includes("Agreed outcome: parked approved outcome"))
  assert.ok(parked?.textContent.includes("Parked — current acceptance not evaluated"))
  assert.ok(parked?.textContent.includes("Historical acceptance evidence is unavailable"))
  assert.ok(parked?.textContent.includes("parked-only result"))
  assert.equal(parked?.textContent.includes("private parked evidence"), false)
  assert.ok(card("Blocked parked Slice")?.textContent.includes("Blocked (parked)"))
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

test("historical acceptance shows its exact past basis without promoting current validity", async () => {
  const attachHistory = (context, {
    receiptId = "receipt-prior",
    revisionId = "criteria-prior",
    receiptSource = "CORE_OBJECTIVE_ACCEPTANCE",
    criteriaSource = "INTERACTIVE_TERMINAL",
    criteriaRows,
    proofSourceRevision = 8,
    proofRecordedRevision = 9,
    outcomeSourceRevision = 10,
    outcomeRecordedRevision = 11,
  } = {}) => {
    const item = context.slice_acceptance_inventory.items[0]
    context.revision = Math.max(context.revision, outcomeRecordedRevision)
    const criterion = {
      id: "AC-prior",
      expected_result: "The previously accepted result",
      expected_evidence: "python-unittest:tests/test_prior.py",
    }
    const criteria = criteriaRows ?? [criterion]
    const proofs = criteria.map((row, index) => ({
      id: criteria.length === 1 ? "proof-prior" : `proof-prior-${index + 1}`,
      slice_agreement_ref: item.slice_agreement_ref,
      criteria_revision_id: revisionId,
      criterion_id: row.id,
      test_kind: "python-unittest",
      test_target: "tests/test_prior.py",
      exit_code: 0,
      output_sha256: "a".repeat(64),
      source_revision: proofSourceRevision,
      recorded_revision: proofRecordedRevision,
    }))
    item.historical_acceptance_status = "ACCEPTED_PREVIOUSLY"
    item.historical_acceptance = {
      receipt: {
        id: receiptId,
        repository_id: context.repository_id,
        slice_agreement_ref: item.slice_agreement_ref,
        criteria_revision_id: revisionId,
        evidence_receipt_ids: proofs.map((proof) => proof.id),
        input_fingerprint: "b".repeat(64),
        input_policy_id: "input-policy",
        input_policy_digest: "c".repeat(64),
        admission_policy_id: "admission-policy",
        admission_policy_digest: "d".repeat(64),
        relevant_drift_digest: "e".repeat(64),
        basis: "ALL_CRITERIA_FRESH_SUPPORTED_TESTS",
        source: receiptSource,
        source_revision: outcomeSourceRevision,
        recorded_revision: outcomeRecordedRevision,
        coverage_contract_digest: null,
      },
      criteria_revision: {
        id: revisionId,
        slice_agreement_ref: item.slice_agreement_ref,
        repository_id: context.repository_id,
        outcome: "Past agreed outcome",
        example: "Past example",
        inclusions: [],
        exclusions: [],
        decisions: [],
        criteria,
        source: criteriaSource,
        source_reference: "tty:prior",
        previous_revision_id: null,
        reason: null,
        decision_statement: null,
      },
      criterion_evidence: proofs,
    }
    return item
  }

  const amended = makeContext({ outcome: "READY" })
  const amendedItem = attachHistory(amended)
  const amendedRevision = {
    ...amendedItem.criteria_revision,
    id: "criteria-current-amended",
  }
  amendedItem.criteria_revision = amendedRevision
  amended.accepted_slice.criteria_revision = amendedRevision
  const amendedApp = await loadedDashboard(amended)
  const amendedCard = descendants(amendedApp.ids.get("active-slice-content")).find((element) =>
    element.className === "acceptance-slice" && element.textContent.includes("Restore saved tasks")
  )
  assert.ok(amendedCard?.textContent.includes("Accepted previously — current validity not evaluated"))
  assert.ok(amendedCard?.textContent.includes("The previously accepted result"))
  assert.equal(amendedApp.ids.get("active-slice-status").textContent, "Ready for acceptance")
  assert.notEqual(amendedApp.ids.get("active-slice-status").textContent, "Slice accepted")
  const details = descendants(amendedCard).find((element) => element.tagName === "details")
  assert.ok(details)
  assert.ok(details.textContent.includes("receipt-prior"))
  assert.ok(details.textContent.includes("proof-prior"))
  assert.ok(details.textContent.includes("tests/test_prior.py"))
  assert.ok(details.textContent.includes("a".repeat(64)))
  const textOutsideDetails = (element) => element.tagName === "details"
    ? ""
    : element._textContent + element.children.map(textOutsideDetails).join("")
  assert.equal(textOutsideDetails(amendedCard).includes("receipt-prior"), false)
  assert.equal(textOutsideDetails(amendedCard).includes("proof-prior"), false)

  const current = makeContext({ outcome: "CURRENT" })
  const currentItem = attachHistory(current, {
    receiptId: current.slice_outcome.latest_receipt_id,
    revisionId: current.accepted_slice.criteria_revision.id,
    receiptSource: "REQUEST_SCOPED_OBJECTIVE",
    criteriaSource: "REQUEST_SCOPED_OBJECTIVE",
    criteriaRows: current.slice_acceptance_inventory.items[0].criteria_revision.criteria,
    proofSourceRevision: 11,
    proofRecordedRevision: 12,
    outcomeSourceRevision: 11,
    outcomeRecordedRevision: 12,
  })
  currentItem.criteria_revision = currentItem.historical_acceptance.criteria_revision
  current.accepted_slice.criteria_revision = currentItem.criteria_revision
  const currentApp = await loadedDashboard(current)
  assert.equal(currentApp.ids.get("active-slice-status").textContent, "Slice accepted")
  assert.equal(currentApp.ids.get("active-slice-content").textContent.includes("Accepted previously"), false)

  const mismatchedRequest = makeContext({ outcome: "READY" })
  attachHistory(mismatchedRequest, {
    receiptSource: "REQUEST_SCOPED_OBJECTIVE",
    criteriaSource: "REQUEST_SCOPED_OBJECTIVE",
    proofSourceRevision: 8,
    proofRecordedRevision: 9,
    outcomeSourceRevision: 10,
    outcomeRecordedRevision: 11,
  })
  const mismatchedRequestApp = await loadedDashboard(mismatchedRequest)
  assert.equal(mismatchedRequestApp.ids.get("active-slice-content").textContent.includes("Accepted previously"), false)
  assert.ok(mismatchedRequestApp.ids.get("active-slice-content").textContent.includes("Historical acceptance evidence is unavailable"))

  const unsupportedSource = makeContext({ outcome: "READY" })
  attachHistory(unsupportedSource, { criteriaSource: "FUTURE_CRITERIA_SOURCE" })
  const unsupportedSourceApp = await loadedDashboard(unsupportedSource)
  assert.equal(unsupportedSourceApp.ids.get("active-slice-content").textContent.includes("Accepted previously"), false)
  assert.ok(unsupportedSourceApp.ids.get("active-slice-content").textContent.includes("Historical acceptance evidence is unavailable"))

  const inconsistent = makeContext({ outcome: "READY" })
  attachHistory(inconsistent).historical_acceptance.receipt.repository_id = "another-repository"
  const inconsistentApp = await loadedDashboard(inconsistent)
  assert.equal(inconsistentApp.ids.get("active-slice-content").textContent.includes("Accepted previously"), false)
  assert.ok(inconsistentApp.ids.get("active-slice-content").textContent.includes("Historical acceptance evidence is unavailable"))
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
  initial.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({ proposalId: formattedProposalId("prior-root-proposal"), title: "Prior-root proposal" }),
  ])
  current.repository_id = "repository-new"
  current.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({
      proposalId: formattedProposalId("new-root-proposal"),
      repositoryId: "repository-new",
      title: "New-root proposal",
    }),
  ])
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
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Prior-root proposal"), false)
  resolveStaleError(makeResponse({}, false))
  await waitFor(() => app.fetchCalls.length === 3)
  assert.equal(app.ids.get("active-slice-status").textContent, "Updating")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("could not be loaded"), false)
  resolveLatest(makeResponse(current))
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Ready for acceptance")
  const content = app.ids.get("active-slice-content").textContent
  assert.ok(content.includes("New root"))
  assert.ok(content.includes("New-root proposal"))
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

test("manual refresh and revision events make only read-only GET requests", async () => {
  const app = await loadedDashboard(makeContext({ outcome: "CURRENT" }))
  assert.equal(app.sources[0].url, "/api/events")
  app.ids.get("refresh-context").click()
  await waitFor(() => app.fetchCalls.length === 2)
  app.sources[0].emit("revision", { data: JSON.stringify({ revision: 7 }) })
  await waitFor(() => app.fetchCalls.length === 3)
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Slice accepted")
  assert.ok(app.fetchCalls.every((call) => call.url === "/api/context" && call.options.method === undefined))
  assert.ok(app.fetchCalls.every((call) => call.options.headers.Accept === "application/json"))
})

test("provisional heads are safe, distinct from agreements, and associated only by canonical identity", async () => {
  const context = makeContext({ outcome: "READY" })
  context.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({
      proposalId: formattedProposalId("proposal-agent-private"),
      title: "<img src=x onerror=alert(1)>",
      expectedResult: "<script>alert(1)</script>",
    }),
    makeProvisionalItem({
      proposalId: formattedProposalId("proposal-user-private"),
      title: "<img src=x onerror=alert(1)>",
      provenance: "USER_EXPRESSED",
    }),
    makeProvisionalItem({
      proposalId: formattedProposalId("proposal-unmatched-private"),
      featureId: "feature-without-agreement",
      title: "Unmatched proposal",
    }),
    makeProvisionalItem({
      proposalId: formattedProposalId("proposal-foreign-private"),
      repositoryId: "another-repository",
      title: "Foreign proposal",
    }),
  ])
  const app = await loadedDashboard(context)
  const content = app.ids.get("active-slice-content")
  const panel = descendants(content).find((element) => element.className.split(/\s+/).includes("provisional-slice-panel"))
  const proposals = descendants(content).filter((element) => element.className === "provisional-slice")
  const groups = descendants(content).filter((element) => element.className.split(/\s+/).includes("provisional-feature-group"))

  assert.ok(panel)
  assert.equal(proposals.length, 3, "duplicate titles remain separate and foreign-root proposals are omitted")
  assert.equal(groups.length, 2, "matched and unmatched proposal groups remain separate")
  assert.ok(groups.some((group) => group.textContent.includes("Personal task list")))
  assert.ok(groups.some((group) => group.textContent.includes("Unmatched Feature proposals")))
  assert.equal(proposals.filter((proposal) => proposal.textContent.includes("<img src=x onerror=alert(1)>")).length, 2)
  assert.ok(proposals[0].textContent.includes("<script>alert(1)</script>"))
  assert.equal(descendants(panel).some((element) => ["img", "script"].includes(element.tagName)), false)
  assert.equal(panel.textContent.includes("private-proposal-evidence"), false)
  assert.equal(panel.textContent.includes(context.provisional_slice_inventory.items[0].proposal_id), false)
  assert.equal(panel.textContent.includes(context.provisional_slice_inventory.items[0].head.revision_id), false)
  assert.equal(panel.textContent.includes("a".repeat(64)), false)
  assert.ok(panel.textContent.includes("Suggested by agent"))
  assert.ok(panel.textContent.includes("User-expressed — not approval"))
  assert.ok(descendants(panel).filter((element) => element.className === "status-label").every((status) => status.textContent === "Provisional"))
  assert.equal(descendants(content).filter((element) => element.className === "acceptance-slice").length, 1)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
})

test("invalid proposal IDs and mismatched head revision IDs are omitted without hiding agreements", async () => {
  const context = makeContext({ outcome: "READY" })
  const invalidProposalId = makeProvisionalItem({
    proposalId: "ps-not-a-canonical-id",
    title: "Invalid proposal ID",
  })
  const mismatchedHead = makeProvisionalItem({
    proposalId: formattedProposalId("mismatched-head"),
    headRevision: 2,
    title: "Mismatched head revision",
  })
  mismatchedHead.head.revision_id = `${mismatchedHead.proposal_id}-r3`
  const terminalLineTerminator = makeProvisionalItem({
    proposalId: `${formattedProposalId("terminal-line-terminator")}\n`,
    title: "Trailing LF proposal ID",
  })
  const currentHead = makeProvisionalItem({
    proposalId: formattedProposalId("canonical-current-head"),
    headRevision: 2,
    title: "Canonical current head",
  })
  context.provisional_slice_inventory = makeProvisionalInventory([
    invalidProposalId,
    mismatchedHead,
    terminalLineTerminator,
    currentHead,
  ])

  const app = await loadedDashboard(context)
  const content = app.ids.get("active-slice-content")
  const proposals = descendants(content).filter((element) => element.className === "provisional-slice")
  assert.deepEqual(proposals.map((proposal) => proposal.textContent).filter((text) => text.includes("Canonical current head")).length, 1)
  assert.equal(proposals.length, 1)
  assert.ok(content.textContent.includes("Some provisional proposals were omitted because they were foreign, malformed, or ambiguous"))
  assert.equal(content.textContent.includes("Invalid proposal ID"), false)
  assert.equal(content.textContent.includes("Mismatched head revision"), false)
  assert.equal(content.textContent.includes("Trailing LF proposal ID"), false)
  assert.equal(content.textContent.includes("Saved tasks reappear after reopening"), true)
  assert.equal(descendants(content).filter((element) => element.className === "acceptance-slice").length, 1)
  assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
})

test("ambiguous proposal IDs are omitted in every order without hiding accepted history", async () => {
  const attachHistory = (context) => {
    const item = context.slice_acceptance_inventory.items[0]
    const revisionId = "criteria-prior"
    const criterion = {
      id: "AC-prior",
      expected_result: "The previously accepted result",
      expected_evidence: "python-unittest:tests/test_prior.py",
    }
    context.revision = 11
    item.historical_acceptance_status = "ACCEPTED_PREVIOUSLY"
    item.historical_acceptance = {
      receipt: {
        id: "receipt-prior",
        repository_id: context.repository_id,
        slice_agreement_ref: item.slice_agreement_ref,
        criteria_revision_id: revisionId,
        evidence_receipt_ids: ["proof-prior"],
        input_fingerprint: "b".repeat(64),
        input_policy_id: "input-policy",
        input_policy_digest: "c".repeat(64),
        admission_policy_id: "admission-policy",
        admission_policy_digest: "d".repeat(64),
        relevant_drift_digest: "e".repeat(64),
        basis: "ALL_CRITERIA_FRESH_SUPPORTED_TESTS",
        source: "CORE_OBJECTIVE_ACCEPTANCE",
        source_revision: 10,
        recorded_revision: 11,
        coverage_contract_digest: null,
      },
      criteria_revision: {
        id: revisionId,
        slice_agreement_ref: item.slice_agreement_ref,
        repository_id: context.repository_id,
        outcome: "Past agreed outcome",
        example: "Past example",
        inclusions: [],
        exclusions: [],
        decisions: [],
        criteria: [criterion],
        source: "INTERACTIVE_TERMINAL",
        source_reference: "tty:prior",
        previous_revision_id: null,
        reason: null,
        decision_statement: null,
      },
      criterion_evidence: [{
        id: "proof-prior",
        slice_agreement_ref: item.slice_agreement_ref,
        criteria_revision_id: revisionId,
        criterion_id: criterion.id,
        test_kind: "python-unittest",
        test_target: "tests/test_prior.py",
        exit_code: 0,
        output_sha256: "a".repeat(64),
        source_revision: 8,
        recorded_revision: 9,
      }],
    }
  }
  const conflictId = formattedProposalId("conflicting-proposal")
  const conflictA = makeProvisionalItem({
    proposalId: conflictId,
    title: "Conflicted proposal",
    expectedResult: "First conflicting body",
  })
  const conflictB = makeProvisionalItem({
    proposalId: conflictId,
    title: "Different conflicting title",
    expectedResult: "Second conflicting body",
    headRevision: 2,
  })
  const malformedId = formattedProposalId("malformed-duplicate")
  const malformedPairFirst = makeProvisionalItem({ proposalId: malformedId, title: "Valid malformed-pair row" })
  const malformedPairSecond = {
    ...malformedPairFirst,
    title: "Malformed duplicate row",
    head: { ...malformedPairFirst.head, digest: "invalid" },
  }
  const exactDuplicate = makeProvisionalItem({
    proposalId: formattedProposalId("exact-duplicate"),
    title: "Exact duplicate",
  })
  const independent = makeProvisionalItem({
    proposalId: formattedProposalId("independent-same-title"),
    title: "Conflicted proposal",
  })

  for (const conflictingRows of [[conflictA, conflictB], [conflictB, conflictA]]) {
    const context = makeContext({ outcome: "READY" })
    attachHistory(context)
    context.provisional_slice_inventory = makeProvisionalInventory([
      ...conflictingRows,
      malformedPairFirst,
      malformedPairSecond,
      exactDuplicate,
      structuredClone(exactDuplicate),
      independent,
    ])
    const app = await loadedDashboard(context)
    const content = app.ids.get("active-slice-content")
    const proposals = descendants(content).filter((element) => element.className === "provisional-slice")
    const acceptedSlice = descendants(content).find((element) =>
      element.className === "acceptance-slice" && element.textContent.includes("Restore saved tasks")
    )

    assert.equal(proposals.length, 1)
    assert.ok(proposals[0].textContent.includes("Conflicted proposal"))
    assert.ok(content.textContent.includes("ambiguous"))
    assert.ok(acceptedSlice?.textContent.includes("Accepted previously — current validity not evaluated"))
    assert.ok(acceptedSlice?.textContent.includes("The previously accepted result"))
    assert.equal(descendants(content).filter((element) => element.className === "acceptance-slice").length, 1)
    assert.equal(app.ids.get("active-slice-status").textContent, "Ready for acceptance")
  }
})

test("provisional availability, legacy, truncation, and malformed entries remain explicit", async () => {
  const legacy = makeContext()
  delete legacy.provisional_slice_inventory
  assert.ok((await loadedDashboard(legacy)).ids.get("active-slice-content").textContent.includes("older context schema"))

  const empty = makeContext()
  empty.provisional_slice_inventory = makeProvisionalInventory()
  assert.ok((await loadedDashboard(empty)).ids.get("active-slice-content").textContent.includes("No provisional proposals"))

  for (const [availability, message] of [
    ["UNINITIALIZED", "not initialized"],
    ["UNAVAILABLE", "unavailable"],
  ]) {
    const context = makeContext()
    context.provisional_slice_inventory = {
      source: "CORE_PROVISIONAL_SLICE_HEADS",
      availability,
      total: 0,
      complete: false,
      truncated: false,
      items: [],
    }
    assert.ok((await loadedDashboard(context)).ids.get("active-slice-content").textContent.toLowerCase().includes(message))
  }

  const partial = makeContext()
  const valid = makeProvisionalItem({ proposalId: formattedProposalId("valid-partial") })
  const malformed = makeProvisionalItem({ proposalId: formattedProposalId("malformed-partial") })
  malformed.head.digest = "not-a-digest"
  partial.provisional_slice_inventory = makeProvisionalInventory([valid, malformed])
  const partialApp = await loadedDashboard(partial)
  assert.equal(descendants(partialApp.ids.get("active-slice-content")).filter((element) => element.className === "provisional-slice").length, 1)
  assert.ok(partialApp.ids.get("active-slice-content").textContent.includes("Some provisional proposals were omitted because they were foreign, malformed, or ambiguous"))
  assert.ok(partialApp.ids.get("active-slice-content").textContent.includes("Ready for acceptance"))

  const truncated = makeContext()
  const items = Array.from({ length: 32 }, (_, index) => makeProvisionalItem({
    proposalId: formattedProposalId(`proposal-${index}`),
    title: `Proposal ${index}`,
  }))
  truncated.provisional_slice_inventory = makeProvisionalInventory(items, {
    total: 33,
    complete: false,
    truncated: true,
  })
  const truncatedApp = await loadedDashboard(truncated)
  assert.equal(descendants(truncatedApp.ids.get("active-slice-content")).filter((element) => element.className === "provisional-slice").length, 32)
  assert.ok(truncatedApp.ids.get("active-slice-content").textContent.includes("truncated"))

  const malformedEnvelope = makeContext()
  malformedEnvelope.provisional_slice_inventory = makeProvisionalInventory([valid], { source: "UNKNOWN" })
  const malformedApp = await loadedDashboard(malformedEnvelope)
  assert.equal(descendants(malformedApp.ids.get("active-slice-content")).some((element) => element.className === "provisional-slice"), false)
  assert.ok(malformedApp.ids.get("active-slice-content").textContent.includes("not available in a recognized Core projection"))
})

test("SSE revision drops stale provisional heads before refetch, and request errors do not retain them", async () => {
  let resolveStale
  let resolveLatest
  let rejectRefresh
  const stale = new Promise((resolve) => { resolveStale = resolve })
  const latest = new Promise((resolve) => { resolveLatest = resolve })
  const failedRefresh = new Promise((_resolve, reject) => { rejectRefresh = reject })
  const initial = makeContext({ revision: 19 })
  initial.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({ proposalId: formattedProposalId("old-proposal"), title: "Old proposal" }),
  ])
  const staleContext = makeContext({ revision: 20 })
  staleContext.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({ proposalId: formattedProposalId("stale-proposal"), title: "Stale proposal" }),
  ])
  const current = makeContext({ revision: 21 })
  current.provisional_slice_inventory = makeProvisionalInventory([
    makeProvisionalItem({ proposalId: formattedProposalId("current-proposal"), title: "Current proposal" }),
  ])
  const app = makeDashboard((_url, _options, requestNumber) => {
    if (requestNumber === 1) return Promise.resolve(makeResponse(initial))
    if (requestNumber === 2) return stale
    if (requestNumber === 3) return latest
    if (requestNumber === 4) return failedRefresh
    throw new Error("unexpected extra context request")
  })
  await waitFor(() => app.ids.get("active-slice-content").textContent.includes("Old proposal"))
  const source = app.sources[0]
  source.emit("open")
  await waitFor(() => app.fetchCalls.length === 2)
  source.emit("revision", { data: JSON.stringify({ revision: 21 }) })
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Old proposal"), false)
  assert.equal(app.ids.get("active-slice-content").textContent.includes("stale-proposal"), false)

  resolveStale(makeResponse(staleContext))
  await waitFor(() => app.fetchCalls.length === 3)
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Stale proposal"), false)
  resolveLatest(makeResponse(current))
  await waitFor(() => app.ids.get("active-slice-content").textContent.includes("Current proposal"))

  app.ids.get("refresh-context").click()
  await waitFor(() => app.fetchCalls.length === 4)
  rejectRefresh(new Error("context unavailable"))
  await waitFor(() => app.ids.get("active-slice-status").textContent === "Status unavailable")
  assert.equal(app.ids.get("active-slice-content").textContent.includes("Current proposal"), false)
})
