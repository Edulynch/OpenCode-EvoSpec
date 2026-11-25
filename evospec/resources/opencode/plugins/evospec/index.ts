// EvoSpec-managed: opencode-v2

import { lstat, open, realpath } from "node:fs/promises"
import { createHash } from "node:crypto"
import { dirname, isAbsolute, relative, resolve } from "node:path"
import {
  abortableDelay,
  deriveContinuationDirective,
  retryDirective,
} from "./continuation.mjs"

const AGENT_COMMAND = __EVOSPEC_AGENT_COMMAND__
const CLI_COMMAND = __EVOSPEC_CLI_COMMAND__
const canonicalSnapshots = new Map()
const explicitR3BPermissionHooks = new WeakSet()
// Ephemeral observation only: no prompts, paths, message content, or permission
// decisions are persisted, and no observation confers Core authority.
const autoHostObservations = new WeakMap()
const R3B_HUMAN_PERMISSION_ACTIONS = new Set([
  "evospec_criterion_test_r3b",
  "evospec_accept_slice_outcome",
])

const string = { type: "string" }
const nullableString = { type: ["string", "null"] }
const boolean = { type: "boolean" }
const nonnegativeInteger = { type: "integer", minimum: 0 }
const stringArray = { type: "array", items: string }
const sha256 = { type: "string", pattern: "^[0-9a-f]{64}$" }
const PYTHON_TEST_RUNNER =
  "import sys,unittest; root,module=sys.argv[1:3]; sys.path.insert(0,root); unittest.main(module=module,argv=[sys.argv[0]])"
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

async function authorizeAcceptedTaskProgress(runtime, request, context) {
  if (typeof runtime?.permission?.assert !== "function") {
    throw new Error("OpenCode V2 permission API is unavailable")
  }
  const requester = {
    sessionID: context?.sessionID,
    agent: context?.agent,
    messageID: context?.messageID,
    id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("OpenCode V2 did not provide complete accepted Task permission context")
  }
  if (typeof runtime?.session?.get !== "function") {
    throw new Error("OpenCode V2 session API is unavailable")
  }
  if (![request?.repository_id, request?.task_id].every((value) => typeof value === "string" && value.trim())) {
    throw new Error("Accepted Task permission requires repository and Task identities")
  }

  const root = await sessionRoot(runtime, context)
  if (typeof root !== "string" || !isAbsolute(root)) {
    throw new Error("OpenCode V2 did not provide an absolute executing repository root")
  }
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action: "evospec_accepted_task_progress",
    resources: [JSON.stringify({
      operation: request.action,
      repository_root: resolve(root),
      repository_id: request.repository_id,
      task_id: request.task_id,
    })],
  })

  if (context.sessionID !== requester.sessionID || context.agent !== requester.agent ||
      context.messageID !== requester.messageID || context.id !== requester.id) {
    throw new Error("OpenCode V2 permission requester changed while accepted Task permission was pending")
  }
  const currentRoot = await sessionRoot(runtime, context)
  if (typeof currentRoot !== "string" || !isAbsolute(currentRoot) || resolve(currentRoot) !== resolve(root)) {
    throw new Error("OpenCode V2 executing repository changed while accepted Task permission was pending")
  }
  return root
}

function criterionCommand(kind, target) {
  // Only a human-accepted, bounded test module or test file may become argv.
  // No shell or free-form command text ever enters this verifier.
  if (kind === "python-unittest" && /^tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(target)) {
    return [AGENT_COMMAND[0], "-m", "unittest", "-q", target]
  }
  if (kind === "node-test" && /^tests\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.test\.mjs$/.test(target)) {
    return ["node", "--test", target]
  }
  throw new Error("R3 test descriptor is not an approved deterministic test target")
}

async function executeCriterionTest(runtime, args, context) {
  if (typeof runtime?.permission?.assert !== "function" || typeof runtime?.session?.get !== "function") {
    throw new Error("R3 host permission/session API is unavailable")
  }
  const requester = {
    sessionID: context?.sessionID, agent: context?.agent,
    messageID: context?.messageID, id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("R3 host permission requester is incomplete")
  }
  const root = await sessionRoot(runtime, context)
  if (typeof root !== "string" || !isAbsolute(root)) throw new Error("R3 executing root is invalid")
  const command = criterionCommand(args.test_kind, args.test_target)
  const inspect = async () => {
    const snapshot = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context, root))
    const scope = snapshot.accepted_slice
    const criterion = scope?.criteria_revision?.criteria?.find((item) => item.id === args.criterion_id)
    if (snapshot.revision !== args.expected_revision || snapshot.repository_id !== args.repository_id ||
        snapshot.r3_schema_ready !== true || scope?.slice_agreement_ref !== args.slice_agreement_ref ||
        scope.criteria_revision?.id !== args.criteria_revision_id ||
        criterion?.expected_evidence !== `${args.test_kind}:${args.test_target}`) {
      throw new Error("R3 test request is stale or differs from current human-accepted criterion authority")
    }
    return snapshot
  }
  await inspect()
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action: "evospec_criterion_test",
    resources: [JSON.stringify({ repository_root: resolve(root), repository_id: args.repository_id,
      slice_agreement_ref: args.slice_agreement_ref, criteria_revision_id: args.criteria_revision_id,
      criterion_id: args.criterion_id, command })],
  })
  if (Object.entries(requester).some(([key, value]) => context?.[key] !== value) ||
      resolve(await sessionRoot(runtime, context)) !== resolve(root)) {
    throw new Error("R3 host permission session/root changed before the test")
  }
  await inspect()
  const process = await spawnCommand(command, root, context)
  if (!Number.isInteger(process.exitStatus) || process.exitStatus < 0 || process.exitStatus > 255) {
    throw new Error("R3 deterministic test did not return a supported process exit code")
  }
  const outputDigest = createHash("sha256").update(`${process.output}\n${process.error}`).digest("hex")
  const request = {
    action: "criterion_test_result", ...args,
    exit_code: process.exitStatus, output_sha256: outputDigest,
  }
  const recorded = await executeRequest(runtime, request, context, { retryStale: false, authorizedRoot: root })
  return JSON.stringify({
    test: { command, exit_code: process.exitStatus, stdout: process.output,
      stderr: process.error, output_sha256: outputDigest },
    recording: JSON.parse(recorded),
  })
}

function canonicalR3BPolicy(snapshot) {
  const policy = snapshot?.r3b_policy
  if (!policy || typeof policy !== "object" ||
      policy.coverage_contract_version !== "r3-known-input-contract-v1" ||
      typeof policy.input_version !== "string" || typeof policy.input_policy_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(policy.input_policy_digest || "") ||
      typeof policy.admission_policy_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(policy.admission_policy_digest || "") ||
      typeof policy.environment_profile_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(policy.environment_profile_policy_digest || "") ||
      typeof policy.coverage_contract !== "string" || !policy.coverage_contract) {
    throw new Error("Core did not provide the fixed schema-17 admission policy")
  }
  return policy
}

function canonicalR3BSnapshot(snapshot, repositoryID) {
  const value = snapshot?.input_snapshot
  if (!value || typeof value !== "object" ||
      typeof value.fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(value.fingerprint) ||
      typeof value.repository_id !== "string" || value.repository_id !== repositoryID ||
      typeof value.version !== "string" || typeof value.policy_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(value.policy_digest || "") ||
      !Number.isSafeInteger(value.file_count) || value.file_count < 0 ||
      !Number.isSafeInteger(value.directory_count) || value.directory_count < 0 ||
      !Number.isSafeInteger(value.total_bytes) || value.total_bytes < 0) {
    throw new Error("Core did not provide a complete schema-17 input snapshot")
  }
  const policy = canonicalR3BPolicy(snapshot)
  if (value.version !== policy.input_version || value.policy_id !== policy.input_policy_id ||
      value.policy_digest !== policy.input_policy_digest) {
    throw new Error("Core input snapshot differs from the approved schema-17 policy")
  }
  return value
}

function approvedR3BCoverage(snapshot, args, inputSnapshot) {
  const policy = canonicalR3BPolicy(snapshot)
  const matches = snapshot.r3b_coverage_approvals?.filter((approval) =>
    approval?.repository_id === args.repository_id &&
    approval?.slice_agreement_ref === args.slice_agreement_ref &&
    approval?.criteria_revision_id === args.criteria_revision_id &&
    approval?.criterion_id === args.criterion_id &&
    approval?.test_kind === args.test_kind &&
    approval?.test_target === args.test_target &&
    approval?.root_fingerprint === inputSnapshot.fingerprint &&
    approval?.input_policy_id === policy.input_policy_id &&
    approval?.input_policy_digest === policy.input_policy_digest &&
    approval?.admission_policy_id === policy.admission_policy_id &&
    approval?.admission_policy_digest === policy.admission_policy_digest &&
    approval?.environment_profile_id === policy.environment_profile_id &&
    approval?.environment_profile_policy_digest === policy.environment_profile_policy_digest
  ) || []
  if (matches.length !== 1) {
    throw new Error("R3-B criterion has no unique current human-approved known-input contract")
  }
  const approval = matches[0]
  if (!/^coverage-[0-9a-f]{20}$/.test(approval.id || "") ||
      !/^[0-9a-f]{64}$/.test(approval.contract_digest || "") ||
      approval.source !== "INTERACTIVE_TERMINAL" ||
      !/^tty:[0-9a-f]{12}$/.test(approval.source_reference || "") ||
      !Number.isSafeInteger(approval.source_revision) ||
      !Number.isSafeInteger(approval.recorded_revision) ||
      !Array.isArray(approval.known_inputs) ||
      approval.known_inputs.some((item) =>
        !item || !["root-file", "root-directory"].includes(item.kind) ||
        typeof item.path !== "string" || !item.path ||
        item.path.includes("\\") || item.path.includes(":") ||
        item.path.startsWith("/") ||
        item.path.split("/").some((part) => !part || part === "." || part === "..") ||
        [".git", ".evospec"].includes(item.path.split("/", 1)[0].toLowerCase())
      ) ||
      !approval.statement.includes(`repository: ${approval.repository_id};`) ||
      !approval.statement.includes(`Slice: ${approval.slice_agreement_ref};`) ||
      !approval.statement.includes(`criterios: ${approval.criteria_revision_id};`) ||
      !approval.statement.includes(`root-input-fingerprint: ${approval.root_fingerprint};`)) {
    throw new Error("R3-B approved known-input contract is malformed or unsupported")
  }
  return approval
}

function coverageApprovalIdentity(approval) {
  return JSON.stringify([
    approval.id, approval.contract_digest, approval.repository_id,
    approval.slice_agreement_ref, approval.criteria_revision_id,
    approval.criterion_id, approval.test_kind, approval.test_target,
    approval.known_inputs.map((item) => [item.kind, item.path]),
    approval.root_fingerprint, approval.input_policy_id,
    approval.input_policy_digest, approval.admission_policy_id,
    approval.admission_policy_digest, approval.environment_profile_id,
    approval.environment_profile_policy_digest, approval.statement,
    approval.source_reference, approval.source, approval.source_revision,
    approval.recorded_revision,
  ])
}

function currentR3BCoverageSet(snapshot, accepted) {
  const criteria = accepted?.criteria_revision?.criteria
  if (!Array.isArray(criteria) || !criteria.length) {
    throw new Error("R3-B current criteria have no approved known-input contracts")
  }
  return criteria.map((criterion) => {
    const [test_kind, test_target] = criterion.expected_evidence.split(":", 2)
    return approvedR3BCoverage(snapshot, {
      repository_id: snapshot.repository_id,
      slice_agreement_ref: accepted.slice_agreement_ref,
      criteria_revision_id: accepted.criteria_revision.id,
      criterion_id: criterion.id,
      test_kind,
      test_target,
    }, canonicalR3BSnapshot(snapshot, snapshot.repository_id))
  }).sort((left, right) => left.criterion_id.localeCompare(right.criterion_id))
}

function coverageSetIdentity(approvals) {
  return JSON.stringify(approvals.map(coverageApprovalIdentity))
}

