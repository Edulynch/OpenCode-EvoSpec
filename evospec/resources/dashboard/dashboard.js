(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const elements = Object.freeze({
    loadStatus: byId("load-status"), projectSummary: byId("project-summary"),
    currentWorkSection: byId("current-work-section"), currentWorkTitle: byId("current-work-title"),
    currentWorkStatus: byId("current-work-status"), currentWorkExplanation: byId("current-work-explanation"),
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
  const objectValue = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const arrayValue = (value) => Array.isArray(value) ? value : [];
  const textValue = (value, fallback = "Not available") => value === null || value === undefined || value === "" ? fallback : String(value);
  const clear = (element) => element.replaceChildren();
  const create = (tagName, className, value) => {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  };
  const appendEmpty = (element, message) => element.append(create("p", "empty-state", message));
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
    const queue = objectValue(context.work_queue);
    const progress = objectValue(context.progress);
    const capabilities = arrayValue(context.accepted_capabilities).map(objectValue);
    renderHeader(context, queue);
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
        render(await response.json());
      } catch (error) {
        render({ dashboard_error: textValue(error && error.message, "GET /api/context failed."), initialization_state: "UNAVAILABLE", work_queue: {}, progress: {} });
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
    eventSource.addEventListener("revision", () => { loadContext(); });
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
