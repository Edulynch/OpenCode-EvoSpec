from __future__ import annotations

import http.client
import json
import os
import re
import select
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from html import unescape
from importlib.resources import files
from pathlib import Path
from typing import BinaryIO, Callable, NamedTuple, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evospec.dashboard import DEFAULT_HOST, DashboardServer, create_server
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory


class _SseFrame(NamedTuple):
    event: str | None
    data: str
    comments: tuple[str, ...]


class _EventStream:
    """Small bounded SSE client that keeps tests independent of server constants."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._socket: socket.socket = socket.create_connection((host, port), timeout=1.0)
        self._socket.settimeout(1.0)
        self._reader: BinaryIO = self._socket.makefile("rb")

    def open(self) -> tuple[int, dict[str, str]]:
        self._socket.sendall(b"".join((
            b"GET /api/events HTTP/1.1\r\n",
            f"Host: {self._host}:{self._port}\r\n".encode("ascii"),
            b"Accept: text/event-stream\r\n",
            b"Connection: keep-alive\r\n\r\n",
        )))
        status_line = self._readline().split(" ", 2)
        if len(status_line) < 2:
            raise AssertionError("SSE response did not include an HTTP status")
        headers: dict[str, str] = {}
        while line := self._readline():
            name, separator, value = line.partition(":")
            if not separator:
                raise AssertionError(f"malformed SSE response header: {line!r}")
            headers[name.lower()] = value.strip()
        return int(status_line[1]), headers

    def wait_for(self, predicate: Callable[[_SseFrame], bool], timeout: float = 2.0) -> _SseFrame:
        deadline = time.monotonic() + timeout
        while remaining := deadline - time.monotonic():
            readable, _, _ = select.select((self._socket,), (), (), min(remaining, 0.25))
            if not readable:
                continue
            frame = self._read_frame()
            if predicate(frame):
                return frame
        raise AssertionError("timed out waiting for an SSE frame")

    def next_frame(self, timeout: float = 2.0) -> _SseFrame:
        return self.wait_for(lambda frame: True, timeout)

    def close(self) -> None:
        try:
            self._reader.close()
        finally:
            self._socket.close()

    def _read_frame(self) -> _SseFrame:
        event: str | None = None
        data: list[str] = []
        comments: list[str] = []
        while True:
            line = self._readline()
            if not line:
                if event is not None or data or comments:
                    return _SseFrame(event, "\n".join(data), tuple(comments))
                raise AssertionError("SSE connection closed before a frame arrived")
            if line.startswith(":"):
                comments.append(line[1:].lstrip())
                continue
            field, separator, value = line.partition(":")
            if not separator:
                raise AssertionError(f"malformed SSE frame line: {line!r}")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)

    def _readline(self) -> str:
        line = self._reader.readline()
        if not line:
            return ""
        return line.decode("utf-8").rstrip("\r\n")


class DashboardTests(unittest.TestCase):
    def test_routes_serve_context_and_remain_read_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            WorkflowService.open(root).init()
            before = self._files(root)
            server = create_server(root, 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                base = f"http://{DEFAULT_HOST}:{server.server_port}"
                with urlopen(f"{base}/api/context") as response:
                    context = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "application/json")
                self.assertEqual(context["initialization_state"], "INITIALIZED")
                self.assertIn("completed_terminal", context)

                with self.assertRaises(HTTPError) as unknown:
                    urlopen(f"{base}/missing")
                self.assertEqual(unknown.exception.code, 404)
                self.assertEqual(json.loads(unknown.exception.read()), {"error": "endpoint not found"})

                with self.assertRaises(HTTPError) as write:
                    urlopen(Request(f"{base}/api/context", method="POST"))
                self.assertEqual(write.exception.code, 405)
                self.assertEqual(write.exception.headers["Allow"], "GET")
                self.assertEqual(json.loads(write.exception.read()), {"error": "dashboard is read-only"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual(self._files(root), before)

    def test_static_shell_and_assets_are_served_from_packaged_resources(self) -> None:
        resources = files("evospec").joinpath("resources", "dashboard")
        if not all(resources.joinpath(name).is_file() for name in ("index.html", "dashboard.css", "dashboard.js")):
            self.skipTest("dashboard assets are owned by a parallel worktree task")
        with TemporaryDirectory() as temporary:
            server = create_server(Path(temporary), 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                base = f"http://{DEFAULT_HOST}:{server.server_port}"
                for path, content_type in (("/", "text/html"), ("/dashboard.css", "text/css"), ("/dashboard.js", "application/javascript")):
                    with self.subTest(path=path), urlopen(f"{base}{path}") as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), content_type)
                        self.assertTrue(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_events_stream_revises_only_after_authoritative_changes_and_stays_read_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            _ = workflow.init()
            initial_revision = workflow.status().revision
            before_stream = self._files(root)
            server, thread, _ = self._start_server(root)
            stream = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                status, headers = stream.open()
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("content-type", "").split(";", 1)[0], "text/event-stream")

                initial = stream.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(initial.data), {"revision": initial_revision})
                unchanged = stream.next_frame()
                self.assertIsNone(unchanged.event)
                self.assertTrue(unchanged.comments)
                self.assertEqual(self._files(root), before_stream)

                _ = workflow.new("Add dashboard event stream")
                changed_revision = workflow.status().revision
                self.assertGreater(changed_revision, initial_revision)
                changed = stream.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(changed.data), {"revision": changed_revision})

                after_change = self._files(root)
                unchanged = stream.next_frame()
                self.assertIsNone(unchanged.event)
                self.assertTrue(unchanged.comments)
                self.assertEqual(self._files(root), after_change)
            finally:
                stream.close()
                self._stop_server(server, thread)

    def test_events_stream_and_reconnect_converge_to_latest_rapid_workflow_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            _ = workflow.init()
            initial_revision = workflow.status().revision
            server, thread, _ = self._start_server(root)
            stream = _EventStream(DEFAULT_HOST, server.server_port)
            reconnected = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                self.assertEqual(stream.open()[0], 200)
                initial = stream.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(initial.data), {"revision": initial_revision})

                transition_started = time.monotonic()
                _ = workflow.new("Add rapid dashboard workflow coverage")
                _ = workflow.record_progress("implement", "dashboard", (), "verify events", (), "reconnect")
                _ = workflow.pause()
                self.assertLess(time.monotonic() - transition_started, 0.5)
                final_revision = workflow.status().revision
                self.assertGreater(final_revision, initial_revision)

                latest = stream.wait_for(
                    lambda frame: frame.event == "revision"
                    and json.loads(frame.data) == {"revision": final_revision}
                )
                self.assertEqual(json.loads(latest.data), {"revision": final_revision})

                after_changes = self._files(root)
                stream.close()
                self.assertEqual(reconnected.open()[0], 200)
                reconnected_initial = reconnected.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(reconnected_initial.data), {"revision": final_revision})
                self.assertEqual(self._files(root), after_changes)
            finally:
                stream.close()
                reconnected.close()
                self._stop_server(server, thread)

    def test_events_stream_supports_independent_clients_after_one_disconnects(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            _ = workflow.init()
            initial_revision = workflow.status().revision
            server, thread, _ = self._start_server(root)
            first = _EventStream(DEFAULT_HOST, server.server_port)
            second = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                self.assertEqual(first.open()[0], 200)
                self.assertEqual(second.open()[0], 200)
                for stream in (first, second):
                    initial = stream.wait_for(lambda frame: frame.event == "revision")
                    self.assertEqual(json.loads(initial.data), {"revision": initial_revision})

                first.close()
                _ = workflow.new("Add independent dashboard client")
                changed_revision = workflow.status().revision
                # El cliente puede recibir revisiones intermedias ya encoladas;
                # comprobamos que converge a la revisión final persistida.
                changed = second.wait_for(
                    lambda frame: frame.event == "revision"
                    and json.loads(frame.data) == {"revision": changed_revision}
                )
                self.assertEqual(json.loads(changed.data), {"revision": changed_revision})
            finally:
                first.close()
                second.close()
                self._stop_server(server, thread)

    def test_events_route_rejects_mutating_requests(self) -> None:
        with TemporaryDirectory() as temporary:
            server, thread, base = self._start_server(Path(temporary))
            try:
                with self.assertRaises(HTTPError) as write:
                    urlopen(Request(f"{base}/api/events", method="POST"), timeout=1.0)
                self.assertEqual(write.exception.code, 405)
                self.assertEqual(write.exception.headers["Allow"], "GET")
                self.assertEqual(json.loads(write.exception.read()), {"error": "dashboard is read-only"})
            finally:
                self._stop_server(server, thread)

    def test_packaged_dashboard_source_declares_event_lifecycle_contract(self) -> None:
        source = files("evospec").joinpath("resources", "dashboard", "dashboard.js").read_text(encoding="utf-8")
        self.assertRegex(source, r'let\s+selectedTab\s*=\s*["\']features["\']')
        self.assertRegex(source, r'(?s)function\s+render\s*\(.*?selectTab\(selectedTab\)')
        self.assertRegex(source, r'new\s+EventSource\(\s*["\']/api/events["\']\s*\)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']revision["\'].*?loadContext\(')
        self.assertRegex(source, r'(?s)(?:onopen|addEventListener\(\s*["\']open["\']).*?(?:clearInterval|stop\w*Fallback)')
        self.assertRegex(source, r'(?s)(?:onerror|addEventListener\(\s*["\']error["\']).*?(?:setInterval|start\w*Fallback)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']pagehide["\'].*?\.close\(\)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']pagehide["\'].*?(?:clearInterval|stop\w*Fallback)')

    def test_dashboard_browser_projects_tabs_exact_work_and_collapsed_technical_details(self) -> None:
        fixtures = self._dashboard_fixture()
        desktop = self._run_dashboard_browser(fixtures)
        narrow = self._run_dashboard_browser(fixtures, "375,800")
        outcomes = {f"desktop: {name}": detail for name, detail in desktop.items()} | {f"narrow: {name}": detail for name, detail in narrow.items()}
        self.assertTrue(outcomes, "browser scenario must emit named outcomes")
        failures = {name: detail for name, detail in outcomes.items() if detail != "pass"}
        self.assertEqual(failures, {})

    def _run_dashboard_browser(self, fixtures: dict[str, dict[str, object]], window_size: str = "1280,900") -> dict[str, str]:
        browser = os.environ.get("EVOSPEC_DASHBOARD_BROWSER")
        if not browser:
            self.skipTest("EVOSPEC_DASHBOARD_BROWSER is not set; Chromium dashboard coverage is optional")
        browser_path = Path(browser)
        if not browser_path.is_file():
            self.fail(f"EVOSPEC_DASHBOARD_BROWSER does not name an executable file: {browser_path}")

        temporary_root = Path(tempfile.gettempdir())
        self.assertTrue(temporary_root.is_dir(), "the system temporary-directory parent must exist")
        dashboard = files("evospec").joinpath("resources", "dashboard")
        shell = dashboard.joinpath("index.html").read_text(encoding="utf-8")
        stylesheet = dashboard.joinpath("dashboard.css").read_text(encoding="utf-8")
        script = dashboard.joinpath("dashboard.js").read_text(encoding="utf-8")
        fixtures_json = json.dumps(fixtures).replace("</", "<\\/")
        with TemporaryDirectory(prefix="evospec-dashboard-browser-") as temporary:
            root = Path(temporary)
            page = root / "dashboard.html"
            profile = root / "chromium-profile"
            _ = page.write_text(
                shell.replace('<link rel="stylesheet" href="dashboard.css">', f"<style>{stylesheet}</style>")
                .replace(
                    '<script src="dashboard.js"></script>',
                    f"<script>{self._browser_boundary(fixtures_json)}</script>"
                    + f"<script>{script}</script><script>{self._browser_scenario()}</script>",
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                (
                    str(browser_path),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--window-size={window_size}",
                    f"--user-data-dir={profile}",
                    "--virtual-time-budget=1000",
                    "--dump-dom",
                    page.as_uri(),
                ),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        sentinel = re.search(r'<output id="dashboard-test-results">(.*?)</output>', completed.stdout, re.DOTALL)
        self.assertIsNotNone(sentinel, "Chromium did not emit the dashboard test sentinel")
        if sentinel is None:
            raise AssertionError("Chromium did not emit the dashboard test sentinel")
        parsed = cast(object, json.loads(unescape(sentinel.group(1))))
        if not isinstance(parsed, dict):
            raise AssertionError("dashboard browser sentinel must contain string-named string outcomes")
        outcomes = cast(dict[str, str], parsed)
        if not all(type(name) is str and type(detail) is str for name, detail in outcomes.items()):
            raise AssertionError("dashboard browser sentinel must contain string-named string outcomes")
        return outcomes

    def _browser_boundary(self, fixtures_json: str) -> str:
        return f"""