function acceptedR3BScope(snapshot, sliceAgreementRef) {
  // The terminal archive and open Feature cycle are Core-issued read-only
  // authority. A caller-supplied Slice ID by itself can never enable a test.
  if (snapshot.accepted_slice?.slice_agreement_ref === sliceAgreementRef) {
    return snapshot.accepted_slice
  }
  const cycle = snapshot.feature_completion
  const matches = snapshot.slice_acceptance_inventory?.items?.filter((item) =>
    item?.slice_agreement_ref === sliceAgreementRef &&
    item?.feature_id === cycle?.feature_id &&
    item?.scope_status === "HISTORICAL" &&
    item?.provenance === "TERMINAL_ARCHIVE" &&
    item?.authority_status === "CANONICAL" &&
    item?.criteria_status === "RECORDED" &&
    item?.criteria_revision?.id)
  if (cycle?.cycle_state !== "OPEN" || !Number.isSafeInteger(cycle.cycle_id) ||
      cycle.cycle_id < 1 || !Array.isArray(matches) || matches.length !== 1) {
    throw new Error("R3-B historical evidence requires a human-reopened, terminal accepted Slice")
  }
  return matches[0]
}

function acceptedR3BCriterion(snapshot, args) {
  if (snapshot.revision !== args.expected_revision || snapshot.repository_id !== args.repository_id ||
      snapshot.r3b_schema_ready !== true) {
    throw new Error("R3-B test request is stale or schema 17 is not enabled")
  }
  const scope = acceptedR3BScope(snapshot, args.slice_agreement_ref)
  const criterion = scope?.criteria_revision?.criteria?.find((item) => item.id === args.criterion_id)
  if (scope?.slice_agreement_ref !== args.slice_agreement_ref ||
      scope.criteria_revision?.id !== args.criteria_revision_id ||
      criterion?.expected_evidence !== `${args.test_kind}:${args.test_target}`) {
    throw new Error("R3-B test differs from the current human-accepted criterion authority")
  }
  return canonicalR3BSnapshot(snapshot, args.repository_id)
}

function r3bEnvironment(root, executable) {
  const environment = {
    PATH: dirname(executable),
    PYTHONHASHSEED: "0",
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
    TEMP: root,
    TMP: root,
    TMPDIR: root,
    TZ: "UTC",
  }
  if (process.platform === "win32") {
    for (const name of ["SystemRoot", "WINDIR"]) {
      const value = process.env[name]
      if (typeof value === "string" && value) environment[name] = value
    }
  }
  const sorted = Object.fromEntries(Object.entries(environment).sort(([left], [right]) =>
    left < right ? -1 : left > right ? 1 : 0))
  const environmentProfileDigest = createHash("sha256")
    .update(JSON.stringify({ environment: sorted, profile_id: "evospec-sanitized-test-env-v1" }))
    .digest("hex")
  return { environment: sorted, environmentProfileDigest }
}

async function runtimeDigest(executable) {
  const before = await lstat(executable, { bigint: true })
  if (before.isSymbolicLink() || !before.isFile() || before.nlink !== 1n ||
      before.size < 0n || before.size > 1_073_741_824n) {
    throw new Error("R3-B runtime executable is not a bounded regular file")
  }
  const handle = await open(executable, "r")
  try {
    const opened = await handle.stat({ bigint: true })
    if (!opened.isFile() || opened.nlink !== 1n || opened.dev !== before.dev ||
        opened.ino !== before.ino || opened.size !== before.size) {
      throw new Error("R3-B runtime executable changed during verification")
    }
    const digest = createHash("sha256")
    const buffer = Buffer.allocUnsafe(1024 * 1024)
    let readBytes = 0n
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null)
      if (bytesRead === 0) break
      readBytes += BigInt(bytesRead)
      if (readBytes > 1_073_741_824n) throw new Error("R3-B runtime executable exceeds the fixed limit")
      digest.update(buffer.subarray(0, bytesRead))
    }
    const after = await handle.stat({ bigint: true })
    const current = await lstat(executable, { bigint: true })
    if (readBytes !== after.size || after.dev !== opened.dev || after.ino !== opened.ino ||
        after.size !== opened.size || after.mtimeNs !== opened.mtimeNs || after.ctimeNs !== opened.ctimeNs ||
        current.dev !== after.dev || current.ino !== after.ino || current.size !== after.size ||
        current.mtimeNs !== after.mtimeNs || current.ctimeNs !== after.ctimeNs) {
      throw new Error("R3-B runtime executable changed during verification")
    }
    return digest.digest("hex")
  } finally {
    await handle.close()
  }
}

async function runtimeExecutable(kind) {
  let candidate
  if (kind === "python-unittest") {
    if (AGENT_COMMAND.length < 3 || AGENT_COMMAND[1] !== "-m" ||
        AGENT_COMMAND[2] !== "evospec.agent_protocol") {
      throw new Error("R3-B Python tests require the exact source Python agent runtime")
    }
    candidate = AGENT_COMMAND[0]
  } else {
    candidate = Bun.which("node")
  }
  if (typeof candidate !== "string" || !candidate) {
    throw new Error("R3-B test runtime is unavailable")
  }
  if (!isAbsolute(candidate)) candidate = Bun.which(candidate)
  if (typeof candidate !== "string" || !isAbsolute(candidate)) {
    throw new Error("R3-B test runtime must resolve to an absolute executable")
  }
  const executable = await realpath(candidate)
  if (executable !== candidate) candidate = executable
  return candidate
}

async function inspectR3B(runtime, root, context, expectedRevision) {
  const response = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context, root))
  if (response.revision !== expectedRevision || response.r3b_schema_ready !== true) {
    throw new Error("R3-B authority changed or schema 17 is unavailable")
  }
  return response
}

async function executeCriterionTestR3B(runtime, args, context) {
  // A plain permission.assert can silently allow custom actions under V2's
  // default rules. Require an installed ask hook before any test can run.
  if (!explicitR3BPermissionHooks.has(runtime)) {
    throw new Error("R3-B explicit human permission guard is unavailable")
  }
  if (typeof runtime?.permission?.assert !== "function" || typeof runtime?.session?.get !== "function") {
    throw new Error("R3-B host permission/session API is unavailable")
  }
  const requester = {
    sessionID: context?.sessionID, agent: context?.agent,
    messageID: context?.messageID, id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("R3-B permission requester is incomplete")
  }
  const sessionPath = await sessionRoot(runtime, context)
  if (typeof sessionPath !== "string" || !isAbsolute(sessionPath)) {
    throw new Error("R3-B executing repository root is invalid")
  }
  const root = await realpath(sessionPath)
  const commandExecutable = await runtimeExecutable(args.test_kind)
  const executableDigest = await runtimeDigest(commandExecutable)
  const command = args.test_kind === "python-unittest"
    ? [commandExecutable, "-I", "-B", "-c", PYTHON_TEST_RUNNER, root, args.test_target]
    : [commandExecutable, "--test", args.test_target]
  if (!(args.test_kind === "python-unittest" &&
        /^tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(args.test_target)) &&
      !(args.test_kind === "node-test" &&
        /^tests\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.test\.mjs$/.test(args.test_target))) {
    throw new Error("R3-B test descriptor is not an approved deterministic test target")
  }
  let before = await inspectR3B(runtime, root, context, args.expected_revision)
  let inputSnapshot = acceptedR3BCriterion(before, args)
  const authorizedFingerprint = inputSnapshot.fingerprint
  let coverage = approvedR3BCoverage(before, args, inputSnapshot)
  const policy = canonicalR3BPolicy(before)
  const profile = r3bEnvironment(root, commandExecutable)
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action: "evospec_criterion_test_r3b",
    resources: [JSON.stringify({
      operation: "run_criterion_test_r3b",
      repository_root: root,
      repository_id: args.repository_id,
      slice_agreement_ref: args.slice_agreement_ref,
      criteria_revision_id: args.criteria_revision_id,
      criterion_id: args.criterion_id,
      test_kind: args.test_kind,
      test_target: args.test_target,
      command,
      runtime_executable: commandExecutable,
      runtime_executable_sha256: executableDigest,
      runtime_version_argv: [commandExecutable, "--version"],
      input_fingerprint: inputSnapshot.fingerprint,
      coverage_approval: coverage,
      input_policy_id: policy.input_policy_id,
      input_policy_digest: policy.input_policy_digest,
      admission_policy_id: policy.admission_policy_id,
      admission_policy_digest: policy.admission_policy_digest,
      environment_profile_id: policy.environment_profile_id,
      environment_profile_policy_digest: policy.environment_profile_policy_digest,
      environment_profile_digest: profile.environmentProfileDigest,
    }), root],
    metadata: {
      repository_root: root,
      criterion_id: args.criterion_id,
      test_target: args.test_target,
      decision: "Inspect the canonical repository root and approve this test only once",
    },
  })
  if (Object.entries(requester).some(([key, value]) => context?.[key] !== value) ||
      resolve(await realpath(await sessionRoot(runtime, context))) !== resolve(root)) {
    throw new Error("R3-B permission session/root changed before the test")
  }
  before = await inspectR3B(runtime, root, context, args.expected_revision)
  inputSnapshot = acceptedR3BCriterion(before, args)
  if (inputSnapshot.fingerprint !== authorizedFingerprint ||
      inputSnapshot.fingerprint !== canonicalR3BSnapshot(before, args.repository_id).fingerprint) {
    throw new Error("R3-B input snapshot changed while permission was pending")
  }
  const permissionCoverage = approvedR3BCoverage(before, args, inputSnapshot)
  if (coverageApprovalIdentity(permissionCoverage) !== coverageApprovalIdentity(coverage)) {
    throw new Error("R3-B known-input contract changed while permission was pending")
  }
  coverage = permissionCoverage
  const versionProcess = await spawnCommand(
    [commandExecutable, "--version"], root, context, undefined, profile.environment,
  )
  const runtimeVersion = `${versionProcess.output}${versionProcess.error}`.trim()
  const versionPattern = args.test_kind === "python-unittest"
    ? /^Python \d+\.\d+\.\d+(?:[-+].*)?$/
    : /^v\d+\.\d+\.\d+(?:[-+].*)?$/
  if (versionProcess.exitStatus !== 0 || !versionPattern.test(runtimeVersion)) {
    throw new Error("R3-B test runtime version is unsupported")
  }
  const afterVersion = await inspectR3B(runtime, root, context, args.expected_revision)
  const versionSnapshot = canonicalR3BSnapshot(afterVersion, args.repository_id)
  if (versionSnapshot.fingerprint !== authorizedFingerprint) {
    throw new Error("R3-B workspace changed while verifying the runtime")
  }
  const versionCoverage = approvedR3BCoverage(afterVersion, args, versionSnapshot)
  if (coverageApprovalIdentity(versionCoverage) !== coverageApprovalIdentity(coverage)) {
    throw new Error("R3-B known-input contract changed while verifying the runtime")
  }
  const testProcess = await spawnCommand(command, root, context, undefined, profile.environment)
  if (!Number.isInteger(testProcess.exitStatus) || testProcess.exitStatus < 0 || testProcess.exitStatus > 255) {
    throw new Error("R3-B deterministic test did not return a supported process exit code")
  }
  const outputDigest = createHash("sha256")
    .update(`${testProcess.output}\n${testProcess.error}`).digest("hex")
  const assertOriginalRequesterAndRoot = async () => {
    if (Object.entries(requester).some(([key, value]) => context?.[key] !== value)) {
      throw new Error("R3-B post-test requester changed; no result was submitted")
    }
    const currentSessionPath = await sessionRoot(runtime, context)
    if (typeof currentSessionPath !== "string" ||
        resolve(await realpath(currentSessionPath)) !== resolve(root)) {
      throw new Error("R3-B post-test session root changed; no result was submitted")
    }
  }
  await assertOriginalRequesterAndRoot()
  const afterTest = await inspectR3B(runtime, root, context, args.expected_revision)
  const afterSnapshot = canonicalR3BSnapshot(afterTest, args.repository_id)
  if (afterSnapshot.fingerprint !== authorizedFingerprint) {
    throw new Error("R3-B workspace changed during the test")
  }
  const afterCoverage = approvedR3BCoverage(afterTest, args, afterSnapshot)
  if (coverageApprovalIdentity(afterCoverage) !== coverageApprovalIdentity(coverage)) {
    throw new Error("R3-B known-input contract changed during the test")
  }
  const request = {
    action: "criterion_test_result_r3b",
    ...args,
    exit_code: testProcess.exitStatus,
    output_sha256: outputDigest,
    input_fingerprint_before: inputSnapshot.fingerprint,
    input_fingerprint_after: afterSnapshot.fingerprint,
    input_policy_id: policy.input_policy_id,
    input_policy_digest: policy.input_policy_digest,
    admission_policy_id: policy.admission_policy_id,
    admission_policy_digest: policy.admission_policy_digest,
    runtime_executable: commandExecutable,
    runtime_executable_sha256: executableDigest,
    runtime_version: runtimeVersion,
    runtime_argv: command.slice(1),
    environment_profile_id: policy.environment_profile_id,
    environment_profile_digest: profile.environmentProfileDigest,
    coverage_approval_id: coverage.id,
    coverage_contract_digest: coverage.contract_digest,
  }
  await assertOriginalRequesterAndRoot()
  const recording = await executeRequest(runtime, request, context, {
    retryStale: false, authorizedRoot: root,
  })
  return JSON.stringify({
    test: { command, runtime_version: runtimeVersion, exit_code: testProcess.exitStatus,
      stdout: testProcess.output, stderr: testProcess.error, output_sha256: outputDigest },
    recording: JSON.parse(recording),
  })
}

