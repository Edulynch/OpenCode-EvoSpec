(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const elements = Object.freeze({
    loadStatus: byId("load-status"), projectSummary: byId("project-summary"),
    currentWorkSection: byId("current-work-section"), currentWorkTitle: byId("current-work-title"),
    currentWorkStatus: byId("current-work-status"), currentWorkExplanation: byId("current-work-explanation"),
    activeSliceStatus: byId("active-slice-status"), activeSliceContent: byId("active-slice-content"),
    blockedSection: byId("blocked-section"),
    blockedList: byId("blocked-list"), featuresList: byId("features-list"),
    featureFilterButtons: Array.from(document.querySelectorAll("[data-feature-filter]")),
    tabs: [byId("tab-features"), byId("tab-technical")],
    panels: [byId("features-panel"), byId("technical-panel")],
    technicalContent: byId("technical-content")
  });

  let selectedFeatureFilter = "all";
  let selectedTab = "features";
  const technicalOpenState = new Map();
  let latestFeatureRender;
  let activeSliceMessage;
  let activeCriterionStatuses = [];
  let acceptanceStatuses = [];
  let displayedRevision;
  let displayedRepositoryId;
  let signaledRevision = null;
  let signaledRepositoryId;
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
  const safeId = (value) => String(value).replace(/[^a-zA-Z0-9_-]+/g, "-");

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

  function renderHeader(context, queue) {
    const active = activeWork(queue);
    if (context.dashboard_error) elements.projectSummary.textContent = "The latest project status could not be loaded.";
    else if (context.stale) elements.projectSummary.textContent = "Project status needs an updated assessment.";
    else if (arrayValue(queue.blocked).length) elements.projectSummary.textContent = "Work is active, with blocked work needing attention.";
    else if (active) elements.projectSummary.textContent = "Current work is underway.";
    else elements.projectSummary.textContent = "No current work needs attention.";
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
    if (status === "UNVERIFIED") return ["Pending", "waiting"];
    if (status === "FAILED") return ["Failed", "failed"];
    if (status === "STALE" || status === "REVALIDATION_REQUIRED") return ["Revalidate", "stale"];
    if (status === "VERIFIED") {
      return verification.fresh_for_current_workspace === true
        ? ["Verified (current)", "verified"]
        : ["Verified (freshness unavailable)", "waiting"];
    }
    return ["Status unavailable", "waiting"];
  }

  function outcomePresentation(outcome) {
    const value = objectValue(outcome);
    const status = String(value.status || "").toUpperCase();
    if (status === "CURRENT") {
      const receipt = objectValue(value.receipt);
      if (typeof value.latest_receipt_id === "string" && value.latest_receipt_id !== "" && receipt.id === value.latest_receipt_id) {
        return ["Slice accepted", "accepted", "Current canonical acceptance is recorded."];
      }
      return ["Acceptance status unavailable", "waiting", "The canonical acceptance receipt is unavailable."];
    }
    if (status === "READY") return ["Ready for acceptance", "ready", "Ready for acceptance; this Slice is not yet accepted."];
    if (status === "BLOCKED") return ["Blocked", "blocked", "Acceptance is blocked by unresolved requirements."];
    if (status === "STALE" || status === "REVALIDATION_REQUIRED") {
      return ["Revalidate", "stale", "Evidence needs revalidation before acceptance."];
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
    const [outcomeLabel, outcomeTone, outcomeMessage] = outcomePresentation(context.slice_outcome);
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

    [...groups.values()]
      .sort((left, right) => left.featureId.localeCompare(right.featureId))
      .forEach((group) => {
        const article = create("article", "acceptance-feature-group");
        article.dataset.featureId = group.featureId;
        article.append(create("p", "eyebrow", "Feature"), create("h3", "acceptance-feature-title", group.title));
        group.items
          .sort((left, right) => left.slice_agreement_ref.localeCompare(right.slice_agreement_ref))
          .forEach((item) => appendAcceptanceSlice(article, item, item === activeItem, context));
        elements.activeSliceContent.append(article);
      });
    if (unassociated.length) {
      const article = create("article", "acceptance-unassociated");
      article.append(create("h3", "", "Unassociated Slice records"));
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
      typeof outcome.latest_receipt_id === "string" && outcome.latest_receipt_id !== ""
      && receipt.id === outcome.latest_receipt_id
      && receipt.repository_id === context.repository_id
      && receipt.slice_agreement_ref === item.slice_agreement_ref
      && receipt.criteria_revision_id === objectValue(item.criteria_revision).id
    ) {
      return ["Slice accepted", "accepted", "Current canonical acceptance is recorded."];
    }
    return ["Acceptance status unavailable", "waiting", "The canonical acceptance receipt is unavailable or belongs to another Slice or criteria revision."];
  }

  function appendAcceptanceSlice(container, item, isActive, context) {
    const slice = create("section", "acceptance-slice");
    slice.append(create("h4", "acceptance-slice-title", item.slice_title));
    const revision = objectValue(item.criteria_revision);
    const scopeLabels = {
      ACTIVE: "Active",
      PARKED: "Parked",
      QUEUED: `Queued${item.operational_state ? ` (${item.operational_state})` : ""}`,
      PRESERVED: "Preserved primary work (inactive)",
      HISTORICAL: "Historical context (not current)",
      INACTIVE: "Inactive (agreement only)",
    };
    slice.append(create("p", "eyebrow", "Agreed scope"));
    const scopeLabel = create("span", "status-label", scopeLabels[item.scope_status]);
    scopeLabel.dataset.status = item.scope_status === "ACTIVE" ? "in-review" : "waiting";
    acceptanceStatuses.push(scopeLabel);
    const provenanceDetails = [`Scope provenance: ${textValue(item.provenance, "unavailable")}`];
    if (item.operational_state) provenanceDetails.push(`Operational state: ${item.operational_state}`);
    slice.append(scopeLabel, create("p", "acceptance-provenance", provenanceDetails.join("; ")));
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
    if (!isActive && item.historical_receipt === true) {
      slice.append(create("p", "acceptance-history-note", "A historical outcome receipt is recorded for this Slice and its criteria; it is not current acceptance."));
    }
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
        slice.append(create("h5", "active-slice-criteria-heading", "Criterion verification"));
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
            : ["Not evaluated", "waiting"];
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
    container.append(slice);
  }

  function renderAcceptanceIndex(context) {
    acceptanceStatuses = [];
    acceptanceInventory(context);
  }

  function markAcceptanceUpdating() {
    elements.activeSliceStatus.textContent = "Updating";
    elements.activeSliceStatus.dataset.status = "waiting";
    if (activeSliceMessage) activeSliceMessage.textContent = "A newer project revision is being checked.";
    (acceptanceStatuses.length ? acceptanceStatuses : activeCriterionStatuses).forEach((status) => {
      status.textContent = "Updating";
      status.dataset.status = "waiting";
    });
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

  function projectFeatures(context, queue) {
    const capabilities = arrayValue(context.accepted_capabilities).map(objectValue);
    const works = arrayValue(queue.all).map(objectValue);
    return capabilities.map((capability) => ({
      key: `feature-${safeId(capability.feature_id)}`,
      featureId: capability.feature_id,
      title: capabilityTitle(capability),
      capability,
      works: works.filter((work) => work.feature_id === capability.feature_id)
    })).sort((left, right) => left.title.localeCompare(right.title) || String(left.featureId).localeCompare(String(right.featureId)));
  }

  function featureStatus(feature) {
    const active = feature.works.find(workIsActive);
    if (active) return humanStatus(active);
    const blocked = feature.works.find((work) => humanStatus(work) === "Blocked");
    if (blocked) return "Blocked";
    const pending = feature.works.find((work) => humanStatus(work) !== "Completed");
    return pending ? humanStatus(pending) : "Completed";
  }

  function renderFeatureFilters() {
    elements.featureFilterButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.featureFilter === selectedFeatureFilter));
    });
  }

  function filterFeatures(features) {
    if (selectedFeatureFilter === "all") return features;
    if (selectedFeatureFilter === "blocked") return features.filter((feature) => feature.status === "Blocked");
    return features.filter((feature) => feature.status === "Completed");
  }

  function selectFeatureFilter(filter) {
    selectedFeatureFilter = filter;
    renderFeatureFilters();
    if (latestFeatureRender) renderFeatures(latestFeatureRender.context, latestFeatureRender.queue, latestFeatureRender.progress);
  }

  function renderFeatures(context, queue, progress) {
    latestFeatureRender = { context, queue, progress };
    renderFeatureFilters();
    clear(elements.featuresList);
    const features = filterFeatures(projectFeatures(context, queue).map((feature) => ({ ...feature, status: featureStatus(feature) })));
    if (!features.length) {
      appendEmpty(elements.featuresList, "No features match this filter.");
      return;
    }
    features.forEach((feature) => {
      const article = create("article", "feature-row");
      article.dataset.featureId = feature.featureId;
      const status = feature.status;
      const label = create("span", "status-label", status);
      label.dataset.status = statusTone(status);
      article.append(create("span", "feature-title", feature.title), label);
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
    const capabilities = arrayValue(context.accepted_capabilities).map(objectValue);
    appendTechnicalRecord(elements.technicalContent, "technical-capabilities", "Accepted capabilities", capabilities.map((capability) => [
      capabilityTitle(capability), capability.feature_id
    ]));
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
          `Feature agreement ${textValue(item.feature_agreement_ref)}; Slice agreement ${textValue(item.slice_agreement_ref)}; criteria revision ${textValue(revision.id, "not recorded")}; provenance ${textValue(item.provenance)}; agreed outcome ${textValue(revision.outcome)}`,
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
    renderHeader(context, queue);
    renderAcceptanceIndex(context);
    renderCurrentWork(queue, progress, capabilities);
    renderBlocked(queue, capabilities);
    renderFeatures(context, queue, progress);
    renderTechnical(contextPayload, context, queue);
    selectTab(selectedTab);
  }

  function selectTab(name, shouldFocus = false) {
    selectedTab = name === "technical" ? "technical" : "features";
    const selectedIndex = selectedTab === "technical" ? 1 : 0;
    elements.tabs.forEach((tab, index) => {
      const selected = index === selectedIndex;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      elements.panels[index].hidden = !selected;
    });
    if (shouldFocus) elements.tabs[selectedIndex].focus();
  }

  function wireTabs() {
    elements.tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => selectTab(index === 1 ? "technical" : "features"));
      tab.addEventListener("keydown", (event) => {
        let nextIndex;
        if (event.key === "ArrowLeft") nextIndex = (index + elements.tabs.length - 1) % elements.tabs.length;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % elements.tabs.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = elements.tabs.length - 1;
        if (nextIndex === undefined) return;
        event.preventDefault();
        selectTab(nextIndex === 1 ? "technical" : "features", true);
      });
    });
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
        }
      } catch {
        // A malformed revision event still triggers a canonical refetch.
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
  elements.featureFilterButtons.forEach((button) => {
    button.addEventListener("click", () => selectFeatureFilter(button.dataset.featureFilter));
  });
  wireTabs();
  loadContext();
  connectEvents();
})();