(() => {{
  const fixtures = {fixtures_json};
  const state = {{ context: fixtures.linked, listeners: [] }};
  const intervals = new Map();
  window.__dashboardFixture = state.context;
  window.__dashboardFixtures = fixtures;
  window.fetch = () => Promise.resolve({{ ok: true, json: async () => state.context }});
  window.setInterval = (callback) => {{ const identifier = {{}}; intervals.set(identifier, callback); return identifier; }};
  window.clearInterval = (identifier) => {{ intervals.delete(identifier); }};
  class DashboardEventSource {{
    constructor() {{ this.readyState = 1; this.listeners = new Map(); sources.push(this); }}
    addEventListener(type, callback) {{
      const callbacks = this.listeners.get(type) || [];
      callbacks.push(callback);
      this.listeners.set(type, callbacks);
      if (type === "open") setTimeout(() => callback(new Event("open")), 0);
    }}
    close() {{ this.readyState = DashboardEventSource.CLOSED; }}
    emit(type) {{ for (const callback of this.listeners.get(type) || []) callback(new Event(type)); }}
  }}
  DashboardEventSource.OPEN = 1;
  DashboardEventSource.CLOSED = 2;
  const sources = [];
  window.EventSource = DashboardEventSource;
  window.__dashboardTest = {{
    setContext: (context) => {{ state.context = context; window.__dashboardFixture = context; }},
    normalRefresh: () => sources.forEach((source) => source.emit("open")),
    revision: () => sources.forEach((source) => source.emit("revision")),
    reconnecting: () => sources.forEach((source) => {{ source.readyState = DashboardEventSource.OPEN; source.emit("error"); }}),
    startPolling: () => sources.forEach((source) => {{ source.readyState = DashboardEventSource.CLOSED; source.emit("error"); }}),
    poll: () => intervals.forEach((callback) => callback()),
    reconnect: () => sources.forEach((source) => {{ source.readyState = DashboardEventSource.OPEN; source.emit("open"); }})
  }};
}})();
"""

    def _browser_scenario(self) -> str:
        return """