async function executeAcceptSliceOutcome(runtime, args, context) {
  if (!explicitR3BPermissionHooks.has(runtime)) {
    throw new Error("R3-B explicit human permission guard is unavailable")
  }
  if (typeof runtime?.permission?.assert !== "function" || typeof runtime?.session?.get !== "function") {
    throw new Error("Slice outcome acceptance requires the host permission/session API")
  }
  const requester = {
    sessionID: context?.sessionID, agent: context?.agent,
    messageID: context?.messageID, id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("Slice outcome permission requester is incomplete")
  }
  const sessionPath = await sessionRoot(runtime, context)
  if (typeof sessionPath !== "string" || !isAbsolute(sessionPath)) {
    throw new Error("Slice outcome repository root is invalid")
  }
  const root = await realpath(sessionPath)
  let before = await inspectR3B(runtime, root, context, args.expected_revision)
  const policy = canonicalR3BPolicy(before)
  const inputSnapshot = canonicalR3BSnapshot(before, before.repository_id)
  const accepted = args.slice_agreement_ref
    ? acceptedR3BScope(before, args.slice_agreement_ref)
    : before.accepted_slice
  const criteriaRevision = accepted?.criteria_revision?.id
  if (!accepted?.slice_agreement_ref || !criteriaRevision) {
    throw new Error("Core has no current accepted Slice criteria head")
  }
  if (!args.slice_agreement_ref && !["READY", "CURRENT"].includes(before.slice_outcome?.status)) {
    throw new Error(`Slice outcome is not eligible: ${before.slice_outcome?.status || "UNKNOWN"}`)
  }
  const coverage = currentR3BCoverageSet(before, accepted)
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action: "evospec_accept_slice_outcome",
    resources: [JSON.stringify({
      operation: "accept_slice_outcome",
      repository_root: root,
      repository_id: before.repository_id,
      slice_agreement_ref: accepted.slice_agreement_ref,
      criteria_revision_id: criteriaRevision,
      expected_revision: args.expected_revision,
      input_fingerprint: inputSnapshot.fingerprint,
      input_policy_id: policy.input_policy_id,
       input_policy_digest: policy.input_policy_digest,
       admission_policy_id: policy.admission_policy_id,
       admission_policy_digest: policy.admission_policy_digest,
       coverage_approvals: coverage,
       environment_profile_id: policy.environment_profile_id,
      environment_profile_policy_digest: policy.environment_profile_policy_digest,
    }), root],
    metadata: {
      repository_root: root,
      slice_agreement_ref: accepted.slice_agreement_ref,
      decision: "Inspect the canonical repository root and approve Slice outcome only once",
    },
  })
  if (Object.entries(requester).some(([key, value]) => context?.[key] !== value) ||
      resolve(await realpath(await sessionRoot(runtime, context))) !== resolve(root)) {
    throw new Error("Slice outcome permission session/root changed before acceptance")
  }
  before = await inspectR3B(runtime, root, context, args.expected_revision)
  const afterSnapshot = canonicalR3BSnapshot(before, before.repository_id)
  const afterPolicy = canonicalR3BPolicy(before)
  const afterAccepted = args.slice_agreement_ref
    ? acceptedR3BScope(before, args.slice_agreement_ref)
    : before.accepted_slice
  if (afterAccepted?.slice_agreement_ref !== accepted.slice_agreement_ref ||
      afterAccepted?.criteria_revision?.id !== criteriaRevision ||
      afterSnapshot.fingerprint !== inputSnapshot.fingerprint ||
      JSON.stringify(afterPolicy) !== JSON.stringify(policy)) {
    throw new Error("Slice outcome scope, policy, or inputs changed while permission was pending")
  }
  const afterCoverage = currentR3BCoverageSet(before, afterAccepted)
  if (coverageSetIdentity(afterCoverage) !== coverageSetIdentity(coverage)) {
    throw new Error("Slice outcome known-input contracts changed while permission was pending")
  }
  return executeRequest(runtime, {
    action: args.slice_agreement_ref ? "accept_historical_slice_outcome" : "accept_slice_outcome",
    expected_revision: args.expected_revision,
    input_snapshot: inputSnapshot,
    ...(args.slice_agreement_ref ? { slice_agreement_ref: args.slice_agreement_ref } : {}),
  }, context, { retryStale: false, authorizedRoot: root })
}

async function spawnCommand(command, root, context, input, env) {
  const options = {
    cwd: root,
    signal: context.signal,
    windowsHide: globalThis.process.platform === "win32",
    stdout: "pipe",
    stderr: "pipe",
  }
  if (input !== undefined) options.stdin = new Blob([new TextEncoder().encode(input)])
  if (env !== undefined) options.env = env
  const process = Bun.spawn(command, options)
  return capture(process)
}

async function rawExecuteRequest(runtime, request, context, rootOverride) {
  const root = rootOverride ?? await sessionRoot(runtime, context)
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
  const root = options.authorizedRoot ?? await sessionRoot(runtime, context)
  const baseline = decodedRequest?.expected_revision === undefined
    ? null
    : canonicalSnapshots.get(resolve(root))
  const output = await rawExecuteRequest(runtime, request, context, options.authorizedRoot)
  if (options.retryStale === false) return output
  if (decodedRequest === null || !SAFE_STALE_REPLAY_ACTIONS.has(decodedRequest.action)) return output
  let response
  try { response = JSON.parse(output) } catch { return output }
  if (response?.error?.code !== "STALE_REVISION") return output

  const refreshedText = await rawExecuteRequest(runtime, { action: "context" }, context, options.authorizedRoot)
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
  return rawExecuteRequest(runtime, retry, context, options.authorizedRoot)
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

// This proposal does not create work; the human's interactive TTY
// confirmation is the only accepted first-Slice authority.
function firstSliceHandoffEligible(c) {
  const q = c?.work_queue, inventory = c?.slice_acceptance_inventory
  // Core exposes a null E1 projection before the very first agreement.
  // The interactive CLI revalidates actual persisted-schema writer eligibility.
  return c?.initialization_state === "INITIALIZED" && c.e1_schema_ready !== false
    && c.lifecycle === null && c.feature_id == null && c.change_id == null
    && c.feature_completion == null && !c.completed_terminal
    && !c.pending_intake && !c.side_work_active && !c.stale
    && c.legal_actions?.includes("new")
    && q?.total === 0 && q.complete === true
    && inventory?.total === 0 && inventory.complete === true
}

function boundedAgreementText(value, field) {
  if (typeof value !== "string" || !value.trim() || value.length > 2048
      || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error("first-Slice " + field + " must be nonblank single-line text (max 2048)")
  }
  return value.trim()
}

function quoteShell(value) {
  const escaped = globalThis.process.platform === "win32"
    ? value.replace(/'/g, "''") : value.replace(/'/g, "'\"'\"'")
  return "'" + escaped + "'"
}

async function executeFirstSliceHandoff(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  const c = parseContextResponse(await rawExecuteRequest(runtime, { action: "context" }, context))
  if (c.revision !== args.expected_revision) {
    throw new Error("STALE_REVISION: reread context before proposing first Slice")
  }
  if (!firstSliceHandoffEligible(c)) {
    throw new Error("FIRST_SLICE_UNAVAILABLE: the current projection cannot offer an empty-slot first-Slice handoff")
  }
  const agreement = {
    feature: boundedAgreementText(args.feature, "Feature"),
    slice: boundedAgreementText(args.slice, "Slice"),
    task: boundedAgreementText(args.task, "Task"),
    checkpoint: boundedAgreementText(args.checkpoint, "checkpoint"),
  }
  const argv = [
    ...CLI_COMMAND, "--project", root, "accept-slice",
    "--feature", agreement.feature, "--slice", agreement.slice,
    "--task", agreement.task, "--checkpoint", agreement.checkpoint,
    "--expected-revision", String(c.revision),
  ]
  return JSON.stringify({
    code: "INTERACTIVE_FIRST_SLICE_APPROVAL_REQUIRED",
    state: "READ_ONLY_PROPOSAL",
    writer_schema_verified: false,
    repository_root: root,
    repository_id: c.repository_id,
    revision: c.revision,
    proposed_agreement: agreement,
    terminal_command: (globalThis.process.platform === "win32" ? "& " : "")
      + argv.map(quoteShell).join(" "),
    instruction: "Show the agreement and command once. This is a candidate only: Core checks the actual writer schema, empty ledger and repository identity when the human runs the command in an interactive terminal. They must inspect Core's exact agreement and type ACEPTO there. Chat responses or OpenCode permission dialogs do not substitute for TTY acceptance. Then reread evospec_context; proceed only if the actual accepted Slice exists.",
    limitations: "No criteria-file attached; Task progress, tests, Slice outcome, Feature completion, edit permissions and Git remain separate.",
  })
}

// This is a session-message provenance route, NOT an authenticated human
// signature. Programmatic session.prompt messages also have type="user".
function hasNoAttachments(item) {
  return item !== null && typeof item === "object"
    && (!Object.hasOwn(item, "files")
      || (Array.isArray(item.files) && item.files.length === 0))
}

function objectiveResumeMessage(item) {
  return validSessionRequest(item)
    && ["continue", "continúa"].includes(item.text.trim())
}

function validSessionRequest(item) {
  return item?.type === "user"
    && typeof item.id === "string"
    && /^msg_[A-Za-z0-9_-]{3,128}$/.test(item.id)
    && typeof item.text === "string"
    && item.text.length <= 2048
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(item.text)
    && item.text.trim().length > 0
    && hasNoAttachments(item)
}

async function currentSessionRequest(runtime, context) {
  if (typeof runtime?.session?.context !== "function"
      || typeof context.messageID !== "string" || !context.messageID.startsWith("msg_")) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: OpenCode session context or tool message identity is missing")
  }
  const messages = await runtime.session.context({ sessionID: context.sessionID })
  if (!Array.isArray(messages) || messages.length > 4096) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: admitted session transcript is unavailable or unbounded")
  }
  const at = messages.findIndex((item) =>
    item?.type === "assistant" && item.id === context.messageID)
  if (at < 1) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: the invoking assistant turn is not in the admitted session")
  }
  const prior = messages.slice(0, at)
  const lastUser = prior.findLastIndex((item) => item?.type === "user")
  if (lastUser < 0) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: no admitted user request precedes this tool call")
  }
  if (prior.slice(lastUser + 1).some((item) => item?.type === "synthetic")) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: a synthetic event separates the request from this tool")
  }
  const item = prior[lastUser]
  if (!validSessionRequest(item)) {
    // Preserve user formatting verbatim. A newline, indentation, or trailing
    // CRLF is normal prompt content, not a reason to demand a new prompt.
    throw new Error("SESSION_REQUEST_SCOPE_INVALID: request must be nonblank, within 2048 characters, without unsupported control characters or attachments")
  }
  return { message_id: item.id, request_text: item.text }
}

function validSessionMessageID(value) {
  return typeof value === "string" && /^msg_[A-Za-z0-9_-]{3,128}$/.test(value)
}

function validSessionModelReference(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && typeof value.id === "string" && value.id.length > 0
    && typeof value.providerID === "string" && value.providerID.length > 0
}

function validSessionAssistant(item) {
  return item?.type === "assistant"
    && validSessionMessageID(item.id)
    && typeof item.agent === "string" && item.agent.length > 0
    && validSessionModelReference(item.model)
    && Array.isArray(item.content)
    && item.time !== null && typeof item.time === "object" && !Array.isArray(item.time)
    && Object.hasOwn(item.time, "created")
}

