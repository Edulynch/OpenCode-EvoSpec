(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const elements = Object.freeze({
    loadStatus: byId("load-status"), projectSummary: byId("project-summary"),
    refreshContext: byId("refresh-context"),
    currentWorkSection: byId("current-work-section"), currentWorkTitle: byId("current-work-title"),
    currentWorkStatus: byId("current-work-status"), currentWorkExplanation: byId("current-work-explanation"),
    activeSliceStatus: byId("active-slice-status"), activeSliceContent: byId("active-slice-content"),
    blockedSection: byId("blocked-section"),
    blockedList: byId("blocked-list"), featuresList: byId("features-list"),
    technicalContent: byId("technical-content")
  });

  const technicalOpenState = new Map();
  let activeSliceMessage;
  let activeCriterionStatuses = [];
  let acceptanceStatuses = [];
  let displayedRevision;
  let displayedRepositoryId;
  let signaledRevision = null;
  let signaledRepositoryId;
  let provisionalSlicePanel;
  let acceptanceFeatureTitles = new Map();
  const objectValue = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const arrayValue = (value) => Array.isArray(value) ? value : [];
  const textValue = (value, fallback = "Not available") => value === null || value === undefined || value === "" ? fallback : String(value);
  const repositoryIdentity = (context) => {
    if (!Object.prototype.hasOwnProperty.call(context, "repository_id")) return undefined;
    const value = context.repository_id;
    return typeof value === "string" || value === null ? value : undefined;
  };
  const clear = (element) => element.replaceChildren();
  const create = (tagName, className, value) => {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  };
  const appendEmpty = (element, message) => {
    const empty = create("p", "empty-state", message);
    element.append(empty);
    return empty;
  };
  function humanStatus(work) {
    const state = String(work.operational_state || "").toUpperCase();
    if (state === "DONE") return "Completed";
    if (state === "BLOCKED") return "Blocked";
    if (state === "FROZEN") return "Paused";
    if (state === "WAITING") return "Waiting";
    if (state === "ACTIVE") {
      const lifecycle = arrayValue(work.lifecycle).map((value) => String(value).toUpperCase());
      if (lifecycle.includes("VERIFY")) return "Verifying";
      if (lifecycle.includes("REVIEW")) return "In review";
      if (lifecycle.includes("IMPLEMENT")) return "In progress";
      return "In progress";
    }
    if (work.activation_eligible === true) return "Ready";
    return "Waiting";
  }

  const statusKey = (status) => status.toLowerCase().replace(/\s+/g, "-");
  const statusTone = (status) => {
    const normalized = statusKey(status);
    if (normalized.includes("completed")) return "completed";
    if (normalized.includes("blocked")) return "blocked";
    if (normalized.includes("waiting") || normalized.includes("paused")) return "waiting";
    if (normalized.includes("review") || normalized.includes("verify")) return "in-review";
    return normalized;
  };
  const workIsActive = (work) => String(work.operational_state || "").toUpperCase() === "ACTIVE";
  const matchingCapability = (capabilities, featureId) => capabilities.find((capability) => capability.feature_id === featureId);
  const capabilityTitle = (capability) => textValue(capability.title || capability.capability, "Untitled feature");
  const workTitle = (work, fallback) => textValue(work.title || work.name || fallback || work.id, "Current work");

  function activeWork(queue) {
    const active = objectValue(queue.active);
    return workIsActive(active) ? active : arrayValue(queue.all).map(objectValue).find(workIsActive);
  }

  function currentExplanation(progress, work) {
    return textValue(progress.current_action || progress.resume_point || work?.reason || work?.blocker, "This work is currently in progress.");
  }

  function currentPhase(work, progress) {
    const evidence = [progress.current_work, progress.current_action, progress.focus].filter(Boolean).join(" ").toLowerCase();
    if (evidence.includes("product review")) return "Waiting for product approval";
    if (evidence.includes("final verification")) return "Running final verification";
    if (evidence.includes("verification") || evidence.includes("verify")) return "Verifying";
    if (evidence.includes("review")) return "In review";
    return humanStatus(work || {});
  }

  function renderHeader(context) {
    if (context.dashboard_error) elements.projectSummary.textContent = "The latest project status could not be loaded.";
    else if (context.stale) elements.projectSummary.textContent = "Project context needs an updated assessment.";
    else elements.projectSummary.textContent = "Read-only view of the Feature and Slice agreements available from Core.";
  }

  function renderCurrentWork(queue, progress, capabilities) {
    const work = activeWork(queue);
    elements.currentWorkSection.hidden = !work;
    if (!work) return;
    const capability = work && matchingCapability(capabilities, work.feature_id);
    elements.currentWorkTitle.textContent = workTitle(work, progress.current_work || progress.focus || capability && capabilityTitle(capability));
    const phase = currentPhase(work, progress);
    elements.currentWorkStatus.textContent = phase;
    elements.currentWorkStatus.dataset.status = statusTone(phase);
    elements.currentWorkExplanation.textContent = currentExplanation(progress, work);
  }

  function criterionPresentation(verification) {
    const status = String(objectValue(verification).status || "").toUpperCase();
    if (status === "UNVERIFIED") return ["Pending verification", "waiting"];
    if (status === "FAILED") return ["Verification failed", "failed"];
    if (status === "STALE" || status === "REVALIDATION_REQUIRED") return ["Revalidation required", "stale"];
    if (status === "VERIFIED") {
      return verification.fresh_for_current_workspace === true
        ? ["Verified (current)", "verified"]
        : ["Verified (currentness unavailable)", "waiting"];
    }
    return ["Verification unavailable", "waiting"];
  }

  function outcomePresentation(outcome, accepted, repositoryId) {
    const value = objectValue(outcome);
    const status = String(value.status || "").toUpperCase();
    if (status === "CURRENT") {
      const receipt = objectValue(value.receipt);
      const revision = objectValue(objectValue(accepted).criteria_revision);
      if (
        typeof value.latest_receipt_id === "string" && value.latest_receipt_id.trim()
        && receipt.id === value.latest_receipt_id
        && typeof repositoryId === "string" && repositoryId.trim()
        && receipt.repository_id === repositoryId
        && typeof accepted?.slice_agreement_ref === "string" && accepted.slice_agreement_ref.trim()
        && receipt.slice_agreement_ref === accepted.slice_agreement_ref
        && typeof revision.id === "string" && revision.id.trim()
        && receipt.criteria_revision_id === revision.id
      ) {
        return ["Slice accepted", "accepted", "Current canonical acceptance is recorded."];
      }
      return ["Acceptance status unavailable", "waiting", "The current receipt does not match this repository, Slice, and criteria revision."];
    }
    if (status === "READY") return ["Ready for acceptance", "ready", "Ready for acceptance; this Slice is not yet accepted."];
    if (status === "BLOCKED") return ["Acceptance blocked", "blocked", "Acceptance is blocked by unresolved requirements."];
    if (status === "STALE" || status === "REVALIDATION_REQUIRED") {
      return ["Revalidation required", "stale", "Evidence needs revalidation before acceptance."];
    }
    return ["Acceptance status unavailable", "waiting", "The canonical acceptance status is not available."];
  }

  function renderLegacyAcceptanceIndex(context) {
    clear(elements.activeSliceContent);
    activeSliceMessage = undefined;
    activeCriterionStatuses = [];
    const setStatus = (label, tone) => {
      elements.activeSliceStatus.textContent = label;
      elements.activeSliceStatus.dataset.status = tone;
    };
    const unavailable = (message) => {
      setStatus("Status unavailable", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, message);
    };
    if (context.dashboard_error) {
      unavailable("The active Slice status could not be loaded.");
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(context, "accepted_slice")) {
      unavailable("Active Slice information is not available in this schema.");
      return;
    }
    if (context.accepted_slice === null) {
      setStatus("No active Slice", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, "No active accepted Slice.");
      return;
    }
    const accepted = objectValue(context.accepted_slice);
    const featureTitle = typeof accepted.feature_title === "string" ? accepted.feature_title.trim() : "";
    const sliceTitle = typeof accepted.slice_title === "string" ? accepted.slice_title.trim() : "";
    if (!featureTitle || !sliceTitle || Array.isArray(context.accepted_slice)) {
      unavailable("Active Slice details are not available for this context.");
      return;
    }
    const identity = create("div", "active-slice-identity");
    const feature = create("div");
    feature.append(create("p", "eyebrow", "Feature"), create("h3", "", featureTitle));
    const slice = create("div");
    slice.append(create("p", "eyebrow", "Active Slice"), create("h3", "", sliceTitle));
    identity.append(feature, slice);
    elements.activeSliceContent.append(identity);

    const criteriaRevision = accepted.criteria_revision;
    const revision = objectValue(criteriaRevision);
    if (typeof revision.outcome === "string" && revision.outcome.trim()) {
      elements.activeSliceContent.append(create("p", "acceptance-scope-outcome", `Agreed outcome: ${revision.outcome.trim()}`));
    }
    if (criteriaRevision === null || criteriaRevision === undefined) {
      setStatus("Criteria not recorded", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, "Acceptance criteria are not recorded yet.");
      return;
    }
    if (!Array.isArray(revision.criteria)) {
      unavailable("Acceptance criteria are not available for this context.");
      return;
    }
    if (!revision.criteria.length) {
      setStatus("Criteria not recorded", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, "Acceptance criteria are not registered yet.");
      return;
    }
    const criteria = revision.criteria.map(objectValue);
    if (criteria.some((criterion) => typeof criterion.expected_result !== "string" || !criterion.expected_result.trim())) {
      unavailable("Acceptance criteria are not available for this context.");
      return;
    }
    const [outcomeLabel, outcomeTone, outcomeMessage] = outcomePresentation(context.slice_outcome, accepted, context.repository_id);
    setStatus(outcomeLabel, outcomeTone);
    activeSliceMessage = create("p", "active-slice-note", outcomeMessage);
    elements.activeSliceContent.append(activeSliceMessage);
    elements.activeSliceContent.append(create("h3", "active-slice-criteria-heading", "Acceptance criteria"));
    const verificationRecords = Array.isArray(context.criterion_verification)
      ? context.criterion_verification.map(objectValue)
      : [];
    const revisionId = typeof revision.id === "string" && revision.id ? revision.id : null;
    const list = create("ul", "active-slice-criteria");
    list.setAttribute("aria-label", "Current acceptance criteria");
    criteria.forEach((criterion) => {
      const matches = revisionId === null ? [] : verificationRecords.filter((item) =>
        item.criterion_id === criterion.id && item.criteria_revision_id === revisionId
      );
      const [label, tone] = criterionPresentation(matches.length === 1 ? matches[0] : null);
      const row = create("li", "active-slice-criterion");
      const status = create("span", "status-label", label);
      status.dataset.status = tone;
      activeCriterionStatuses.push(status);
      row.append(create("span", "active-slice-criterion-result", criterion.expected_result.trim()), status);
      list.append(row);
    });
    elements.activeSliceContent.append(list);
  }

  function acceptanceInventory(context) {
    if (!Object.prototype.hasOwnProperty.call(context, "slice_acceptance_inventory")) {
      renderLegacyAcceptanceIndex(context);
      return;
    }
    clear(elements.activeSliceContent);
    activeSliceMessage = undefined;
    activeCriterionStatuses = [];
    acceptanceStatuses = [elements.activeSliceStatus];
    const setStatus = (label, tone) => {
      elements.activeSliceStatus.textContent = label;
      elements.activeSliceStatus.dataset.status = tone;
    };
    const unavailable = (message) => {
      setStatus("Status unavailable", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, message);
    };
    if (context.dashboard_error) {
      unavailable("The accepted Slice inventory could not be loaded.");
      return;
    }
    const inventory = objectValue(context.slice_acceptance_inventory);
    if (
      inventory.source !== "CORE_AGREEMENTS"
      || !Number.isSafeInteger(inventory.total) || inventory.total < 0
      || typeof inventory.complete !== "boolean"
      || typeof inventory.truncated !== "boolean"
      || inventory.history_scope !== "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE"
      || !Array.isArray(inventory.items)
      || inventory.items.length > inventory.total
      || inventory.truncated !== (inventory.total > inventory.items.length)
      || (inventory.truncated && inventory.complete)
    ) {
      unavailable("The accepted Slice inventory is not available in a recognized Core projection.");
      return;
    }
    const items = inventory.items.map(objectValue);
    if (
      items.length
      && (typeof context.repository_id !== "string" || !context.repository_id.trim())
    ) {
      unavailable("Repository identity is unavailable for grouping accepted Feature/Slice scopes.");
      return;
    }
    const activeItems = items.filter((item) => item.scope_status === "ACTIVE");
    const acceptedExists = Object.prototype.hasOwnProperty.call(context, "accepted_slice");
    if (!acceptedExists || activeItems.length > 1) {
      unavailable("Active Slice authority is missing or ambiguous.");
      return;
    }
    let activeItem = activeItems[0];
    const accepted = context.accepted_slice;
    if (accepted === null) {
      if (activeItem) {
        unavailable("The active Slice does not match the canonical agreement inventory.");
        return;
      }
      setStatus("No active Slice", "waiting");
      activeSliceMessage = appendEmpty(elements.activeSliceContent, "No active accepted Slice.");
    } else {
      const current = objectValue(accepted);
      if (
        !activeItem
        || current.slice_agreement_ref !== activeItem.slice_agreement_ref
        || current.feature_agreement_ref !== activeItem.feature_agreement_ref
        || activeItem.authority_status !== "CANONICAL"
      ) {
        unavailable("Active Slice details do not match one canonical Feature/Slice agreement.");
        return;
      }
      if (activeItem.criteria_status === "RECORDED") {
        const projected = objectValue(activeItem.criteria_revision);
        const acceptedRevision = objectValue(current.criteria_revision);
        if (
          !Array.isArray(projected.criteria) || !projected.criteria.length
          || typeof projected.id !== "string" || projected.id !== acceptedRevision.id
          || !Array.isArray(acceptedRevision.criteria)
          || JSON.stringify(projected) !== JSON.stringify(acceptedRevision)
        ) {
          unavailable("The active Slice criteria head is unavailable or inconsistent.");
          return;
        }
      } else if (
        activeItem.criteria_status !== "NOT_RECORDED"
        || current.criteria_revision !== null
      ) {
        unavailable("The active Slice criteria head is unavailable or inconsistent.");
        return;
      }
      const criteria = objectValue(activeItem.criteria_revision);
      const criteriaRows = arrayValue(criteria.criteria).map(objectValue);
      if (activeItem.criteria_status === "RECORDED" && criteriaRows.some((criterion) =>
        typeof criterion.expected_result !== "string" || !criterion.expected_result.trim()
      )) {
        unavailable("The active Slice criteria are malformed.");
        return;
      }
      if (activeItem.criteria_status === "NOT_RECORDED") {
        setStatus("Criteria not recorded", "waiting");
        activeSliceMessage = appendEmpty(elements.activeSliceContent, "Acceptance criteria are not recorded yet.");
      } else {
        const [label, tone, message] = activeOutcomePresentation(context, activeItem);
        setStatus(label, tone);
        activeSliceMessage = create("p", "active-slice-note", message);
        elements.activeSliceContent.append(activeSliceMessage);
      }
    }

    const note = create(
      "p",
      "acceptance-coverage-note",
      `Showing ${items.length} of ${inventory.total} Slice agreements in Core agreement records. Archived/history coverage is not exhaustive.`
    );
    elements.activeSliceContent.append(note);
    if (inventory.truncated) {
      elements.activeSliceContent.append(create("p", "empty-state", "The displayed list is truncated; additional accepted Slice agreements are not shown."));
    } else if (!inventory.complete) {
      elements.activeSliceContent.append(create("p", "empty-state", "Some canonical agreement or criteria records are unresolved; acceptance is not inferred."));
    }
    const groups = new Map();
    const unassociated = [];
    items.forEach((item) => {
      const scope = String(item.scope_status || "").toUpperCase();
      const knownScope = ["ACTIVE", "PARKED", "QUEUED", "PRESERVED", "HISTORICAL", "INACTIVE"].includes(scope);
      if (
        item.authority_status !== "CANONICAL" || !knownScope
        || typeof item.feature_id !== "string" || !item.feature_id
        || typeof item.feature_title !== "string" || !item.feature_title.trim()
        || typeof item.feature_agreement_ref !== "string" || !item.feature_agreement_ref
        || typeof item.slice_title !== "string" || !item.slice_title.trim()
      ) {
        unassociated.push(item);
        return;
      }
      const key = `${String(context.repository_id || "")}\u0000${item.feature_id}`;
      if (!groups.has(key)) groups.set(key, { featureId: item.feature_id, title: item.feature_title, items: [] });
      groups.get(key).items.push(item);
    });

      [...groups.values()].forEach((group) => {
        const article = create("article", "acceptance-feature-group");
        article.append(create("p", "eyebrow", "Feature"), create("h3", "acceptance-feature-title", group.title));
        group.items.forEach((item) => appendAcceptanceSlice(article, item, item === activeItem, context));
        elements.activeSliceContent.append(article);
      });
    groups.forEach((group, key) => acceptanceFeatureTitles.set(key, group.title));
    if (unassociated.length) {
      const article = create("article", "acceptance-unassociated");
      article.append(create("h3", "", "Unassociated Slice records"));
      article.append(create("p", "empty-state", "These records could not be matched to a canonical Feature; their criteria and acceptance are not inferred."));
      unassociated.forEach((item) => {
        const row = create("p", "empty-state", textValue(item.slice_title, "Slice agreement details unavailable."));
        article.append(row);
      });
      elements.activeSliceContent.append(article);
    }
  }

  function activeOutcomePresentation(context, item) {
    const outcome = objectValue(context.slice_outcome);
    if (String(outcome.status || "").toUpperCase() !== "CURRENT") {
      return outcomePresentation(outcome);
    }
    const receipt = objectValue(outcome.receipt);
    if (
      typeof outcome.latest_receipt_id === "string" && outcome.latest_receipt_id.trim()
      && receipt.id === outcome.latest_receipt_id
      && receipt.repository_id === context.repository_id
      && receipt.slice_agreement_ref === item.slice_agreement_ref
      && receipt.criteria_revision_id === objectValue(item.criteria_revision).id
    ) {
      return ["Slice accepted", "accepted", "Current canonical acceptance is recorded."];
    }
    return ["Acceptance status unavailable", "waiting", "The canonical acceptance receipt is unavailable or belongs to another Slice or criteria revision."];
  }

  function historicalAcceptanceBasis(item, context) {
    const acceptance = objectValue(item.historical_acceptance);
    const receipt = objectValue(acceptance.receipt);
    const revision = objectValue(acceptance.criteria_revision);
    const criteria = arrayValue(revision.criteria).map(objectValue);
    const evidence = arrayValue(acceptance.criterion_evidence).map(objectValue);
    const evidenceIds = arrayValue(receipt.evidence_receipt_ids);
    const criterionIds = criteria.map((criterion) => criterion.id);
    const requestScoped = revision.source === "REQUEST_SCOPED_OBJECTIVE";
    if (
      !Number.isSafeInteger(context.revision) || context.revision < 0
      || typeof context.repository_id !== "string" || !context.repository_id.trim()
      || typeof item.slice_agreement_ref !== "string" || !item.slice_agreement_ref
      || typeof receipt.id !== "string" || !receipt.id
      || receipt.repository_id !== context.repository_id
      || receipt.slice_agreement_ref !== item.slice_agreement_ref
      || typeof revision.id !== "string" || !revision.id
      || revision.id !== receipt.criteria_revision_id
      || revision.repository_id !== context.repository_id
      || revision.slice_agreement_ref !== item.slice_agreement_ref
      || !["INTERACTIVE_TERMINAL", "INTERACTIVE_TERMINAL_DECISION", "REQUEST_SCOPED_OBJECTIVE"].includes(revision.source)
      || receipt.source !== (requestScoped ? "REQUEST_SCOPED_OBJECTIVE" : "CORE_OBJECTIVE_ACCEPTANCE")
      || criteria.length === 0 || evidence.length !== criteria.length
      || evidenceIds.length !== evidence.length
      || new Set(evidenceIds).size !== evidenceIds.length
      || new Set(criterionIds).size !== criterionIds.length
      || evidence.some((proof) =>
        typeof proof.id !== "string" || !proof.id
        || !evidenceIds.includes(proof.id)
        || proof.slice_agreement_ref !== item.slice_agreement_ref
        || proof.criteria_revision_id !== revision.id
        || typeof proof.criterion_id !== "string"
        || !criterionIds.includes(proof.criterion_id)
        || proof.exit_code !== 0
        || typeof proof.test_kind !== "string" || !proof.test_kind.trim()
        || typeof proof.test_target !== "string" || !proof.test_target.trim()
        || typeof proof.output_sha256 !== "string"
        || !/^[0-9a-f]{64}$/.test(proof.output_sha256)
        || !Number.isSafeInteger(proof.source_revision) || proof.source_revision < 0
        || !Number.isSafeInteger(proof.recorded_revision)
        || proof.recorded_revision !== proof.source_revision + 1
        || proof.recorded_revision > context.revision
        || (requestScoped
          ? proof.source_revision !== receipt.source_revision
            || proof.recorded_revision !== receipt.recorded_revision
          : proof.recorded_revision > receipt.source_revision)
      )
      || new Set(evidence.map((proof) => proof.id)).size !== evidence.length
      || new Set(evidence.map((proof) => proof.criterion_id)).size !== criterionIds.length
      || criterionIds.some((id) => evidence.filter((proof) => proof.criterion_id === id).length !== 1)
      || evidenceIds.some((id) => evidence.filter((proof) => proof.id === id).length !== 1)
      || !Number.isSafeInteger(receipt.source_revision) || receipt.source_revision < 0
      || !Number.isSafeInteger(receipt.recorded_revision)
      || receipt.recorded_revision !== receipt.source_revision + 1
      || receipt.recorded_revision > context.revision
      || typeof receipt.input_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(receipt.input_fingerprint)
      || typeof receipt.input_policy_id !== "string" || !receipt.input_policy_id.trim()
      || typeof receipt.input_policy_digest !== "string" || !/^[0-9a-f]{64}$/.test(receipt.input_policy_digest)
      || typeof receipt.admission_policy_id !== "string" || !receipt.admission_policy_id.trim()
      || typeof receipt.admission_policy_digest !== "string" || !/^[0-9a-f]{64}$/.test(receipt.admission_policy_digest)
      || typeof receipt.relevant_drift_digest !== "string" || !/^[0-9a-f]{64}$/.test(receipt.relevant_drift_digest)
      || typeof receipt.basis !== "string" || !receipt.basis.trim()
      || typeof receipt.source !== "string" || !receipt.source.trim()
      || (receipt.coverage_contract_digest !== null
        && (typeof receipt.coverage_contract_digest !== "string"
          || !/^[0-9a-f]{64}$/.test(receipt.coverage_contract_digest)))
      || typeof revision.outcome !== "string" || !revision.outcome.trim()
      || typeof revision.example !== "string" || !revision.example.trim()
      || typeof revision.source !== "string" || !revision.source.trim()
      || typeof revision.source_reference !== "string" || !revision.source_reference.trim()
      || criteria.some((criterion) =>
        typeof criterion.id !== "string" || !criterion.id
        || typeof criterion.expected_result !== "string" || !criterion.expected_result.trim()
        || typeof criterion.expected_evidence !== "string" || !criterion.expected_evidence.trim()
      )
    ) return null;
    return { receipt, revision, criteria, evidence };
  }

  function appendHistoricalAcceptance(slice, item, isActive, context) {
    const status = item.historical_acceptance_status;
    if (status === "NOT_RECORDED" && item.historical_receipt !== true) return;
    if (status !== "ACCEPTED_PREVIOUSLY") {
      slice.append(create(
        "p",
        "acceptance-history-unavailable",
        status === "PARTIAL"
          ? "Historical acceptance evidence is partial; no prior acceptance is inferred."
          : "Historical acceptance evidence is unavailable; no prior acceptance is inferred.",
      ));
      return;
    }
    const basis = historicalAcceptanceBasis(item, context);
    if (!basis) {
      slice.append(create(
        "p",
        "acceptance-history-unavailable",
        "Historical acceptance evidence is unavailable; no prior acceptance is inferred.",
      ));
      return;
    }
    const currentOutcome = objectValue(context.slice_outcome);
    if (
      isActive && String(currentOutcome.status || "").toUpperCase() === "CURRENT"
      && currentOutcome.latest_receipt_id === basis.receipt.id
    ) return;

    const statusLabel = create(
      "span",
      "status-label",
      "Accepted previously — current validity not evaluated",
    );
    statusLabel.dataset.status = "waiting";
    acceptanceStatuses.push(statusLabel);
    slice.append(create("p", "eyebrow", "Historical outcome"), statusLabel);
    slice.append(create(
      "h5",
      "active-slice-criteria-heading",
      "Previously accepted criteria (current validity not evaluated)",
    ));
    const list = create("ul", "acceptance-history-criteria");
    list.setAttribute("aria-label", `${item.slice_title} previously accepted criteria`);
    basis.criteria.forEach((criterion) => {
      const row = create("li", "active-slice-criterion");
      const acceptedPreviously = create("span", "status-label", "Accepted previously");
      acceptedPreviously.dataset.status = "waiting";
      acceptanceStatuses.push(acceptedPreviously);
      row.append(
        create("span", "active-slice-criterion-result", criterion.expected_result.trim()),
        acceptedPreviously,
      );
      list.append(row);
    });
    slice.append(list);

    const details = create("details", "acceptance-history-details");
    details.append(create("summary", "", "Technical provenance"));
    details.append(
      create("p", "", `Repository: ${basis.receipt.repository_id}`),
      create("p", "", `Slice agreement: ${basis.receipt.slice_agreement_ref}`),
      create("p", "", `Outcome receipt: ${basis.receipt.id}`),
      create("p", "", `Criteria revision: ${basis.revision.id}`),
      create("p", "", `Previously agreed outcome: ${basis.revision.outcome}`),
      create("p", "", `Criteria source: ${textValue(basis.revision.source)}`),
      create("p", "", `Criteria source reference: ${textValue(basis.revision.source_reference)}`),
      create("p", "", `Outcome basis: ${basis.receipt.basis}`),
      create("p", "", `Outcome source: ${basis.receipt.source}`),
      create("p", "", `Source revision: ${basis.receipt.source_revision}`),
      create("p", "", `Recorded revision: ${basis.receipt.recorded_revision}`),
      create("p", "", `Input fingerprint: ${basis.receipt.input_fingerprint}`),
      create("p", "", `Input policy: ${basis.receipt.input_policy_id} (${basis.receipt.input_policy_digest})`),
      create("p", "", `Admission policy: ${basis.receipt.admission_policy_id} (${basis.receipt.admission_policy_digest})`),
      create("p", "", `Relevant drift digest: ${basis.receipt.relevant_drift_digest}`),
    );
    if (basis.receipt.coverage_contract_digest !== null) {
      details.append(create("p", "", `Coverage contract digest: ${basis.receipt.coverage_contract_digest}`));
    }
    basis.evidence.forEach((proof) => details.append(create(
      "p",
      "",
      `${proof.criterion_id}: ${basis.criteria.find((criterion) => criterion.id === proof.criterion_id).expected_evidence}; ${proof.test_kind}:${proof.test_target}; receipt ${proof.id}; exit ${proof.exit_code}; SHA-256 ${proof.output_sha256}`,
    )));
    slice.append(details);
  }

  function appendAcceptanceSlice(container, item, isActive, context) {
    const slice = create("section", "acceptance-slice");
    slice.append(create("h4", "acceptance-slice-title", item.slice_title));
    const revision = objectValue(item.criteria_revision);
    const scopeLabels = {
      ACTIVE: "Active",
      PARKED: item.operational_state === "BLOCKED" ? "Blocked (parked)" : item.operational_state === "FROZEN" ? "Paused (parked)" : "Parked",
      QUEUED: "Queued",
      PRESERVED: "Preserved primary work (inactive)",
      HISTORICAL: "Historical context (not current)",
      INACTIVE: "Inactive (agreement only)",
    };
    slice.append(create("p", "eyebrow", "Slice status"));
    const scopeLabel = create("span", "status-label", scopeLabels[item.scope_status]);
    scopeLabel.dataset.status = item.scope_status === "ACTIVE" ? "in-review" : item.operational_state === "BLOCKED" ? "blocked" : "waiting";
    acceptanceStatuses.push(scopeLabel);
    slice.append(scopeLabel);
    if (typeof revision.outcome === "string" && revision.outcome.trim()) {
      slice.append(create("p", "acceptance-scope-outcome", `Agreed outcome: ${revision.outcome.trim()}`));
    }
    const acceptance = isActive && item.criteria_status === "RECORDED"
      ? activeOutcomePresentation(context, item)
      : isActive
        ? ["Acceptance status unavailable", "waiting", "Current outcome acceptance requires a recorded criteria revision."]
        : [
          item.scope_status === "PARKED"
            ? "Parked — current acceptance not evaluated"
            : "Current acceptance not evaluated",
          "waiting",
          "This inactive Slice has no current acceptance evaluation.",
        ];
    slice.append(create("p", "eyebrow", "Outcome acceptance"));
    const acceptanceStatus = create("span", "status-label", acceptance[0]);
    acceptanceStatus.dataset.status = acceptance[1];
    acceptanceStatuses.push(acceptanceStatus);
    slice.append(acceptanceStatus);
    if (item.criteria_status === "NOT_RECORDED") {
      slice.append(create("p", "empty-state", "Acceptance criteria are not recorded for this Slice."));
    } else if (item.criteria_status !== "RECORDED") {
      slice.append(create("p", "empty-state", "Acceptance criteria are unavailable; no current criteria are inferred."));
    } else {
      const revision = objectValue(item.criteria_revision);
      const criteria = arrayValue(revision.criteria).map(objectValue);
      if (!criteria.length || criteria.some((criterion) =>
        typeof criterion.expected_result !== "string" || !criterion.expected_result.trim()
      )) {
        slice.append(create("p", "empty-state", "Acceptance criteria are unavailable; this record is malformed."));
      } else {
        slice.append(create("h5", "active-slice-criteria-heading", "Agreed acceptance criteria"));
        const verificationRecords = isActive ? arrayValue(context.criterion_verification).map(objectValue) : [];
        const list = create("ul", "active-slice-criteria");
        list.setAttribute("aria-label", `${item.slice_title} criterion verification`);
        criteria.forEach((criterion) => {
          const matches = isActive
            ? verificationRecords.filter((record) =>
              record.criterion_id === criterion.id && record.criteria_revision_id === revision.id
            )
            : [];
          const [label, tone] = isActive
            ? criterionPresentation(matches.length === 1 ? matches[0] : null)
            : ["Not evaluated for this inactive Slice", "waiting"];
          const row = create("li", "active-slice-criterion");
          const status = create("span", "status-label", label);
          status.dataset.status = tone;
          acceptanceStatuses.push(status);
          if (isActive) activeCriterionStatuses.push(status);
          row.append(create("span", "active-slice-criterion-result", criterion.expected_result.trim()), status);
          list.append(row);
        });
        slice.append(list);
      }
    }
    appendHistoricalAcceptance(slice, item, isActive, context);
    container.append(slice);
  }

  function renderAcceptanceIndex(context) {
    provisionalSlicePanel = undefined;
    acceptanceFeatureTitles = new Map();
    acceptanceStatuses = [];
    acceptanceInventory(context);
  }

  function provisionalInventoryMessage(message) {
    provisionalSlicePanel.append(create("p", "empty-state", message));
  }

  function renderProvisionalItem(container, item) {
    const proposal = create("article", "provisional-slice");
    proposal.append(create("h4", "provisional-slice-title", item.title.trim()));
    const status = create("span", "status-label", "Provisional");
    status.dataset.status = "waiting";
    proposal.append(status);
    const provenance = item.provenance === "USER_EXPRESSED"
      ? "User-expressed — not approval"
      : "Suggested by agent";
    proposal.append(create("p", "provisional-slice-provenance", provenance));
    proposal.append(create("h5", "provisional-criteria-heading", "Expected results"));
    const results = create("ul");
    item.criteria.forEach((criterion) => results.append(
      create("li", "provisional-criterion-result", criterion.expected_result.trim())
    ));
    proposal.append(results);
    container.append(proposal);
  }

  function renderProvisionalInventory(context) {
    provisionalSlicePanel = create("section", "active-slice-section provisional-slice-panel");
    provisionalSlicePanel.append(create("h3", "provisional-slice-heading", "Provisional proposals"));
    provisionalSlicePanel.append(create(
      "p",
      "active-slice-note",
      "Read-only proposals are not agreed Slices and do not change acceptance.",
    ));
    elements.activeSliceContent.append(provisionalSlicePanel);

    if (context.dashboard_error) {
      provisionalInventoryMessage("Proposals could not be loaded with the project context.");
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(context, "provisional_slice_inventory")) {
      provisionalInventoryMessage("Proposals are not exposed by this older context schema.");
      return;
    }

    const inventory = objectValue(context.provisional_slice_inventory);
    const unavailable = () => provisionalInventoryMessage(
      "The provisional proposal inventory is not available in a recognized Core projection.",
    );
    if (
      inventory.source !== "CORE_PROVISIONAL_SLICE_HEADS"
      || !["AVAILABLE", "UNINITIALIZED", "UNAVAILABLE"].includes(inventory.availability)
      || !Number.isSafeInteger(inventory.total) || inventory.total < 0
      || typeof inventory.complete !== "boolean"
      || typeof inventory.truncated !== "boolean"
      || !Array.isArray(inventory.items)
      || inventory.items.length > 32
      || inventory.items.length > inventory.total
    ) {
      unavailable();
      return;
    }
    if (inventory.availability !== "AVAILABLE") {
      if (
        inventory.total !== 0 || inventory.complete !== false
        || inventory.truncated !== false || inventory.items.length !== 0
      ) {
        unavailable();
        return;
      }
      provisionalInventoryMessage(
        inventory.availability === "UNINITIALIZED"
          ? "Provisional proposal storage is not initialized."
          : "Provisional proposals are unavailable; no proposal head is inferred.",
      );
      return;
    }

    const truncated = inventory.total > 32;
    if (
      inventory.truncated !== truncated
      || inventory.complete === truncated
      || inventory.items.length !== Math.min(inventory.total, 32)
    ) {
      unavailable();
      return;
    }
    if (!inventory.items.length) {
      provisionalInventoryMessage("No provisional proposals are recorded.");
      return;
    }
    const repositoryId = context.repository_id;
    if (typeof repositoryId !== "string" || !repositoryId.trim()) {
      provisionalInventoryMessage("Repository identity is unavailable; provisional proposals are hidden.");
      return;
    }

    const proposals = [];
    const proposalIds = new Set();
    let omitted = false;
    inventory.items.forEach((value) => {
      const item = objectValue(value);
      const head = objectValue(item.head);
      const criteria = Array.isArray(item.criteria) ? item.criteria.map(objectValue) : [];
      const criterionIds = criteria.map((criterion) => criterion.id);
      const valid = typeof item.proposal_id === "string" && item.proposal_id.trim()
        && /^ps-[0-9a-f]{64}$/.test(item.proposal_id)
        && item.repository_id === repositoryId
        && typeof item.feature_id === "string" && item.feature_id.trim()
        && typeof item.title === "string" && item.title.trim()
        && item.status === "PROVISIONAL"
        && ["AGENT_SUGGESTION", "USER_EXPRESSED"].includes(item.provenance)
        && typeof head.revision_id === "string" && head.revision_id.trim()
        && Number.isSafeInteger(head.revision) && head.revision >= 1
        && head.revision_id === `${item.proposal_id}-r${head.revision}`
        && typeof head.digest === "string" && /^[0-9a-f]{64}$/.test(head.digest)
        && criteria.length > 0
        && criteria.every((criterion) =>
          typeof criterion.id === "string" && criterion.id.trim()
          && typeof criterion.expected_result === "string" && criterion.expected_result.trim()
          && typeof criterion.expected_evidence === "string" && criterion.expected_evidence.trim()
        )
        && new Set(criterionIds).size === criterionIds.length
        && !proposalIds.has(item.proposal_id);
      if (!valid) {
        omitted = true;
        return;
      }
      proposalIds.add(item.proposal_id);
      proposals.push({ ...item, criteria });
    });

    if (inventory.truncated) {
      provisionalInventoryMessage("This proposal inventory is truncated; additional current heads are not shown.");
    }
    if (omitted) {
      provisionalInventoryMessage("Some provisional proposals were foreign or malformed and could not be displayed.");
    }
    const matched = new Map();
    const unmatched = [];
    proposals.forEach((item) => {
      const key = `${item.repository_id}\u0000${item.feature_id}`;
      const featureTitle = acceptanceFeatureTitles.get(key);
      if (!featureTitle) {
        unmatched.push(item);
        return;
      }
      if (!matched.has(key)) matched.set(key, { title: featureTitle, items: [] });
      matched.get(key).items.push(item);
    });
    matched.forEach((group) => {
      const article = create("article", "acceptance-feature-group provisional-feature-group");
      article.append(create("p", "eyebrow", "Proposals for agreed Feature"));
      article.append(create("h4", "acceptance-feature-title provisional-feature-title", group.title));
      group.items.forEach((item) => renderProvisionalItem(article, item));
      provisionalSlicePanel.append(article);
    });
    if (unmatched.length) {
      const article = create("article", "acceptance-feature-group provisional-feature-group");
      article.append(create("p", "eyebrow", "Unmatched Feature proposals"));
      article.append(create(
        "p",
        "active-slice-note",
        "No canonical Feature association is available; proposals are not matched by title.",
      ));
      unmatched.forEach((item) => renderProvisionalItem(article, item));
      provisionalSlicePanel.append(article);
    }
    if (!proposals.length && !omitted) {
      provisionalInventoryMessage("No valid provisional proposals are available.");
    }
  }

  function hideProvisionalSliceHeads() {
    if (!provisionalSlicePanel) return;
    const panel = provisionalSlicePanel;
    provisionalSlicePanel = undefined;
    panel.replaceChildren(create(
      "p",
      "empty-state",
      "Provisional proposal heads are hidden while the current project context is being checked.",
    ));
  }

  function markAcceptanceUpdating() {
    elements.activeSliceStatus.textContent = "Updating";
    elements.activeSliceStatus.dataset.status = "waiting";
    if (activeSliceMessage) activeSliceMessage.textContent = "A newer project revision is being checked.";
    (acceptanceStatuses.length ? acceptanceStatuses : activeCriterionStatuses).forEach((status) => {
      status.textContent = "Updating";
      status.dataset.status = "waiting";
    });
    hideProvisionalSliceHeads();
  }

  function blockedRecords(queue) {
    const records = arrayValue(queue.blocked).map(objectValue);
    return records.length ? records : arrayValue(queue.all).map(objectValue).filter((work) => humanStatus(work) === "Blocked");
  }

  function renderBlocked(queue, capabilities) {
    clear(elements.blockedList);
    const blocked = blockedRecords(queue);
    elements.blockedSection.hidden = !blocked.length;
    blocked.forEach((work) => {
      const capability = matchingCapability(capabilities, work.feature_id);
      const row = create("article", "blocked-item");
      const copy = create("div");
       copy.append(create("strong", "", capability ? capabilityTitle(capability) : workTitle(work, "Blocked work")));
      copy.append(create("p", "", textValue(work.reason || work.blocker, "This work is blocked and needs attention.")));
      const status = create("span", "status-label", "Blocked");
      status.dataset.status = "blocked";
      row.append(copy, status);
      elements.blockedList.append(row);
    });
  }

  function renderFeatures(context) {
    clear(elements.featuresList);
    const capabilities = arrayValue(context.accepted_capabilities).map(objectValue);
    if (!capabilities.length) {
      appendEmpty(elements.featuresList, "No published capabilities are available.");
      return;
    }
    capabilities.forEach((capability) => {
      const article = create("article", "feature-row");
      if (typeof capability.feature_id === "string") article.dataset.featureId = capability.feature_id;
      article.append(create("span", "feature-title", capabilityTitle(capability)));
      elements.featuresList.append(article);
    });
  }

  function appendTechnicalRecord(container, id, title, lines) {
    const section = create("details", "technical-record");
    section.id = id;
    section.open = technicalOpenState.get(id) === true;
    section.append(create("summary", "", title));
    const list = create("ul", "technical-list");
    lines.forEach(([label, value]) => {
      const item = create("li");
      item.append(create("strong", "", `${label}: `), document.createTextNode(textValue(value)));
      list.append(item);
    });
    section.append(list);
    section.addEventListener("toggle", () => technicalOpenState.set(id, section.open));
    container.append(section);
  }

  function appendTechnicalRaw(container, contextPayload) {
    const section = create("details", "technical-record");
    section.id = "technical-raw";
    section.open = technicalOpenState.get(section.id) === true;
    section.append(create("summary", "", "Raw JSON"));
    const output = create("pre");
    output.textContent = JSON.stringify(contextPayload, null, 2);
    section.append(output);
    section.addEventListener("toggle", () => technicalOpenState.set(section.id, section.open));
    container.append(section);
  }

  function renderTechnical(contextPayload, context, queue) {
    clear(elements.technicalContent);
    appendTechnicalRecord(elements.technicalContent, "technical-context", "Context", [
      ["Revision", context.revision], ["Initialization", context.initialization_state],
      ["Feature", context.feature_id], ["Change", context.change_id]
    ]);
    appendTechnicalRecord(elements.technicalContent, "technical-workflow", "Workflow", [
      ["Lifecycle", arrayValue(context.lifecycle).join(", ")], ["Queue", `${textValue(queue.total, "0")} work items`],
      ["Queue complete", queue.complete === true ? "Yes" : "No"], ["Current action", objectValue(context.progress).current_action]
    ]);
    const works = arrayValue(queue.all).map(objectValue);
    appendTechnicalRecord(elements.technicalContent, "technical-work-items", "Work items", works.map((work) => [
      textValue(work.id, "Unknown work"), `${humanStatus(work)}${work.reason || work.blocker ? `: ${textValue(work.reason || work.blocker)}` : ""}`
    ]));
    const slices = arrayValue(objectValue(context.slice_acceptance_inventory).items).map(objectValue);
    if (slices.length) appendTechnicalRecord(
      elements.technicalContent, "technical-slice-agreements", "Slice agreement revisions",
      slices.map((item) => {
        const revision = objectValue(item.criteria_revision);
        return [
          textValue(item.slice_title, "Slice"),
          `Feature agreement ${textValue(item.feature_agreement_ref)}; Slice agreement ${textValue(item.slice_agreement_ref)}; criteria revision ${textValue(revision.id, "not recorded")}; provenance ${textValue(item.provenance)}; operational state ${textValue(item.operational_state)}; agreed outcome ${textValue(revision.outcome)}`,
        ];
      })
    );
    const assurance = [];
    if (arrayValue(queue.relations).length) assurance.push(["Relations", `${arrayValue(queue.relations).length} recorded`]);
    if (arrayValue(context.active_locks).length) assurance.push(["Active locks", `${arrayValue(context.active_locks).length} active`]);
    if (Object.keys(objectValue(context.decision_state)).length) assurance.push(["Decision state", "Recorded"]);
    if (assurance.length) appendTechnicalRecord(elements.technicalContent, "technical-assurance", "Relations and assurance", assurance);
    const guidance = arrayValue(context.guidance).map(objectValue);
    if (guidance.length) appendTechnicalRecord(elements.technicalContent, "technical-guidance", "Guidance", guidance.map((item) => [
      textValue(item.action, "Guidance"), textValue(item.message || item.blocker)
    ]));
    appendTechnicalRaw(elements.technicalContent, contextPayload);
  }

  function render(contextPayload) {
    const context = objectValue(contextPayload);
    displayedRevision = Number.isSafeInteger(context.revision) ? context.revision : null;
    displayedRepositoryId = repositoryIdentity(context);
    if (
      signaledRevision !== null
      && (signaledRepositoryId === undefined || displayedRepositoryId === signaledRepositoryId)
      && displayedRevision !== null && displayedRevision >= signaledRevision
    ) {
      signaledRevision = null;
      signaledRepositoryId = undefined;
    }
    const queue = objectValue(context.work_queue);
    const progress = objectValue(context.progress);
    const capabilities = arrayValue(context.accepted_capabilities).map(objectValue);
    renderHeader(context);
    renderAcceptanceIndex(context);
    renderProvisionalInventory(context);
    renderCurrentWork(queue, progress, capabilities);
    renderBlocked(queue, capabilities);
    renderFeatures(context);
    renderTechnical(contextPayload, context, queue);
  }

  const fallbackIntervalMs = 5000;
  let eventSource;
  let fallbackTimer;
  let contextRequestInFlight = false;
  let contextRequestQueued = false;
  let pageHidden = false;
  const setLoadStatus = (status) => { elements.loadStatus.textContent = status; };
  const stopFallback = () => {
    if (fallbackTimer === undefined) return;
    clearInterval(fallbackTimer);
    fallbackTimer = undefined;
  };
  const startFallback = () => {
    if (fallbackTimer === undefined) fallbackTimer = setInterval(loadContext, fallbackIntervalMs);
  };

  async function loadContext() {
    if (pageHidden) return;
    if (contextRequestInFlight) {
      contextRequestQueued = true;
      return;
    }
    contextRequestInFlight = true;
    do {
      contextRequestQueued = false;
      try {
        const response = await fetch("/api/context", { headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`Context request failed with status ${response.status}.`);
        const context = await response.json();
        const current = objectValue(context);
        const revision = current.revision;
        const repositoryId = repositoryIdentity(current);
        const signaledRootMismatch = signaledRepositoryId !== undefined && repositoryId !== signaledRepositoryId;
        const olderThanSignal = signaledRevision !== null && !signaledRootMismatch
          && Number.isSafeInteger(revision) && revision < signaledRevision;
        const olderThanDisplayed = displayedRepositoryId !== undefined && repositoryId === displayedRepositoryId
          && displayedRevision !== null && Number.isSafeInteger(revision) && revision < displayedRevision;
        if (signaledRootMismatch || olderThanSignal || olderThanDisplayed) {
          contextRequestQueued = true;
        }
        if (!contextRequestQueued && !pageHidden) render(context);
      } catch (error) {
        if (!contextRequestQueued && !pageHidden) {
          render({ dashboard_error: textValue(error && error.message, "GET /api/context failed."), initialization_state: "UNAVAILABLE", work_queue: {}, progress: {} });
        }
      }
    } while (contextRequestQueued && !pageHidden);
    contextRequestInFlight = false;
  }

  function connectEvents() {
    if (typeof EventSource === "undefined") {
      setLoadStatus("Polling");
      startFallback();
      return;
    }
    eventSource = new EventSource("/api/events");
    eventSource.addEventListener("open", () => {
      setLoadStatus("Live");
      stopFallback();
      loadContext();
    });
    eventSource.addEventListener("revision", (event) => {
      try {
        const data = objectValue(JSON.parse(event.data));
        const revision = data.revision;
        const repositoryId = repositoryIdentity(data);
        const rootChanged = repositoryId !== undefined && repositoryId !== displayedRepositoryId;
        const revisionChanged = Number.isSafeInteger(revision)
          && (displayedRevision === undefined || displayedRevision === null || revision > displayedRevision);
        if (Number.isSafeInteger(revision) && (rootChanged || revisionChanged)) {
          if (rootChanged) {
            signaledRepositoryId = repositoryId;
            signaledRevision = revision;
          } else {
            if (repositoryId !== undefined) signaledRepositoryId = repositoryId;
            signaledRevision = Math.max(signaledRevision === null ? revision : signaledRevision, revision);
          }
          if (displayedRevision !== undefined && displayedRevision !== null) markAcceptanceUpdating();
          else hideProvisionalSliceHeads();
        }
      } catch {
        // A malformed revision event still triggers a canonical refetch.
        hideProvisionalSliceHeads();
      }
      loadContext();
    });
    eventSource.addEventListener("error", () => {
      setLoadStatus(eventSource.readyState === EventSource.CLOSED ? "Polling" : "Reconnecting");
      startFallback();
    });
  }

  window.addEventListener("pagehide", () => {
    pageHidden = true;
    if (eventSource) eventSource.close();
    stopFallback();
  });
  elements.refreshContext.addEventListener("click", loadContext);
  loadContext();
  connectEvents();
})();
