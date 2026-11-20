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
  return c?.initialization_state === "INITIALIZED" && c.e1_schema_ready === true
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
    throw new Error("FIRST_SLICE_UNAVAILABLE: Core's initialized E1 work ledger is not demonstrably empty")
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
    repository_root: root,
    repository_id: c.repository_id,
    revision: c.revision,
    proposed_agreement: agreement,
    terminal_command: (globalThis.process.platform === "win32" ? "& " : "")
      + argv.map(quoteShell).join(" "),
    instruction: "Show the agreement and command once. The human must run it in an interactive terminal, inspect Core's exact agreement and type ACEPTO there. Chat responses or OpenCode permission dialogs do not substitute for TTY acceptance. Then reread evospec_context; proceed only if the actual accepted Slice exists.",
    limitations: "No criteria-file attached; Task progress, tests, Slice outcome, Feature completion, edit permissions and Git remain separate.",
  })
}

async function executeStart(runtime, args, context) {
  const root = await sessionRoot(runtime, context)
  const initialized = await ensureInitializedForExplicitTask(runtime, root, args.intent, context)
  if (args.operation === "new" && args.intake_route !== "legacy"
      && firstSliceHandoffEligible(initialized)) {
    return JSON.stringify({
      code: "FIRST_SLICE_HANDOFF_REQUIRED",
      state: "NO_FEATURE_CREATED",
      repository_root: root,
      revision: initialized.revision,
      next_tool: "evospec_first_slice_handoff",
      instruction: "Propose the smallest user-observable first Slice, one initial Task and checkpoint; call the read-only handoff tool at this revision. Do not run legacy NEW before TTY approval.",
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

const tools = [
  protocolTool(
    "evospec",
    "Read EvoSpec context or submit one provider-neutral protocol request for the executing session's repository.",
    object({ request: string }),
    (args, context) => {
      // Keep the E1 capability visible to the host as its own permission-controlled tool.
      if (["accepted_task_progress", "criterion_test_result", "criterion_test_result_r3b",
        "accept_slice_outcome", "accept_historical_slice_outcome"].includes(staleRequest(args.request)?.action)) {
        return JSON.stringify({ error: {
          code: "DEDICATED_TOOL_REQUIRED",
          detail: "Use the dedicated host-authorized tool for accepted Task, criterion test evidence, or Slice outcome acceptance",
        } })
      }
      return executeRequest(context.runtime, args.request, context)
    },
  ),
  protocolTool(
    "evospec_start",
    "Start session intake. A first new Feature in an empty E1 project returns a read-only first-Slice handoff; explicitly requested legacy routing remains available.",
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
    if (typeof runtime?.permission?.hook === "function") {
      await runtime.permission.hook("evaluate", (event) => {
        if (!R3B_HUMAN_PERMISSION_ACTIONS.has(event.action) || event.effect === "deny") return
        // Ask again even when a permissive default or saved approval would
        // otherwise let the host execute without showing a human decision.
        event.effect = "ask"
        event.message = "EvoSpec R3-B requires explicit approval. Inspect repository_root before proceeding."
      })
      explicitR3BPermissionHooks.add(runtime)
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
    await runtime.session.hook("context", (event) => {
      event.system.push({
        type: "text",
        text: "For natural-language questions about where work stands or where to resume (¿dónde seguimos?, ¿en qué quedamos?, where were we?), load the evospec skill and call only evospec_context for the current session project. Report the known Feature, Slice, Task, and next checkpoint plainly; distinguish initial acceptance from later task_progress (REPORTED_UNVERIFIED), and disclose missing or ambiguous context. When there is no active Task, report accepted work_queue items as inactive and never choose or activate one on a status query. Read-only questions never initialize, continue, reaccept, or mutate. " +
          "If evospec_context is unavailable or fails, report that the authoritative context cannot be verified and stop; never replace it with Engram memories, previous session messages, other repositories, or guessed work. " +
          "For a first new Feature in an empty initialized E1 project, use evospec_first_slice_handoff and require an interactive TTY acceptance; chat consent is not Slice acceptance. Never create legacy active work first. Reread canonical context after approval. For other explicit EvoSpec lifecycle work, load the evospec skill and call evospec_context before repository inspection, tests, or lifecycle mutations. Then use evospec_continuation and follow its deterministic adapter directive: SAFE_AUTOMATIC_OPERATION and in-envelope BOUNDED_DELEGATION continue; EXTERNAL_WAIT waits; HUMAN_MATERIAL_DECISION asks exactly Core's decision; STRUCTURAL_DEFECT fails closed; terminal with outcome null completes. Re-read context after every Core mutation, reevaluate STALE_REVISION before any retry, and never edit .evospec manually.",
      })
    })
  },
}