// V2.3.2 typed session controls; their payloads never become request intent.
function validSessionControl(item) {
  if (item?.type === "idle") {
    return ["succeeded", "failed", "interrupted"].includes(item.outcome)
      && (item.error === undefined
        || (item.error !== null && typeof item.error === "object" && !Array.isArray(item.error)))
  }
  if (item?.type === "agent-switched") {
    return typeof item.agent === "string" && item.agent.length > 0
      && (item.previous === undefined
        || (typeof item.previous === "string" && item.previous.length > 0))
  }
  if (item?.type === "model-switched") {
    return validSessionModelReference(item.model)
      && (item.previous === undefined || validSessionModelReference(item.previous))
  }
  return false
}

async function requestScopedObjectiveSource(runtime, context, objective) {
  if (typeof runtime?.session?.context !== "function") {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: OpenCode session context is unavailable")
  }
  const sessionID = context?.sessionID
  const invokingMessageID = context?.messageID
  if (typeof sessionID !== "string" || !/^ses_[A-Za-z0-9_-]{3,128}$/.test(sessionID)
      || !validSessionMessageID(invokingMessageID)) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: exact executing session or assistant message identity is missing")
  }

  const reference = typeof objective?.source_reference === "string"
    ? /^session:(ses_[A-Za-z0-9_-]{3,128}):(msg_[A-Za-z0-9_-]{3,128}):([0-9a-f]{16})$/.exec(objective.source_reference)
    : null
  const expectedDigest = objective?.request_sha256
  if (!reference || reference[1] !== sessionID
      || typeof expectedDigest !== "string" || !/^[0-9a-f]{64}$/.test(expectedDigest)
      || reference[3] !== expectedDigest.slice(0, 16)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_SOURCE_MISMATCH: Core source reference is not canonical for this session and digest")
  }

  const messages = await runtime.session.context({ sessionID })
  if (!Array.isArray(messages) || messages.length > 4096) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: admitted session transcript is unavailable or unbounded")
  }
  const sourceID = reference[2]
  const sourceIndexes = []
  const invokingIndexes = []
  const seenIDs = new Set()
  for (let index = 0; index < messages.length; index++) {
    const item = messages[index]
    if (item === null || typeof item !== "object" || Array.isArray(item)
        || ![Object.prototype, null].includes(Object.getPrototypeOf(item))
        || !Object.hasOwn(item, "type") || !Object.hasOwn(item, "id")) {
      throw new Error("INPUT_PROVENANCE_UNAVAILABLE: malformed session record occurs in the admitted transcript")
    }
    const id = item.id
    if (!validSessionMessageID(id) || seenIDs.has(id)) {
      throw new Error("INPUT_PROVENANCE_UNAVAILABLE: malformed or duplicate message ID occurs in the admitted transcript")
    }
    seenIDs.add(id)
    if (id === sourceID) sourceIndexes.push(index)
    if (id === invokingMessageID) invokingIndexes.push(index)
  }
  if (sourceIndexes.length !== 1 || invokingIndexes.length !== 1) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: source and invoking assistant IDs must each identify one session record")
  }
  const sourceIndex = sourceIndexes[0]
  const invokingIndex = invokingIndexes[0]
  const original = messages[sourceIndex]
  if (sourceIndex >= invokingIndex || original?.type !== "user") {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: Core source is not a unique user record before this invocation")
  }
  if (!validSessionRequest(original)) {
    throw new Error("SESSION_REQUEST_SCOPE_INVALID: original request must be nonblank, within 2048 characters, without unsupported control characters or attachments")
  }
  const digest = createHash("sha256").update(original.text, "utf8").digest("hex")
  if (digest !== expectedDigest || digest.slice(0, 16) !== reference[3]) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_SOURCE_MISMATCH: original request bytes do not match Core's full digest")
  }

  const invoking = messages[invokingIndex]
  if (!validSessionAssistant(invoking)) {
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: invoking message ID does not identify a valid assistant record")
  }
  for (let index = 0; index < messages.length; index++) {
    const item = messages[index]
    if (item.type === "synthetic") continue
    if (index === sourceIndex) continue
    if (item.type === "user") {
      if (typeof item.text !== "string"
          || (Object.hasOwn(item, "files") && !Array.isArray(item.files))) {
        throw new Error("INPUT_PROVENANCE_UNAVAILABLE: malformed user record occurs in the admitted transcript")
      }
      if (index > sourceIndex && !objectiveResumeMessage(item)) {
        throw new Error("INPUT_PROVENANCE_UNAVAILABLE: a later user message is substantive, malformed, or has attachments")
      }
      continue
    }
    if (item.type === "assistant") {
      if (index !== invokingIndex && !validSessionAssistant(item)) {
        throw new Error("INPUT_PROVENANCE_UNAVAILABLE: malformed assistant event occurs in the admitted transcript")
      }
      continue
    }
    if (validSessionControl(item)) continue
    throw new Error("INPUT_PROVENANCE_UNAVAILABLE: unknown, malformed, or unsupported event occurs in the admitted transcript")
  }
  return { message_id: original.id, request_text: original.text, request_sha256: digest }
}

async function executeSessionFirstSlice(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  const current = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root)
  )
  if (current.revision !== args.expected_revision) {
    throw new Error("STALE_REVISION: reread canonical context before new session intake")
  }
  if (!firstSliceHandoffEligible(current) || typeof current.repository_id !== "string") {
    throw new Error("SESSION_REQUEST_FIRST_SLICE_UNAVAILABLE: an initialized empty E1 slot is required")
  }
  const original = await currentSessionRequest(runtime, context)
  if (typeof runtime?.permission?.assert !== "function"
      || !["sessionID", "agent", "messageID", "id"].every((key) =>
        typeof context[key] === "string" && context[key].trim())) {
    throw new Error("HOST_PERMISSION_UNAVAILABLE: full executing tool identity and permission API required")
  }
  // Let OpenCode resolve this precise operation under this session's own
  // policy (Auto or prompt); Core independently restricts the only new file.
  await runtime.permission.assert({
    sessionID: context.sessionID,
    agent: context.agent,
    source: { type: "tool", messageID: context.messageID, id: context.id },
    action: "evospec_session_intake",
    resources: [root],
  })
  // Permission checks may await human input: re-read root, actual message,
  // and Core revision after the await. Never reuse stale session authority.
  if (resolve(await sessionRoot(runtime, context)) !== resolve(root)) {
    throw new Error("SESSION_REQUEST_ROOT_CHANGED: project moved after host admission")
  }
  const fresh = await currentSessionRequest(runtime, context)
  if (fresh.message_id !== original.message_id || fresh.request_text !== original.request_text) {
    throw new Error("SESSION_REQUEST_CHANGED: the session request changed before recording")
  }
  const latest = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root)
  )
  if (latest.revision !== current.revision || !firstSliceHandoffEligible(latest)
      || latest.repository_id !== current.repository_id) {
    throw new Error("STALE_REVISION: Core authority changed during host permission admission")
  }
  return rawExecuteRequest(runtime, {
    action: "accept_first_slice_session",
    expected_revision: current.revision,
    repository_id: current.repository_id,
    session_id: context.sessionID,
    message_id: original.message_id,
    request_text: original.request_text,
  }, context, root)
}

function requestScopedObjectiveBinding(snapshot, expectedRevision) {
  if (snapshot.revision !== expectedRevision) {
    throw new Error("STALE_REVISION: reread canonical context before objective verification")
  }
  const objective = snapshot.request_scoped_objective
  if (objective === null || objective === undefined) {
    return { unavailable: "REQUEST_SCOPED_OBJECTIVE_UNAVAILABLE", snapshot }
  }
  if (!objective || typeof objective !== "object" || Array.isArray(objective)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_CONTEXT_INVALID: objective projection is malformed")
  }
  if (objective.status === "BLOCKED") {
    return { unavailable: "REQUEST_SCOPED_OBJECTIVE_BLOCKED", snapshot }
  }
  if (objective.status === "COMPLETE") {
    return { complete: true, snapshot }
  }
  if (!["PENDING", "VERIFIED"].includes(objective.status)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_CONTEXT_INVALID: objective status is unsupported")
  }
  const criteria = objective.criteria
  const semanticProjectionValid = objective.kind === undefined
    ? criteria === undefined
    : objective.kind === "PERSONAL_TASKS_WEB" && objective.heading === null
      && Array.isArray(criteria) && [2, 3].includes(criteria.length)
      && criteria.every((item) => item && typeof item === "object" && !Array.isArray(item)
        && Object.keys(item).length === 2
        && typeof item.operation === "string"
        && ["EXPLICIT_REQUEST", "DERIVED_OBJECTIVE"].includes(item.provenance))
      && JSON.stringify(criteria.map((item) => item.operation)) === JSON.stringify(
        criteria.length === 2 ? ["create", "consult"] : ["create", "consult", "complete"],
      )
      && criteria.every((item) =>
        item.operation !== "complete" || item.provenance === "EXPLICIT_REQUEST")
  const fingerprint = snapshot.input_snapshot?.fingerprint
  if (typeof snapshot.repository_id !== "string" || !snapshot.repository_id.trim()
      || typeof fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(fingerprint)
      || typeof objective.source_reference !== "string" || !objective.source_reference
      || typeof objective.request_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(objective.request_sha256)
      || !Array.isArray(objective.blockers) || objective.blockers.some((item) => typeof item !== "string")
      || !(objective.heading === null || typeof objective.heading === "string")
      || !semanticProjectionValid
      || !Array.isArray(snapshot.lifecycle) || snapshot.lifecycle.length < 2
      || snapshot.lifecycle.some((item) => typeof item !== "string")) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_CONTEXT_INVALID: canonical identity, stage, or objective fields are incomplete")
  }
  return {
    fingerprint,
    objective,
    repositoryID: snapshot.repository_id,
    stage: [...snapshot.lifecycle],
  }
}