(() => {
  const results = {};
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const check = (name, callback) => {
    try { callback(); results[name] = "pass"; }
    catch (error) { results[name] = error instanceof Error ? error.message : String(error); }
  };
  const require = (condition, message) => { if (!condition) throw new Error(message); };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const bounds = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && bounds.height > 0;
  };
   const ids = (selector) => Array.from(document.querySelectorAll(selector)).map((element) => element.dataset.featureId || element.dataset.workId);
   const sameMembers = (left, right) => JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
   const click = (element) => element.click();
   const key = (element, value) => element.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true }));
   const assertSelectedTab = (name) => {
     const features = document.querySelector('#tab-features');
     const technical = document.querySelector('#tab-technical');
     const featurePanel = document.querySelector('#features-panel');
     const technicalPanel = document.querySelector('#technical-panel');
     const featuresSelected = name === "features";
     require(features && technical && featurePanel && technicalPanel, "both tabs and tab panels must render");
     require(features.getAttribute("aria-selected") === String(featuresSelected) && features.tabIndex === (featuresSelected ? 0 : -1) && featurePanel.hidden === !featuresSelected, `Features must be ${featuresSelected ? "selected" : "unselected"} with matching roving tabindex and panel visibility`);
     require(technical.getAttribute("aria-selected") === String(!featuresSelected) && technical.tabIndex === (featuresSelected ? -1 : 0) && technicalPanel.hidden === featuresSelected, `Technical must be ${featuresSelected ? "unselected" : "selected"} with matching roving tabindex and panel visibility`);
   };
   const output = document.createElement("output");
  output.id = "dashboard-test-results";
  document.body.append(output);

  setTimeout(async () => {
    const expectedFeatures = ["feature-alpha", "feature-beta", "feature-gamma", "feature-duplicate-a", "feature-duplicate-b", "feature-epsilon", "feature-zeta", "feature-eta", "feature-blocked"];
    const acceptedFeatures = expectedFeatures.filter((id) => id !== "feature-alpha" && id !== "feature-blocked");
    const expectedWorks = ["work-alpha-active", "work-alpha-done", "work-blocked"];
    await sleep(20);

    check("focused skip link reserves space above the Current Work summary", () => {
      const skipLink = document.querySelector(".skip-link");
      const summary = document.querySelector("#current-work-section .current-work-summary");
      require(skipLink && summary && visible(summary), "linked fixture must render a focusable skip link and visible Current Work summary");
      skipLink.focus();
      const skipStyle = getComputedStyle(skipLink);
      const skipBounds = skipLink.getBoundingClientRect();
      require(document.activeElement === skipLink, "skip link must be the active focused element");
      require(visible(skipLink), "focused skip link must be visible");
      require(skipStyle.position === "static", "focused skip link must remain in normal flow");
      const summaryBounds = summary.getBoundingClientRect();
      require(skipBounds.right <= summaryBounds.left || skipBounds.left >= summaryBounds.right || skipBounds.bottom <= summaryBounds.top || skipBounds.top >= summaryBounds.bottom, "focused skip link must not intersect the visible Current Work summary");
    });

    check("human-first dashboard labels identify the current, blocked, and published work", () => {
      const currentHeadings = Array.from(document.querySelectorAll('#current-work-heading'));
      const blockedHeadings = Array.from(document.querySelectorAll('#blocked-heading'));
      const featuresHeadings = Array.from(document.querySelectorAll('#features-heading'));
      require(currentHeadings.length === 1 && currentHeadings[0].textContent.trim() === "Current work", "Current work must appear once as the operational heading");
      require(blockedHeadings.length === 1 && blockedHeadings[0].textContent.trim() === "Blocked work", "Blocked work must appear once as the operational heading");
      require(featuresHeadings.length === 1 && featuresHeadings[0].textContent.trim() === "Published features", "Published features must name the feature projection");
      require(document.querySelector('#load-status').textContent.trim() === "Live", "a connected event stream must use the simple Live status");
    });

    check("semantic tabs expose exactly two native tab controls", () => {
      const tablist = document.querySelector('[role="tablist"]');
      const features = document.querySelector('#tab-features[role="tab"]');
      const technical = document.querySelector('#tab-technical[role="tab"]');
      require(tablist && tablist.contains(features) && tablist.contains(technical) && tablist.querySelectorAll('[role="tab"]').length === 2, "Features and Technical must be the only tabs in one tablist");
      require(features.getAttribute("aria-controls") === "features-panel" && technical.getAttribute("aria-controls") === "technical-panel", "tabs must own their corresponding panels");
      const featurePanel = document.querySelector('#features-panel[role="tabpanel"]');
      const technicalPanel = document.querySelector('#technical-panel[role="tabpanel"]');
      require(featurePanel && featurePanel.getAttribute("aria-labelledby") === "tab-features", "features panel must be labelled by Features tab");
      require(technicalPanel && technicalPanel.getAttribute("aria-labelledby") === "tab-technical", "technical panel must be labelled by Technical tab");
      assertSelectedTab("features");
      require(!visible(technicalPanel), "Features must be the visible default tab and Technical fully collapsed");
      require(!document.querySelector('#current-work-section').closest('[role="tabpanel"]') && !document.querySelector('#blocked-section').closest('[role="tabpanel"]'), "Current Work and Blocked must remain outside tab panels");
    });

    check("feature projection renders accepted capabilities as human-facing title and state rows", () => {
      require(sameMembers(ids('#features-list > article[data-feature-id]'), expectedFeatures), "feature rows must map one-for-one to accepted capabilities by distinct id");
      require(document.querySelectorAll('#features-list > article[data-feature-id]').length === 9, "all nine capabilities must render even when titles duplicate");
      require(document.querySelectorAll('#features-list [data-work-id]').length === 0, "feature rows must keep work identifiers in Technical Details");
      const alpha = document.querySelector('[data-feature-id="feature-alpha"]');
      require(alpha.querySelector('.feature-title').textContent.trim() === "Duplicate capability", "feature rows must retain the accepted capability title");
      require(alpha.querySelector('.status-label').textContent.trim() === "In progress", "an exactly linked active work item must project its human state");
      const noWork = document.querySelector('[data-feature-id="feature-beta"]');
      require(noWork.querySelector('.status-label').textContent.trim() === "Completed", "accepted capabilities without exactly linked work must project as Completed");
      require(Array.from(document.querySelectorAll('#features-list > article[data-feature-id]')).every((row) => row.children.length === 2 && row.querySelector('.feature-title') && row.querySelector('.status-label')), "feature rows must contain only a title and human state");
      require(document.querySelector('#blocked-list').textContent.includes("Blocked work") && !document.querySelector('#blocked-list').textContent.includes("Unassociated work"), "unlinked blocked work must use a neutral human fallback without exposing linkage state");
    });

    check("feature filters use human state labels and preserve exact linkage projection", () => {
      require(Array.from(document.querySelectorAll('[data-feature-filter]')).map((button) => button.textContent.trim()).join(",") === "All,Completed,Blocked", "feature filters must use human state labels");
      click(document.querySelector('#feature-filter-blocked'));
      require(sameMembers(ids('#features-list > article[data-feature-id]'), ["feature-blocked"]), "Blocked filter must show only the blocked capability");
      require(document.querySelector('[data-feature-id="feature-blocked"] .status-label').textContent.trim() === "Blocked", "Blocked must remain visibly canonical in the human projection");
      click(document.querySelector('#feature-filter-completed'));
      require(sameMembers(ids('#features-list > article[data-feature-id]'), acceptedFeatures), "Completed filter must include only the seven completed presentation capabilities");
      click(document.querySelector('#feature-filter-all'));
      require(document.querySelector('[data-feature-id="feature-alpha"] .status-label').textContent.trim() === "In progress", "returning to All must retain the exact active-work projection");
    });

    check("tab keyboard navigation wraps and changes accessible visibility", () => {
      const features = document.querySelector('#tab-features');
      const technical = document.querySelector('#tab-technical');
      features.focus();
      key(features, "ArrowLeft");
      require(document.activeElement === technical && technical.getAttribute("aria-selected") === "true", "ArrowLeft must wrap Features to Technical");
      key(technical, "ArrowRight");
      require(document.activeElement === features && features.getAttribute("aria-selected") === "true", "ArrowRight must wrap Technical to Features");
      click(technical);
      const technicalPanel = document.querySelector('#technical-panel');
      assertSelectedTab("technical");
      require(visible(technicalPanel), "Technical click must select and visibly reveal its panel");
    });

    check("technical records start closed and retain the complete raw context last", () => {
      const panel = document.querySelector('#technical-panel');
      const details = Array.from(panel.querySelectorAll('details'));
      const raw = document.querySelector('#technical-raw');
      require(details.length > 1 && details.every((entry) => !entry.open), "technical sections must be separate native details closed on first entry");
      require(details.at(-1) === raw && raw && raw.querySelectorAll('summary').length === 1 && raw.querySelector('summary').textContent.trim() === "Raw JSON" && raw.querySelectorAll('pre').length === 1, "the final technical detail must expose one Raw JSON summary and one preformatted payload");
      require(JSON.stringify(JSON.parse(raw.querySelector('pre').textContent)) === JSON.stringify(window.__dashboardFixture), "raw technical JSON must exactly equal the received fixture");
      click(raw.querySelector('summary'));
      require(raw.open, "raw technical JSON must be independently expandable");
    });

    await sleep(20);
    window.__dashboardTest.setContext(window.__dashboardFixtures.inactive);
    window.__dashboardTest.normalRefresh();
    await sleep(20);
    check("normal context refresh preserves selected Technical tab and open raw disclosure", () => {
      const raw = document.querySelector('#technical-raw');
      assertSelectedTab("technical");
      require(raw.open, "normal context refresh must retain the selected Technical tab and its raw disclosure state");
      click(document.querySelector('#tab-features'));
      require(document.querySelector('#technical-panel').hidden && document.querySelector('#technical-panel').getBoundingClientRect().height === 0, "an opened hidden technical panel must not retain layout height");
      click(document.querySelector('#tab-technical'));
      click(document.querySelector('#feature-filter-all'));
      const current = document.querySelector('#current-work-section');
      require(current.hidden && !visible(current), "current work must hide when queue.active is null despite leftover progress");
    });
    window.__dashboardTest.setContext(window.__dashboardFixtures.live);
    window.__dashboardTest.revision();
    await sleep(20);
    check("SSE revision refresh preserves Technical while unpublished active work stays current", () => {
      assertSelectedTab("technical");
      require(document.querySelector('#current-work-section').hidden === false, "an active unpublished work item must remain visible as Current Work");
      require(document.querySelectorAll('#features-list > article[data-feature-id]').length === 9, "live fixture must render eight Accepted capabilities and one Blocked capability");
      require(!document.querySelector('#features-list [data-work-id="work-live-unpublished"]'), "unpublished active work must not fabricate a feature row");
      click(document.querySelector('#feature-filter-completed'));
      require(document.querySelectorAll('#features-list > article[data-feature-id]').length === 8, "Completed filter must exclude the sole Blocked capability");
      click(document.querySelector('#feature-filter-blocked'));
      require(sameMembers(ids('#features-list > article[data-feature-id]'), ["feature-live-blocked"]), "Blocked filter must show the sole blocked capability");
    });
    window.__dashboardTest.reconnecting();
    await sleep(20);
    check("an open event stream error retains the Reconnecting status", () => {
      require(document.querySelector('#load-status').textContent.trim() === "Reconnecting", "an open event stream error must retain the Reconnecting status");
    });
    window.__dashboardTest.setContext(window.__dashboardFixtures.empty);
    window.__dashboardTest.startPolling();
    window.__dashboardTest.poll();
    await sleep(20);
    check("polling fallback refresh preserves Technical without overflow", () => {
      assertSelectedTab("technical");
      require(document.querySelector('#load-status').textContent.trim() === "Polling", "a closed event stream must retain the Polling status");
      require(document.documentElement.scrollWidth <= document.documentElement.clientWidth, "dashboard must not overflow horizontally at the configured viewport width");
    });
    window.__dashboardTest.setContext(window.__dashboardFixtures.linked);
    window.__dashboardTest.reconnect();
    await sleep(20);
    check("SSE reconnect refresh preserves Technical and its raw disclosure", () => {
      assertSelectedTab("technical");
      require(document.querySelector('#load-status').textContent.trim() === "Live", "a restored event stream must return to the Live status");
      require(document.querySelector('#technical-raw').open, "SSE reconnect must retain the open Raw JSON disclosure");
    });
    click(document.querySelector('#tab-features'));
    check("Features click reverses tab selection with coherent hidden Technical panel", () => {
      assertSelectedTab("features");
      const technical = document.querySelector('#technical-panel');
      require(technical.hidden && getComputedStyle(technical).display === "none" && technical.getBoundingClientRect().height === 0, "Features selection must fully hide the Technical panel");
      require(document.documentElement.scrollWidth <= document.documentElement.clientWidth, "dashboard must not overflow after returning to Features");
    });
    window.__dashboardTest.setContext(window.__dashboardFixtures.empty);
    window.__dashboardTest.revision();
    await sleep(20);
    check("empty capabilities and queue retain Features safely at desktop and narrow widths", () => {
      click(document.querySelector('#feature-filter-all'));
      const list = document.querySelector('#features-list');
      require(list.querySelectorAll('article[data-feature-id]').length === 0 && list.querySelector('.empty-state'), "empty context must render the feature empty state without phantom rows");
      require(document.documentElement.scrollWidth <= document.documentElement.clientWidth, "dashboard must not overflow horizontally at the configured viewport width");
      assertSelectedTab("features");
      const technical = document.querySelector('#technical-panel');
      require(technical.hidden && getComputedStyle(technical).display === "none" && technical.getBoundingClientRect().height === 0, "hidden technical panel must be display-none and height-zero at every viewport");
    });
    output.textContent = JSON.stringify(results);
  }, 40);
})();
"""

    def _dashboard_fixture(self) -> dict[str, dict[str, object]]:
        capabilities = [
            {"feature_id": "feature-alpha", "title": "Duplicate capability"},
            {"feature_id": "feature-beta", "title": "Accepted without work"},
            {"feature_id": "feature-gamma", "title": "Another accepted capability"},
            {"feature_id": "feature-duplicate-a", "title": "Repeated title"},
            {"feature_id": "feature-duplicate-b", "title": "Repeated title"},
            {"feature_id": "feature-epsilon", "title": "Fifth accepted capability"},
            {"feature_id": "feature-zeta", "title": "Sixth accepted capability"},
            {"feature_id": "feature-eta", "title": "Seventh accepted capability"},
            {"feature_id": "feature-blocked", "title": "Blocked capability"},
        ]
        records = (
            ("work-alpha-active", "feature-alpha", "ACTIVE", ("IN_PROGRESS", "IMPLEMENT")),
            ("work-alpha-done", "feature-alpha", "DONE", None),
            ("work-blocked", "feature-blocked", "BLOCKED", ("VERIFYING", "VERIFY")),
            ("work-alpha-prefix", "feature-alpha-extra", "DONE", None),
            ("work-unassociated-terminal", None, "DONE", None),
            ("work-unassociated-blocked", None, "BLOCKED", ("VERIFYING", "VERIFY")),
        )
        works = [
            {
                "id": identifier,
                "feature_id": feature_id,
                "change_id": f"change-{identifier}",
                "operational_state": state,
                "lifecycle": list(lifecycle) if lifecycle is not None else None,
                "reason": "Blocked for deterministic fixture coverage" if state == "BLOCKED" else None,
                "blocker": None,
                "transitioned_at": None,
                "review_verdict": None,
                "verification_sufficient": False,
                "activation_eligible": False,
            }
            for identifier, feature_id, state, lifecycle in records
        ]
        active, terminal, blocked, prefix, unassociated, unassociated_blocked = works
        all_works = [active, terminal, blocked, prefix, unassociated, unassociated_blocked]
        queue = {
            "active": active,
            "all": all_works,
            "blocked": [blocked, unassociated_blocked],
            "complete": True,
            "total": 6,
            "relations": [],
        }
        linked: dict[str, object] = {
            "revision": 101,
            "initialization_state": "INITIALIZED",
            "accepted_capabilities": capabilities,
            "accepted_capabilities_complete": True,
            "accepted_capabilities_total": 9,
            "work_queue": queue,
            "progress": {"current_work": "Implementation is underway", "current_action": "Render linked work", "focus": "Dashboard", "completed_work": ["Prior step"], "pending_work": ["Review"]},
            "decision_state": {},
            "lifecycle": ["IN_PROGRESS", "IMPLEMENT"],
            "guidance": [],
            "active_locks": [],
            "external_sdd_evidence": [],
        }
        inactive = {**linked, "revision": 102, "work_queue": {**queue, "active": None, "all": [terminal, blocked, prefix, unassociated]}}
        live_capabilities = [
            {"feature_id": f"feature-live-{index}", "title": f"Accepted live capability {index}"}
            for index in range(8)
        ] + [{"feature_id": "feature-live-blocked", "title": "Blocked live capability"}]
        live_blocked = {**blocked, "id": "work-live-blocked", "feature_id": "feature-live-blocked"}
        live_active = {**active, "id": "work-live-unpublished", "feature_id": "feature-unpublished"}
        live = {
            **linked,
            "revision": 103,
            "accepted_capabilities": live_capabilities,
            "work_queue": {"active": live_active, "all": [live_active, live_blocked], "blocked": [live_blocked], "complete": True, "total": 2, "relations": []},
        }
        empty: dict[str, object] = {**linked, "revision": 104, "accepted_capabilities": [], "accepted_capabilities_total": 0, "work_queue": {"active": None, "all": [], "blocked": [], "complete": True, "total": 0, "relations": []}, "progress": {}}
        return {"linked": linked, "inactive": inactive, "live": live, "empty": empty}

    def test_get_rejects_invalid_host_before_serving_context_events_or_assets(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            WorkflowService.open(root).init()
            server, thread, _ = self._start_server(root)
            try:
                port = server.server_port
                wrong_port = port + 1 if port < 65535 else port - 1
                invalid_hosts = (
                    (),
                    ("foreign.example",),
                    (f"localhost.evil:{port}",),
                    (f"127.0.0.1@localhost:{port}",),
                    (f"127.0.0.1:{wrong_port}",),
                    (f"127.0.0.1:{port}", f"localhost:{port}"),
                    (f"[::1]:{port}",),
                )
                for path in ("/api/context", "/api/events", "/dashboard.css"):
                    for hosts in invalid_hosts:
                        with self.subTest(path=path, hosts=hosts):
                            status, content_type, body = self._get_with_host(port, path, hosts)
                            self.assertEqual(status, 400)
                            self.assertEqual(content_type, "application/json")
                            self.assertEqual(json.loads(body), {"error": "invalid Host header"})

                status, content_type, body = self._get_with_host(port, "/api/context", (f"localhost:{port}",))
                self.assertEqual(status, 200)
                self.assertEqual(content_type, "application/json")
                self.assertEqual(json.loads(body)["initialization_state"], "INITIALIZED")
            finally:
                self._stop_server(server, thread)

    def _get_with_host(self, port: int, path: str, hosts: tuple[str, ...]) -> tuple[int, str, bytes]:
        connection = http.client.HTTPConnection(DEFAULT_HOST, port, timeout=1.0)
        try:
            connection.putrequest("GET", path, skip_host=True)
            for host in hosts:
                connection.putheader("Host", host)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response.headers.get_content_type(), response.read()
        finally:
            connection.close()
    def _start_server(self, root: Path) -> tuple[DashboardServer, threading.Thread, str]:
        server = create_server(root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://{DEFAULT_HOST}:{server.server_port}"

    def _stop_server(self, server: DashboardServer, thread: threading.Thread) -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive(), "dashboard server did not stop")

    def _files(self, root: Path) -> dict[Path, bytes]:
        return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