function requestScopedIncrementalBinding(snapshot, root) {
  const objective = snapshot?.request_scoped_objective
  const accepted = snapshot?.accepted_slice
  const request = accepted?.session_request
  const input = snapshot?.input_snapshot
  const envelope = snapshot?.autonomy_envelope
  const metrics = snapshot?.autonomy_metrics
  const reference = typeof objective?.source_reference === "string"
    ? /^session:(ses_[A-Za-z0-9_-]{3,128}):(msg_[A-Za-z0-9_-]{3,128}):([0-9a-f]{16})$/.exec(objective.source_reference)
    : null
  const digest = typeof request?.statement === "string"
    ? createHash("sha256").update(request.statement, "utf8").digest("hex")
    : null
  const rootRepositoryID = createHash("sha256").update(root, "utf8").digest("hex").slice(0, 16)
  const exactEnvelope = envelope && typeof envelope === "object" && !Array.isArray(envelope)
    && envelope.goal === accepted?.slice_title
    && JSON.stringify(envelope.allowed_paths) === JSON.stringify(["index.html"])
    && envelope.operation_budget === 8
    && JSON.stringify(envelope.reversible_actions) === JSON.stringify([
      "progress", "assess_locks", "review", "verify", "edit",
    ])
    && envelope.external_wait_allowed === true
    && envelope.retry_budget === 0
    && JSON.stringify(envelope.irreversible_permissions) === "[]"
    && JSON.stringify(envelope.stop_conditions) === JSON.stringify([
      "material decision required", "scope expansion proposed", "irreversible operation proposed",
    ])
  const sessionRequestValid = accepted?.source === "SESSION_REQUEST"
    && accepted.source_reference === objective?.source_reference
    && typeof accepted.feature_agreement_ref === "string" && accepted.feature_agreement_ref.trim()
    && typeof accepted.slice_agreement_ref === "string" && accepted.slice_agreement_ref.trim()
    && typeof accepted.task_id === "string" && accepted.task_id.trim()
    && typeof accepted.feature_title === "string" && accepted.feature_title.trim()
    && typeof accepted.slice_title === "string" && accepted.slice_title.trim()
    && accepted.criteria_status === "NOT_RECORDED"
    && accepted.criteria_revision === null
    && request && typeof request === "object" && !Array.isArray(request)
    && request.sha256 === digest
    && request.repository_id === rootRepositoryID
    && request.repository_id === snapshot?.repository_id
  const snapshotValid = input && typeof input === "object" && !Array.isArray(input)
    && input.version === "r3-root-input-v2"
    && input.policy_id === input.version
    && typeof input.policy_digest === "string" && /^[0-9a-f]{64}$/.test(input.policy_digest)
    && input.repository_id === rootRepositoryID
    && typeof input.fingerprint === "string" && /^[0-9a-f]{64}$/.test(input.fingerprint)
    && Number.isSafeInteger(input.file_count) && input.file_count >= 0
    && Number.isSafeInteger(input.directory_count) && input.directory_count >= 0
    && Number.isSafeInteger(input.total_bytes) && input.total_bytes >= 0
  const metricsValid = metrics && typeof metrics === "object" && !Array.isArray(metrics)
    && Number.isSafeInteger(metrics.automatic_operations)
    && metrics.automatic_operations >= 0 && metrics.automatic_operations < 8
    && metrics.human_material_decisions === 0
    && metrics.operational_handoffs === 0
  const legal = snapshot?.legal_actions
  const ordinaryActions = [
    "accepted_task_progress", "progress", "review", "verify", "converge",
  ]
  const lifecycle = snapshot?.lifecycle
  const allowedStages = [
    ["PROPOSED", "RESEARCH"],
    ["READY", "READY"],
    ["IN_PROGRESS", "IMPLEMENT"],
    ["IN_PROGRESS", "REVIEW"],
    ["VERIFYING", "VERIFY"],
    ["VERIFYING", "CONVERGE"],
  ]
  const objectiveKeys = [
    "blockers", "heading", "mode", "request_sha256", "source_reference", "status",
  ]
  if (
    !objective || typeof objective !== "object" || Array.isArray(objective)
    || JSON.stringify(Object.keys(objective).sort()) !== JSON.stringify(objectiveKeys)
    || objective.status !== "BLOCKED"
    || objective.mode !== "INCREMENTAL"
    || !Array.isArray(objective.blockers)
    || objective.blockers.length !== 1
    || objective.blockers[0] !== "REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST"
    || objective.heading !== null
    || !reference
    || typeof objective.request_sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(objective.request_sha256)
    || objective.request_sha256 !== digest
    || reference[3] !== objective.request_sha256.slice(0, 16)
    || objective.source_reference !== `session:${reference[1]}:${reference[2]}:${reference[3]}`
    || !sessionRequestValid
    || !snapshot || !Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0
    || snapshot.initialization_state !== "INITIALIZED"
    || typeof snapshot.feature_id !== "string" || !snapshot.feature_id.trim()
    || typeof snapshot.change_id !== "string" || !snapshot.change_id.trim()
    || !Array.isArray(lifecycle)
    || !allowedStages.some((stage) => JSON.stringify(stage) === JSON.stringify(lifecycle))
    || snapshot.stale !== false
    || snapshot.completed_terminal !== false
    || snapshot.side_work_active !== false
    || snapshot.pending_intake !== false
    || !snapshot.decision_state
    || snapshot.decision_state.unresolved_material_decision !== false
    || snapshot.pending_autonomy_expansion !== null
    || snapshot.pending_capability_selection !== null
    || snapshot.pending_discovery_recovery !== null
    || !Array.isArray(snapshot.irreversible_authorizations)
    || snapshot.irreversible_authorizations.length !== 0
    || !Array.isArray(snapshot.active_locks)
    || snapshot.active_locks.length !== 0
    || snapshot.active_locks_total !== 0
    || snapshot.active_locks_complete !== true
    || snapshot.r3_schema_ready !== false
    || snapshot.r3b_schema_ready !== false
    || !snapshotValid
    || !exactEnvelope
    || !metricsValid
    || !Array.isArray(legal)
    || !ordinaryActions.some((action) => legal.includes(action))
  ) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_CONTEXT_INVALID")
  }
  return {
    objective,
    requestText: request.statement,
    requestDigest: digest,
    repositoryID: rootRepositoryID,
    fingerprint: input.fingerprint,
    revision: snapshot.revision,
  }
}

function sameRequestScopedIncrementalContext(left, right) {
  const fields = [
    "revision", "repository_id", "initialization_state", "lifecycle",
    "feature_id", "change_id", "stale", "completed_terminal",
    "side_work_active", "pending_intake", "accepted_slice", "input_snapshot",
    "request_scoped_objective", "autonomy_envelope", "autonomy_metrics",
    "decision_state", "outcome", "legal_actions", "guidance",
    "pending_autonomy_expansion", "pending_capability_selection",
    "pending_discovery_recovery", "irreversible_authorizations",
    "active_locks", "active_locks_total", "active_locks_complete",
    "r3_schema_ready", "r3b_schema_ready",
  ]
  return fields.every((field) =>
    JSON.stringify(left?.[field]) === JSON.stringify(right?.[field]))
}

function incrementalModeFailure() {
  return JSON.stringify({
    code: "REQUEST_SCOPED_INCREMENTAL_BLOCKED",
    directive: "FAIL_CLOSED",
    refresh_context: false,
    next_action: null,
    wait: null,
    retry: { eligible: false, attempt: 0, budget: 0 },
    reason: "the Core incremental projection did not pass its current source, session, root, revision, snapshot, and scope guards",
    material_decision: null,
  })
}

async function bindRequestScopedIncrementalMode(runtime, context, snapshot) {
  const sessionPath = await sessionRoot(runtime, context)
  if (typeof sessionPath !== "string" || !isAbsolute(sessionPath)) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_ROOT_INVALID")
  }
  const root = await realpath(sessionPath)
  const initial = requestScopedIncrementalBinding(snapshot, root)
  if (
    typeof context?.sessionID !== "string"
    || context.sessionID !== snapshot.request_scoped_objective.source_reference.split(":")[1]
    || typeof context?.agent !== "string" || !context.agent.trim()
    || typeof context?.messageID !== "string"
    || typeof context?.id !== "string" || !context.id.trim()
  ) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_CALLER_INVALID")
  }
  const source = await requestScopedObjectiveSource(runtime, context, initial.objective)
  if (
    source.message_id !== initial.objective.source_reference.split(":")[2]
    || source.request_text !== initial.requestText
    || source.request_sha256 !== initial.requestDigest
  ) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_SOURCE_INVALID")
  }
  const currentPath = await sessionRoot(runtime, context)
  if (typeof currentPath !== "string" || resolve(await realpath(currentPath)) !== resolve(root)) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_ROOT_CHANGED")
  }
  const current = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root),
  )
  if (!sameRequestScopedIncrementalContext(snapshot, current)) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_CONTEXT_CHANGED")
  }
  const fresh = requestScopedIncrementalBinding(current, root)
  const freshSource = await requestScopedObjectiveSource(runtime, context, fresh.objective)
  if (
    fresh.revision !== initial.revision
    || fresh.fingerprint !== initial.fingerprint
    || fresh.repositoryID !== initial.repositoryID
    || fresh.requestDigest !== initial.requestDigest
    || freshSource.message_id !== source.message_id
    || freshSource.request_text !== source.request_text
    || freshSource.request_sha256 !== source.request_sha256
  ) {
    throw new Error("REQUEST_SCOPED_INCREMENTAL_BINDING_CHANGED")
  }
  return { context: current, root }
}

async function executeRequestScopedObjective(runtime, args, context) {
  if (typeof runtime?.permission?.assert !== "function"
      || typeof runtime?.session?.get !== "function") {
    throw new Error("HOST_PERMISSION_UNAVAILABLE: full executing tool identity and permission API required")
  }
  const requester = {
    sessionID: context?.sessionID,
    agent: context?.agent,
    messageID: context?.messageID,
    id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("HOST_PERMISSION_UNAVAILABLE: full executing tool identity is required")
  }

  const sessionPath = await sessionRoot(runtime, context)
  if (typeof sessionPath !== "string" || !isAbsolute(sessionPath)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_ROOT_INVALID: executing repository root is unavailable")
  }
  const root = await realpath(sessionPath)
  const before = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root)
  )
  const binding = requestScopedObjectiveBinding(before, args.expected_revision)
  if (binding.unavailable) {
    return JSON.stringify({ code: binding.unavailable, context: before })
  }
  if (binding.complete) {
    return JSON.stringify({ code: "REQUEST_SCOPED_OBJECTIVE_COMPLETE", context: before })
  }

  const original = await requestScopedObjectiveSource(runtime, context, binding.objective)
  const requestDigest = original.request_sha256
  const indexPath = resolve(root, "index.html")
  const metadata = {
    operation: "request_scoped_objective",
    repository_root: root,
    repository_id: binding.repositoryID,
    source_reference: binding.objective.source_reference,
    request_sha256: requestDigest,
    expected_revision: args.expected_revision,
    input_fingerprint: binding.fingerprint,
    current_stage: binding.stage,
  }
  // Host policy controls allow/ask/deny for this exact operation; do not
  // rewrite a deny or force an ask.
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action: "evospec_request_scoped_objective",
    resources: [root, indexPath],
    metadata,
  })

  if (Object.entries(requester).some(([key, value]) => context?.[key] !== value)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_REQUESTER_CHANGED: tool identity changed during host admission")
  }
  const currentSessionPath = await sessionRoot(runtime, context)
  if (typeof currentSessionPath !== "string" || !isAbsolute(currentSessionPath)
      || resolve(await realpath(currentSessionPath)) !== resolve(root)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_ROOT_CHANGED: project moved during host admission")
  }
  const latest = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root)
  )
  const latestBinding = requestScopedObjectiveBinding(latest, args.expected_revision)
  if (latestBinding.unavailable || latestBinding.complete
      || latestBinding.repositoryID !== binding.repositoryID
      || latestBinding.fingerprint !== binding.fingerprint
      || latestBinding.objective.source_reference !== binding.objective.source_reference
      || latestBinding.objective.request_sha256 !== binding.objective.request_sha256
      || JSON.stringify(latestBinding.objective) !== JSON.stringify(binding.objective)
      || JSON.stringify(latestBinding.stage) !== JSON.stringify(binding.stage)) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_STALE: source, scope, stage, revision, or inputs changed during host admission")
  }
  const freshRequest = await requestScopedObjectiveSource(runtime, context, latestBinding.objective)
  if (freshRequest.message_id !== original.message_id
      || freshRequest.request_text !== original.request_text
      || freshRequest.request_sha256 !== requestDigest) {
    throw new Error("REQUEST_SCOPED_OBJECTIVE_REQUEST_CHANGED: admitted request changed during host admission")
  }

  return rawExecuteRequest(runtime, {
    action: "request_scoped_objective",
    expected_revision: args.expected_revision,
    repository_id: binding.repositoryID,
    session_id: requester.sessionID,
    message_id: original.message_id,
    request_text: original.request_text,
    input_fingerprint: binding.fingerprint,
  }, context, root)
}

// A single-file edit request is deliberately a proposal, not a grant.
// Core alone owns the exact envelope and the subsequent HUMAN decision.
function canonicalEditFilePath(value) {
  if (typeof value !== "string" || !value || value.length > 512
      || /[\u0000-\u001f\u007f\\]/.test(value) || value.startsWith("/")) {
    throw new Error("EDIT_PATH_INVALID: provide one canonical repository-relative file")
  }
  const parts = value.split("/")
  if (parts.some((part) => !part || part === "." || part === ".." || part.includes(":"))
      || [".git", ".evospec", ".opencode"].includes(parts[0])) {
    throw new Error("EDIT_PATH_INVALID: path escapes the allowed user-file scope")
  }
  return value
}

async function executeProposeFileEdit(runtime, args, context) {
  const path = canonicalEditFilePath(args.path)
  const root = await sessionRoot(runtime, context)
  const current = parseContextResponse(
    await rawExecuteRequest(runtime, { action: "context" }, context, root)
  )
  if (current.revision !== args.expected_revision) {
    throw new Error("STALE_REVISION: reread accepted Slice and edit scope")
  }
  if (!current.accepted_slice || current.lifecycle === null
      || !current.feature_id || !current.change_id
      || current.side_work_active || current.stale) {
    throw new Error("EDIT_SCOPE_UNAVAILABLE: no current, unambiguous accepted primary Slice")
  }
  if (!current.legal_actions?.includes("propose_autonomy_expansion")
      || current.pending_autonomy_expansion) {
    throw new Error("EDIT_SCOPE_UNAVAILABLE: Core does not currently allow an expansion proposal; inspect its current blocker")
  }
  const envelope = current.autonomy_envelope
  const metrics = current.autonomy_metrics
  if (!envelope || !Array.isArray(envelope.allowed_paths)
      || !Array.isArray(envelope.reversible_actions)
      || !Number.isSafeInteger(envelope.operation_budget)
      || !metrics || !Number.isSafeInteger(metrics.automatic_operations)
      || metrics.automatic_operations < 0) {
    throw new Error("EDIT_SCOPE_UNAVAILABLE: canonical autonomy budget or actions are incomplete")
  }
  const remaining = args.remaining_operations ?? 3
  if (!Number.isSafeInteger(remaining) || remaining < 1 || remaining > 4) {
    throw new Error("EDIT_BUDGET_INVALID: request 1 to 4 remaining Core operations")
  }
  const evaluated = JSON.parse(await rawExecuteRequest(runtime, {
    action: "autonomy_evaluate",
    expected_revision: current.revision,
    operation: "edit", paths: [path], irreversible: false,
  }, context, root))
  if (evaluated.error) return JSON.stringify(evaluated)
  if (evaluated.context?.revision !== current.revision
      || evaluated.context?.feature_id !== current.feature_id
      || evaluated.context?.change_id !== current.change_id) {
    throw new Error("STALE_REVISION: edit authority changed during read-only preflight")
  }
  if (evaluated.evaluation?.authorization === "ALLOWED") {
    return JSON.stringify({
      code: "EDIT_SCOPE_ALREADY_ALLOWED",
      path, revision: current.revision,
      instruction: "Edit is already inside the current Core envelope; host file permissions still apply.",
    })
  }
  if (evaluated.evaluation?.authorization !== "HUMAN_MATERIAL_DECISION") {
    throw new Error("EDIT_SCOPE_UNAVAILABLE: Core did not offer a material expansion decision")
  }
  // Delta collections replace entire fields, so preserve the existing grants
  // and append only the one requested file and reversible edit action.
  const paths = envelope.allowed_paths.includes(path)
    ? envelope.allowed_paths
    : [...envelope.allowed_paths, path]
  const actions = envelope.reversible_actions.some((action) => action.toLowerCase() === "edit")
    ? envelope.reversible_actions
    : [...envelope.reversible_actions, "edit"]
  const budget = Math.max(
    envelope.operation_budget,
    metrics.automatic_operations + remaining
  )
  if (!Number.isSafeInteger(budget) || budget <= 0) {
    throw new Error("EDIT_BUDGET_INVALID: finite positive operation budget required")
  }
  // Strict protocol requires all seven delta fields; null means
  // unchanged, not an attempted removal of existing authority.
  const delta = {
    allowed_paths: paths,
    reversible_actions: actions,
    operation_budget: budget,
    external_wait_allowed: null,
    retry_budget: null,
    irreversible_permissions: null,
    stop_conditions: null,
  }
  // A stale revision is never replayed, and the proposal still needs a
  // separate exact AUTHORIZE / REJECT / NARROW human decision.
  const response = JSON.parse(await executeRequest(runtime, {
    action: "propose_autonomy_expansion",
    expected_revision: current.revision,
    delta,
  }, context, { retryStale: false, authorizedRoot: root }))
  if (response.error) return JSON.stringify(response)
  return JSON.stringify({
    ...response,
    requested_edit_scope: {
      repository_root: root,
      path, remaining_operations: remaining,
      total_operation_budget: budget,
      existing_paths_preserved: envelope.allowed_paths,
      exact_delta: delta,
      human_decision_required: true,
    },
    instruction: "Show the exact proposed paths, actions and finite budget. Ask the user to choose only the Core-offered AUTHORIZE, REJECT or NARROW option. The proposal does not permit edits; reread context after the human decision and revalidate the host's file permission.",
  })
}

// Keep the probe bounded and privacy-preserving. A prompt hook can also see
// programmatic session.prompt calls; a permission "allow" may be a saved rule.
function noteAutoHostObservation(runtime, sessionID, kind, effect, event = null) {
  const observation = autoHostObservations.get(runtime)
  if (!observation || typeof sessionID !== "string" || !sessionID.trim()) return
  let seen = observation.sessions.get(sessionID)
  if (!seen) {
    if (observation.sessions.size >= 64) {
      observation.sessions.delete(observation.sessions.keys().next().value)
    }
    seen = { admitted: false, permissionEffects: new Set(), ruleSourceShapeSeen: false }
    observation.sessions.set(sessionID, seen)
  }
  if (kind === "prompt") seen.admitted = true
  if (kind === "permission" && ["allow", "ask"].includes(effect)) {
    seen.permissionEffects.add(effect)
    // An upstream host may eventually expose a rule source (#61767).
    // Record only the presence of a candidate field, never its contents:
    // prior plugins can also mutate events, so shape is not a signature.
    if (event?.rule && typeof event.rule === "object"
        && ["agent", "session", "saved"].includes(event.rule.source)) {
      seen.ruleSourceShapeSeen = true
    }
  }
}

async function executeAutoHostReadiness(runtime, context) {
  const root = await sessionRoot(runtime, context)
  if (typeof root !== "string" || !isAbsolute(root)) {
    throw new Error("AUTO_HOST_ROOT_UNAVAILABLE: session root must be absolute")
  }
  const observed = autoHostObservations.get(runtime)
  const seen = observed?.sessions.get(context.sessionID)
  // Never inspect configuration files, query settings, ask for permission,
  // or classify a model-provided message as authenticated human intent.
  return JSON.stringify({
    code: "AUTO_HOST_ATTESTATION_UNAVAILABLE",
    mode: "READ_ONLY_CAPABILITY_PROBE",
    session: {
      session_id: context.sessionID,
      repository_root: resolve(root),
      root_source: "OPEN_CODE_SESSION_GET",
    },
    observed_capabilities: {
      prompt_hook_registered: observed?.promptHookRegistered === true,
      permission_evaluate_hook_registered: observed?.permissionHookRegistered === true,
      session_context_api_available: typeof runtime?.session?.context === "function",
      permission_assert_api_available: typeof runtime?.permission?.assert === "function",
      prompt_admission_seen_this_process: seen?.admitted === true,
      permission_rule_effects_seen_this_process: [...(seen?.permissionEffects ?? [])],
    },
    trust: {
      effective_auto_mode: "UNVERIFIED",
      prompt_human_origin: "UNVERIFIED",
      permission_allow_provenance: "UNVERIFIED",
      durable_consent_receipt: "UNAVAILABLE",
      delegated_core_authority: "NOT_GRANTED",
    },
    automatic_acceptance_allowed: false,
    automatic_file_grant_allowed: false,
    automatic_feature_completion_allowed: false,
    blockers: [
      "AUTO_MODE_PROVENANCE_UNAVAILABLE",
      "HUMAN_INTENT_PROVENANCE_UNAVAILABLE",
    ],
    explanation: "Prompt admission may be programmatic, and an allow effect may come from saved or configured rules. These observations are not consent, do not survive reload, and never bypass Core or host denials. R2-C1 is qualification only; E1 TTY gates remain unchanged.",
  })
}

// R2-C2 detects candidate host API shapes without calling them. This report
// is an integration dependency diagnostic, never an authority or TTY receipt.
async function executeAutoContractProbe(runtime, context) {
  const root = await sessionRoot(runtime, context)
  if (typeof root !== "string" || !isAbsolute(root)) {
    throw new Error("AUTO_CONTRACT_ROOT_UNAVAILABLE: session root must be absolute")
  }
  const observations = autoHostObservations.get(runtime)
  const seen = observations?.sessions.get(context.sessionID)
  const ruleField = seen?.ruleSourceShapeSeen === true
  const savedList = typeof runtime?.permission?.saved?.list === "function"
  return JSON.stringify({
    code: "AUTO_DELEGATION_CONTRACT_BLOCKED",
    version: "r2-c2-host-contract-v1",
    mode: "READ_ONLY_COMPATIBILITY_PROBE",
    session: {
      session_id: context.sessionID,
      repository_root: resolve(root),
      root_source: "OPEN_CODE_SESSION_GET",
    },
    candidates: {
      permission_rule_origin_field: ruleField ? "SHAPE_OBSERVED_UNQUALIFIED" : "NOT_OBSERVED",
      saved_permission_list_api: savedList ? "API_PRESENT_UNQUALIFIED" : "NOT_EXPOSED",
      prompt_hook: observations?.promptHookRegistered === true ? "PRESENT" : "UNAVAILABLE",
      permission_evaluate_hook: observations?.permissionHookRegistered === true ? "PRESENT" : "UNAVAILABLE",
    },
    required_trust: {
      user_prompt_origin: "UNVERIFIED",
      effective_auto_mode_and_rule_origin: "UNVERIFIED",
      policy_epoch_and_revocation: "UNVERIFIED",
      exact_invocation_one_time_admission: "UNVERIFIED",
      scoped_semantic_intent: "UNVERIFIED",
    },
    authority: {
      user_acceptance_source: "NOT_ATTESTED",
      delegated_core_authority: "NOT_GRANTED",
      automatic_slice_acceptance_allowed: false,
      automatic_edit_grant_allowed: false,
      automatic_feature_completion_allowed: false,
    },
    blockers: [
      "HUMAN_PROMPT_ORIGIN_ATTESTATION_REQUIRED",
      "PERMISSION_MODE_AND_SOURCE_ATTESTATION_REQUIRED",
      "HOST_SINGLE_USE_ADMISSION_REQUIRED",
      "POLICY_REVOCATION_BINDING_REQUIRED",
    ],
    guidance: "Candidate APIs or fields, when observed, are not authenticated grants. Qualify host-issued provenance, scoped execution, revocation and a one-use binding before changing Core; do not infer consent from autoaccept, allow, saved rules, a user-role message, or model text. No Core mutation, permission request, API call, or file read was performed.",
  })
}

async function executeStart(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  const initialized = await ensureInitializedForExplicitTask(runtime, root, args.intent, context)
  if (args.operation === "new" && args.intake_route !== "legacy"
      && firstSliceHandoffEligible(initialized)) {
    return JSON.stringify({
      code: "SESSION_FIRST_SLICE_AVAILABLE",
      state: "NO_FEATURE_CREATED",
      repository_root: root,
      revision: initialized.revision,
      next_tool: "evospec_session_first_slice",
      instruction: "For a clear, low-risk, new single-file web app, call evospec_session_first_slice with this revision. It reads the original admitted session message itself, asks OpenCode to authorize the operation under its current session policy, and records an exact first Slice without TTY; Core permits only a fresh index.html. If provenance or scope fails, explain the specific blocker without forging user consent or falling back to legacy NEW.",
    })
  }
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
  const objective = canonical.request_scoped_objective
  if (objective !== null && objective !== undefined
      && (!objective || typeof objective !== "object" || Array.isArray(objective))) {
    return incrementalModeFailure()
  }
  const incrementalDeclared = objective && Object.hasOwn(objective, "mode")
  const blockedObjective = objective?.status === "BLOCKED"
  let incremental = false
  let current = canonical
  if (incrementalDeclared || blockedObjective) {
    const preflight = deriveContinuationDirective(canonical, {
      explicitTask: args.explicit_task,
      sessionRootMatches: true,
      externalStatus: args.external_status,
      failureCode: args.failure_code,
      attempt: args.attempt,
    })
    if (preflight.directive !== "CONTINUE" || preflight.refresh_context) {
      return JSON.stringify(preflight)
    }
    if (objective?.status !== "BLOCKED" || objective.mode !== "INCREMENTAL") {
      return incrementalModeFailure()
    }
    try {
      const bound = await bindRequestScopedIncrementalMode(runtime, context, canonical)
      current = bound.context
      incremental = true
    } catch {
      return incrementalModeFailure()
    }
  }
  let operationEvaluation = null
  if (typeof args.operation === "string" && args.operation.trim() && current.autonomy_envelope !== null) {
    const evaluationText = await executeRequest(runtime, {
      action: "autonomy_evaluate",
      expected_revision: current.revision,
      operation: args.operation,
      paths: args.paths,
      irreversible: args.irreversible,
    }, context)
    const evaluationResponse = JSON.parse(evaluationText)
    operationEvaluation = evaluationResponse.evaluation
    current = contextFromResponse(evaluationResponse) || current
  }
  const directive = deriveContinuationDirective(current, {
    explicitTask: args.explicit_task,
    sessionRootMatches: true,
    ...(args.operation ? { operation: args.operation, operationEvaluation } : {}),
    externalStatus: args.external_status,
    failureCode: args.failure_code,
    attempt: args.attempt,
  })
  if (!incremental || directive.directive !== "CONTINUE" || directive.refresh_context) {
    return JSON.stringify(directive)
  }
  if (directive.next_action?.action === "activate_provisional_slice_writes") {
    return incrementalModeFailure()
  }
  if (current !== canonical) {
    try {
      await bindRequestScopedIncrementalMode(runtime, context, current)
    } catch {
      return incrementalModeFailure()
    }
  }
  return JSON.stringify({
    ...directive,
    mode: "INCREMENTAL",
    revision: current.revision,
    source_reference: objective.source_reference,
    request_sha256: objective.request_sha256,
  })
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

function protocolTool(name, description, input, execute, options = undefined) {
  return {
    name,
    description,
    input,
    ...(options === undefined ? {} : { options }),
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

function continuityRequest(operation, expectedRevision, sessionID, data = {}) {
  return {
    action: "continuity",
    operation,
    expected_revision: expectedRevision,
    session_id: sessionID,
    data,
  }
}

function continuityValue(text) {
  const response = JSON.parse(text)
  if (response?.error) {
    throw new Error(`${response.error.code}: ${response.error.detail}`)
  }
  if (!response?.continuity || typeof response.continuity !== "object") {
    throw new Error("EvoSpec did not return canonical continuity state")
  }
  return response.continuity
}

function continuityWrites(operation, data) {
  if (["specify", "clarify", "plan", "change", "pause", "resume", "close"].includes(operation)) return true
  if (operation === "use") return Object.keys(data).length > 0
  if (operation === "tasks") return data.task_title !== undefined
  if (operation === "implement") return data.phase === "begin" || data.phase === "report"
  return false
}

function sameRequester(context, requester) {
  return ["sessionID", "agent", "messageID", "id"].every((key) => context?.[key] === requester[key])
}

async function authorizeContinuityWrite(runtime, context, root, operation, data, action) {
  if (typeof runtime?.permission?.assert !== "function") {
    throw new Error("OpenCode V2 permission API is unavailable")
  }
  const requester = {
    sessionID: context?.sessionID,
    agent: context?.agent,
    messageID: context?.messageID,
    id: context?.id,
  }
  if (Object.values(requester).some((value) => typeof value !== "string" || !value.trim())) {
    throw new Error("OpenCode V2 did not provide complete continuity permission context")
  }
  await runtime.permission.assert({
    sessionID: requester.sessionID,
    agent: requester.agent,
    source: { type: "tool", messageID: requester.messageID, id: requester.id },
    action,
    resources: [JSON.stringify({
      repository_root: resolve(root),
      operation,
      feature_id: data.feature_id ?? null,
      task_id: data.task_id ?? null,
      criterion_id: data.criterion_id ?? null,
    })],
  })
  if (!sameRequester(context, requester) || resolve(await sessionRoot(runtime, context)) !== resolve(root)) {
    throw new Error("OpenCode continuity requester or repository changed while permission was pending")
  }
  return requester
}

async function executeContinuity(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  if (typeof root !== "string" || !isAbsolute(root)) {
    throw new Error("OpenCode V2 did not provide an absolute continuity repository root")
  }
  const operation = args.operation
  const data = args.data ?? {}
  if (operation !== "verify") {
    const write = continuityWrites(operation, data)
    if (write) {
      const action = operation === "close" ? "evospec_continuity_close" : "evospec_continuity_write"
      await authorizeContinuityWrite(runtime, context, root, operation, data, action)
    }
    return rawExecuteRequest(
      runtime,
      continuityRequest(operation, args.expected_revision ?? null, context.sessionID, data),
      context,
      root,
    )
  }

  const requested = continuityValue(await rawExecuteRequest(
    runtime,
    continuityRequest("status", null, context.sessionID),
    context,
    root,
  ))
  const feature = requested.features?.find((item) =>
    item.id === (data.feature_id ?? requested.selected_feature_id))
  const criterion = feature?.criteria?.find((item) => item.id === data.criterion_id)
  if (!feature || !criterion || feature.status !== "OPEN" || requested.execution !== null) {
    throw new Error("Continuity verification is stale, ambiguous, closed, or blocked by an unresolved Task attempt")
  }
  if (args.expected_revision !== undefined && args.expected_revision !== null &&
      args.expected_revision !== requested.revision) {
    throw new Error("STALE_REVISION: reread continuity status before verification")
  }
  const command = criterionCommand(criterion.check_kind, criterion.check_target)
  const requester = await authorizeContinuityWrite(
    runtime, context, root, operation,
    { ...data, feature_id: feature.id, criterion_id: criterion.id, command },
    "evospec_continuity_verify",
  )
  const fresh = continuityValue(await rawExecuteRequest(
    runtime,
    continuityRequest("status", null, requester.sessionID),
    context,
    root,
  ))
  const freshFeature = fresh.features?.find((item) => item.id === feature.id)
  const freshCriterion = freshFeature?.criteria?.find((item) => item.id === criterion.id)
  if (fresh.revision !== requested.revision || fresh.execution !== null ||
      freshCriterion?.digest !== criterion.digest ||
      freshCriterion?.check_kind !== criterion.check_kind ||
      freshCriterion?.check_target !== criterion.check_target) {
    throw new Error("STALE_REVISION: Core authority changed while continuity permission was pending")
  }
  const result = await spawnCommand(command, root, context)
  if (!Number.isInteger(result.exitStatus) || result.exitStatus < 0 || result.exitStatus > 255) {
    throw new Error("Continuity check did not return a supported process exit code")
  }
  if (!sameRequester(context, requester) || resolve(await sessionRoot(runtime, context)) !== resolve(root)) {
    throw new Error("OpenCode continuity requester or repository changed while the check was running")
  }
  const checked = continuityValue(await rawExecuteRequest(
    runtime,
    continuityRequest("status", null, requester.sessionID),
    context,
    root,
  ))
  const checkedFeature = checked.features?.find((item) => item.id === feature.id)
  const checkedCriterion = checkedFeature?.criteria?.find((item) => item.id === criterion.id)
  if (checked.revision !== fresh.revision || checked.execution !== null ||
      checkedCriterion?.digest !== criterion.digest ||
      checkedCriterion?.check_kind !== criterion.check_kind ||
      checkedCriterion?.check_target !== criterion.check_target) {
    throw new Error("STALE_REVISION: Core authority changed while the continuity check was running")
  }
  const outputDigest = createHash("sha256").update(`${result.output}\n${result.error}`).digest("hex")
  const recorded = await rawExecuteRequest(
    runtime,
    continuityRequest("verify", checked.revision, requester.sessionID, {
      feature_id: feature.id,
      criterion_id: criterion.id,
      check_kind: criterion.check_kind,
      check_target: criterion.check_target,
      exit_code: result.exitStatus,
      output_sha256: outputDigest,
      ...(data.task_id === undefined ? {} : { task_id: data.task_id }),
    }),
    context,
    root,
  )
  return JSON.stringify({
    check: {
      command,
      exit_code: result.exitStatus,
      output_sha256: outputDigest,
      stdout: result.output,
      stderr: result.error,
      result: result.exitStatus === 0 ? "CHECK_RECORDED_PASS" : "CHECK_RECORDED_FAIL",
    },
    core: JSON.parse(recorded),
  })
}

const continuityCriterion = object({
  id: nullableString,
  description: string,
  check_kind: { type: "string", enum: ["python-unittest", "node-test"] },
  check_target: string,
}, ["description", "check_kind", "check_target"])
const continuityData = object({
  title: string,
  outcome: string,
  criteria: { type: "array", items: continuityCriterion, maxItems: 200 },
  increments: { type: "array", items: object({ title: string, outcome: string }), maxItems: 100 },
  tasks: { type: "array", items: object({
    title: string,
    description: string,
    increment_index: { type: ["integer", "null"], minimum: 0 },
    criteria: { type: "array", items: { type: "integer", minimum: 0 } },
    depends_on: { type: "array", items: { type: "integer", minimum: 0 } },
  }, ["title"]), maxItems: 1000 },
  plan: string,
  feature_id: string,
  scope: { type: "string", enum: ["session", "worktree"] },
  question: string,
  decision: string,
  task_title: string,
  task_description: string,
  increment_id: string,
  criteria_ids: stringArray,
  depends_on: stringArray,
  task_id: string,
  phase: { type: "string", enum: ["queue", "begin", "report"] },
  report: string,
  result: { type: "string", enum: ["SUCCEEDED", "BLOCKED", "UNKNOWN"] },
  criterion_id: string,
  check_kind: { type: "string", enum: ["python-unittest", "node-test"] },
  check_target: string,
  exit_code: { type: "integer", minimum: 0, maximum: 255 },
  output_sha256: sha256,
  reason: string,
  detour_kind: { type: "string", enum: ["RELATED", "EXTERNAL"] },
  detour_title: string,
  return_task_id: string,
  reconcile: { type: "string", enum: ["COMPLETE", "INCOMPLETE"] },
  evidence_ref: string,
  detour_summary: string,
}, [])

const tools = [
  protocolTool(
    "evospec",
    "Read EvoSpec context or submit one provider-neutral protocol request for the executing session's repository.",
    object({ request: string }),
    (args, context) => {
      // Keep the E1 capability visible to the host as its own permission-controlled tool.
      if (["continuity", "accept_first_slice_session", "request_scoped_objective", "accepted_task_progress", "criterion_test_result", "criterion_test_result_r3b",
        "accept_slice_outcome", "accept_historical_slice_outcome"].includes(staleRequest(args.request)?.action)) {
        return JSON.stringify({ error: {
          code: "DEDICATED_TOOL_REQUIRED",
          detail: "Use the dedicated host-authorized tool for composed continuity, session-bound objective verification, accepted Task, criterion test evidence, or Slice outcome acceptance",
        } })
      }
      return executeRequest(context.runtime, args.request, context)
    },
  ),
  protocolTool(
    "evospec_continuity",
    "Read or mutate the document-backed composed work continuity for this OpenCode worktree. The host supplies session identity; mutating operations and checks require host permission. A permission result is not Feature acceptance.",
    object({
      operation: { type: "string", enum: ["specify", "clarify", "plan", "tasks", "implement", "use", "change", "status", "verify", "pause", "resume", "close"] },
      expected_revision: { type: ["integer", "null"], minimum: 0 },
      data: continuityData,
    }, ["operation"]),
    (args, context) => executeContinuity(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_start",
    "Start session intake. For an empty E1 repo, offer native session-bound first Slice without TTY when the host can supply admitted message provenance. Legacy routing is explicit only.",
    object({
      operation: { type: "string", enum: ["new", "change"] },
      intent: string,
      intake_route: { type: "string", enum: ["auto", "legacy"] },
    }, ["operation", "intent"]),
    (args, context) => executeStart(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_first_slice_handoff",
    "Prepare one revision-bound, read-only first-Slice agreement for an initialized empty E1 ledger. Return a human TTY approval command without accepting or writing anything.",
    object({
      expected_revision: nonnegativeInteger,
      feature: { type: "string", minLength: 1, maxLength: 2048 },
      slice: { type: "string", minLength: 1, maxLength: 2048 },
      task: { type: "string", minLength: 1, maxLength: 2048 },
      checkpoint: { type: "string", minLength: 1, maxLength: 2048 },
    }),
    (args, context) => executeFirstSliceHandoff(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_session_first_slice",
    "For a new, empty repository only: recover the admitted user message from the executing OpenCode session, ask the host to authorize session intake, then persist the exact Feature/Slice/Task with only index.html in Core edit scope. Never claims authenticated human approval; cannot add criteria or close Feature.",
    object({ expected_revision: nonnegativeInteger }),
    (args, context) => executeSessionFirstSlice(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_request_scoped_objective",
    "Verify and then close only the bounded objective derived by Core from the exact admitted session request, after host policy admits this exact operation. Requires only the current revision; never accepts model-supplied criteria or results.",
    object({ expected_revision: nonnegativeInteger }),
    (args, context) => executeRequestScopedObjective(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_propose_file_edit",
    "Propose bounded edit authority for one file of the currently accepted Slice, preserving existing Core constraints. Requires a separate exact human Core decision; it never edits files or grants permission.",
    object({
      expected_revision: nonnegativeInteger,
      path: { type: "string", minLength: 1, maxLength: 512 },
      remaining_operations: { type: "integer", minimum: 1, maximum: 4 },
    }, ["expected_revision", "path"]),
    (args, context) => executeProposeFileEdit(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_auto_readiness",
    "Read-only R2-C1 host capability and provenance qualification for this OpenCode session. Never infers Auto consent or grants Feature, Slice, edit, or completion authority.",
    object({}),
    (_, context) => executeAutoHostReadiness(context.runtime, context),
  ),
  protocolTool(
    "evospec_auto_contract",
    "Read-only R2-C2 compatibility check for host-issued prompt and permission provenance. Candidate APIs remain unqualified: this tool never grants authority or completes work.",
    object({}),
    (_, context) => executeAutoContractProbe(context.runtime, context),
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
    // The canonical read-only entry point must remain directly discoverable
    // even when Code Mode's deferred catalog omits other plugin tools.
    { codemode: false },
  ),
  protocolTool(
    "evospec_accepted_task_progress",
    "Report one unverified operational checkpoint for the exact human-accepted E1 Task; requires the host's permission and Core's revision, root, scope, and autonomy checks. Never approves or verifies a Task.",
    object({
      expected_revision: nonnegativeInteger,
      repository_id: { type: "string", minLength: 1, maxLength: 2048 },
      task_id: { type: "string", minLength: 1, maxLength: 2048 },
      feature_agreement_ref: { type: "string", minLength: 1, maxLength: 2048 },
      slice_agreement_ref: { type: "string", minLength: 1, maxLength: 2048 },
      checkpoint: { type: "string", minLength: 1, maxLength: 2048 },
      statement: { type: "string", minLength: 1, maxLength: 2048 },
      evidence_refs: { type: "array", items: { type: "string", minLength: 1, maxLength: 2048 }, maxItems: 16, uniqueItems: true },
    }),
    // A stale report is never silently rebound to a different accepted revision.
    async (args, context) => {
      const request = { ...args, action: "accepted_task_progress" }
      const authorizedRoot = await authorizeAcceptedTaskProgress(context.runtime, request, context)
      return executeRequest(context.runtime, request, context, { retryStale: false, authorizedRoot })
    },
  ),
  protocolTool(
    "evospec_criterion_test",
    "Run a host-permitted deterministic test named by the current human-accepted criterion; record its actual exit code and output digest without model attestation, auto-acceptance, or stale retries.",
    object({
      expected_revision: nonnegativeInteger,
      repository_id: { type: "string", minLength: 1 },
      slice_agreement_ref: { type: "string", minLength: 1 },
      criteria_revision_id: { type: "string", minLength: 1 },
      criterion_id: { type: "string", minLength: 1 },
      test_kind: { type: "string", enum: ["python-unittest", "node-test"] },
      test_target: { type: "string", minLength: 1, maxLength: 256 },
    }),
    (args, context) => executeCriterionTest(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_criterion_test_r3b",
    "Run one supported human-accepted deterministic test under schema 17; bind native permission to the exact Core snapshot and sanitized runtime profile, then record fresh evidence without accepting the Slice. This is trusted-local evidence, not a sandbox.",
    object({
      expected_revision: nonnegativeInteger,
      repository_id: { type: "string", minLength: 1, maxLength: 2048 },
      slice_agreement_ref: { type: "string", minLength: 1, maxLength: 2048 },
      criteria_revision_id: { type: "string", minLength: 1, maxLength: 2048 },
      criterion_id: { type: "string", minLength: 1, maxLength: 2048 },
      test_kind: { type: "string", enum: ["python-unittest", "node-test"] },
      test_target: { type: "string", minLength: 1, maxLength: 256 },
    }),
    (args, context) => executeCriterionTestR3B(context.runtime, args, context),
  ),
  protocolTool(
    "evospec_accept_slice_outcome",
    "Accept an active Slice outcome or explicitly named historical terminal Slice in a human-reopened Feature cycle, only with fresh passing evidence and exact-scope host permission.",
    object({ expected_revision: nonnegativeInteger,
      slice_agreement_ref: { type: "string", minLength: 1, maxLength: 2048 } }, ["expected_revision"]),
    (args, context) => executeAcceptSliceOutcome(context.runtime, args, context),
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
    const autoObservation = {
      sessions: new Map(),
      promptHookRegistered: false,
      permissionHookRegistered: false,
    }
    autoHostObservations.set(runtime, autoObservation)
    if (typeof runtime?.permission?.hook === "function") {
      await runtime.permission.hook("evaluate", (event) => {
        // Observe only the rule effect; never change it for the Auto probe.
        noteAutoHostObservation(runtime, event.sessionID, "permission", event.effect, event)
        if (!R3B_HUMAN_PERMISSION_ACTIONS.has(event.action) || event.effect === "deny") return
        // Ask again even when a permissive default or saved approval would
        // otherwise let the host execute without showing a human decision.
        event.effect = "ask"
        event.message = "EvoSpec R3-B requires explicit approval. Inspect repository_root before proceeding."
      })
      explicitR3BPermissionHooks.add(runtime)
      autoObservation.permissionHookRegistered = true
    }
    const runtimeTools = tools.map((definition) => ({
      ...definition,
      execute: (args, context) => definition.execute(
        args,
        definition.name === "evospec_criterion_test_r3b"
          ? new Proxy(context, {
              get(target, property, receiver) {
                return property === "runtime"
                  ? runtime
                  : Reflect.get(target, property, receiver)
              },
            })
          : { ...context, runtime },
      ),
    }))
    await runtime.tool.transform((editor) => {
      for (const definition of runtimeTools) editor.add(definition)
    })
    const registered = await runtime.tool.list()
    const names = new Set(registered.map((tool) => tool.id))
    const missing = runtimeTools.map((tool) => tool.name).filter((name) => !names.has(name))
    if (missing.length) throw new Error(`EvoSpec V2 tools were not registered: ${missing.join(", ")}`)
    // The host's prompt admission hook is an observation, not proof that
    // a human typed the prompt; session.prompt and commands can also invoke it.
    // Older OpenCode hosts may lack this hook, so report unavailable instead
    // of failing plugin startup or fabricating a source receipt.
    if (typeof runtime?.session?.hook === "function") {
      try {
        await runtime.session.hook("prompt", (event) => {
          noteAutoHostObservation(runtime, event.sessionID, "prompt")
        })
        autoObservation.promptHookRegistered = true
      } catch {
        autoObservation.promptHookRegistered = false
      }
    }
    await runtime.session.hook("context", (event) => {
      event.system.push({
        type: "text",
        text: "For a canonical request_scoped_objective with kind PERSONAL_TASKS_WEB, use Core's ordered criteria as the complete product boundary: create and consult are the baseline operations; include complete only when Core lists it with provenance EXPLICIT_REQUEST. Layout, appearance, and persistence implementation details are discretionary. Do not write unless the exact admitted SESSION_REQUEST source is current, Core reports only REQUEST_OBJECTIVE_ARTIFACT_MISSING and REQUEST_OBJECTIVE_ARTIFACT_HASH_UNAVAILABLE, index.html is absent, no material decision is pending, and the current Core scope and operation budget still allow editing only index.html. Then create only index.html with a visible text entry [data-evo-action=\"task-input\"], a visible native add button [data-evo-action=\"add-task\"], and one visible [data-evo-task] row per actual task with its exact title; add a native [data-evo-action=\"complete-task\"] checkbox only for an explicitly requested complete operation. Add no other files, requirements, external dependencies, or network behavior. Use normal host file permission and keep the existing session/source/root/revision/fingerprint checks; do not add a permission gate or suggest TTY acceptance. Reread context after the file creation, call evospec_request_scoped_objective only for the same-source PENDING state, and make a separate fresh-revision call only after VERIFIED; stop at COMPLETE. For read-only, material, unsupported requests outside the exact guarded INCREMENTAL continuation below, or out-of-scope requests, do not write or suggest terminal recovery: report Core's exact blocker and give only safe ordinary-workflow guidance.",
      })
      event.system.push({
        type: "text",
         text: "For natural-language questions about where work stands or where to resume (¿dónde seguimos?, ¿en qué quedamos?, where were we?), load the evospec skill and call only read operations: first evospec_continuity status for the executing session/worktree; if it reports no composed mode or legacy MIXED_AUTHORITY, call only evospec_context for the legacy projection. Report composed Features, optional increments, Tasks, criterion checks, pauses, unknown outcomes, and next eligible Task only from canonical continuity; in legacy mode report the known Feature, Slice, Task, and checkpoint, distinguishing task_progress (REPORTED_UNVERIFIED). Never treat a checkbox/report/check as acceptance or closure. Corrupt or diverged state is a stop. Read-only questions never initialize, continue, reaccept, select, or mutate. " +
          "If evospec_context is unavailable or fails, report that the authoritative context cannot be verified and stop; never replace it with Engram memories, previous session messages, other repositories, or guessed work. " +
          "For a clear low-risk single-file web application in a freshly initialized empty E1 repository, call evospec_session_first_slice with the current revision instead of a TTY acceptance. It obtains the admitted user message from the current session itself and asks the host for the session-intake tool permission; Core records source SESSION_REQUEST (not authenticated human approval) and grants ONLY a fresh index.html with finite reversible budget. Never invent or widen scope from model-generated prose; follow Core and host denials. No ACEPTO or AUTHORIZE is required for that exact bounded initial file if Core and host both allow it. Do not create legacy active work first. If the current Core envelope still excludes a requested edit, do NOT silently self-grant it; use evospec_propose_file_edit only for other explicitly accepted work. Session intake alone does not register formal Slice criteria or complete a Feature; the separate request_scoped_objective route may create formal verification and Slice-outcome receipts only for the exact supported static-page scope, never broader Feature requirements. For other explicit EvoSpec lifecycle work, load the evospec skill and call evospec_context before repository inspection, tests, or lifecycle mutations. Then use evospec_continuation and follow its deterministic adapter directive: SAFE_AUTOMATIC_OPERATION and in-envelope BOUNDED_DELEGATION continue; EXTERNAL_WAIT waits; HUMAN_MATERIAL_DECISION asks exactly Core's decision; STRUCTURAL_DEFECT fails closed; terminal with outcome null completes. Re-read context after every Core mutation, reevaluate STALE_REVISION before any retry, and never edit .evospec manually. " +
          "Treat request_scoped_objective as a dedicated one-shot branch except for the exact guarded incremental case. For a canonical PERSONAL_TASKS_WEB objective, preserve its existing one-shot lifecycle phase: BLOCKED does not call evospec_request_scoped_objective; only when Core provides a valid non-empty heading, index.html is absent, no material decision is pending, and blockers contain exactly REQUEST_OBJECTIVE_ARTIFACT_MISSING and REQUEST_OBJECTIVE_ARTIFACT_HASH_UNAVAILABLE (in any order, no duplicates or other blockers), create only index.html within the current SESSION_REQUEST Core scope using the skill's inert HTML template and ordinary OpenCode edit/write permission. Re-read context after editing; proceed only if the same objective/source is now PENDING. Repair incorrect existing content only if current Core scope explicitly permits that exact edit; never expand scope. For PENDING, call the dedicated tool with only the current revision when the file is ready under current scope and no material decision is pending; reread context, and call it again at the fresh revision only when the same source is VERIFIED and no material decision is pending; reread after closure and stop at COMPLETE. Never invent criteria or results. The sole incremental exception is a Core objective with status exactly BLOCKED, mode exactly INCREMENTAL, and exactly one blocker: REQUEST_OBJECTIVE_UNSUPPORTED_REQUEST. For that case, call evospec_continuation and continue only if its fresh source, executing-session, repository-root, revision, input-fingerprint, and scope checks return mode INCREMENTAL with a current ordinary Core-authorized next_action. Follow only that action through the ordinary Task workflow under the active Core envelope and normal OpenCode file permissions; the marker grants no file/objective permission, criteria, proof, D4 activation, or D5 adoption. Never call evospec_request_scoped_objective for this branch. Any other, additional, or unknown blocker, missing/unknown mode, or failed guard remains blocked at Core's exact blocker. Material decisions are handled before mode routing by evospec_continuation; ask exactly Core's decision and never route around it. Stale refresh, external wait, structural defect, and terminal outcomes retain their existing deterministic directives. For every other non-null objective, preserve the current one-shot lifecycle phase and do not use generic lifecycle mutations to advance it. The objective adapter scans the full returned transcript: top-level typed synthetic records after the canonical source are opaque non-user events only, never trusted/authenticated or consent, permission, authority, or evidence; every actual objective call still requires native permission. Real later users are allowed only as attachment-free same-session continue or continúa; malformed/unknown events, missing sources, and substantive follow-ups block. Initial session intake still rejects synthetic events between the original request and its tool. The transcript is only an observed snapshot, with no atomic guarantee for unseen prompts arriving after the final host read. Never skip substantive prompts or infer the original request from them. The host permission policy remains authoritative; do not force an ask or claim it proves human approval.",
      })
    })
  },
}
