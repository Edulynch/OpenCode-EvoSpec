from __future__ import annotations

import http.client
import json
import os
import re
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from hashlib import sha256
from html import unescape
from importlib.resources import files
from pathlib import Path
from typing import Any, BinaryIO, Callable, NamedTuple, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evospec.agent_protocol import execute
from evospec.dashboard import DEFAULT_HOST, DashboardServer, create_server
from evospec.domain import ExpectedRevision
from evospec.services import CoreService, slice_acceptance_statement
from evospec.workflow import WorkflowService
from tests._tempdir import TemporaryDirectory
from tests.test_e1_r1d1_slice_criteria import CHECKPOINT, FEATURE, SLICE, TASK, _provenance, _spec


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
                with urlopen(f"{base}/api/context") as refreshed:
                    self.assertEqual(json.loads(refreshed.read())["revision"], context["revision"])
                self.assertEqual(context["initialization_state"], "INITIALIZED")
                self.assertIn("completed_terminal", context)

                with self.assertRaises(HTTPError) as unknown:
                    urlopen(f"{base}/missing")
                self.assertEqual(unknown.exception.code, 404)
                self.assertEqual(json.loads(unknown.exception.read()), {"error": "endpoint not found"})

                for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
                    with self.subTest(method=method), self.assertRaises(HTTPError) as write:
                        urlopen(Request(f"{base}/api/context", method=method))
                    self.assertEqual(write.exception.code, 405)
                    self.assertEqual(write.exception.headers["Allow"], "GET")
                    if method != "HEAD":
                        self.assertEqual(json.loads(write.exception.read()), {"error": "dashboard is read-only"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual(self._files(root), before)

    def test_active_slice_index_projects_canonical_get_snapshot_and_is_read_only(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the dependency-free dashboard UI regressions")
        from tests.test_persistence import _ProvisionalSliceTestHarness, _make_provisional_revision

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = WorkflowService.open(root)
            initial = workflow.init()
            criteria = _spec()
            statement = slice_acceptance_statement(FEATURE, SLICE, TASK, CHECKPOINT, criteria)
            _ = workflow.accept_first_slice(
                FEATURE, SLICE, TASK, CHECKPOINT, _provenance(statement),
                expected_revision=ExpectedRevision(initial.revision), criteria=criteria,
            )
            state = CoreService.open(root).recover()
            proposal = _make_provisional_revision(state, title="Read-only dashboard proposal")
            saved = _ProvisionalSliceTestHarness(root).publish(state, proposal, None)
            before = self._files(root)
            server, thread, base = self._start_server(root)
            try:
                contexts = []
                for _ in range(3):
                    with urlopen(f"{base}/api/context") as response:
                        self.assertEqual(response.status, 200)
                        contexts.append(json.loads(response.read()))
                context = contexts[0]
            finally:
                self._stop_server(server, thread)
            self.assertEqual(self._files(root), before)
            self.assertEqual(CoreService.open(root).recover(), saved)
            self.assertTrue(all(snapshot == context for snapshot in contexts))
            accepted = context["accepted_slice"]
            self.assertEqual(accepted["feature_title"], FEATURE)
            self.assertEqual(accepted["slice_title"], SLICE)
            self.assertEqual(len(accepted["criteria_revision"]["criteria"]), 2)
            self.assertEqual(len(context["criterion_verification"]), 2)
            self.assertTrue(all(
                item["criteria_revision_id"] == accepted["criteria_revision"]["id"]
                and item["status"] == "UNVERIFIED"
                for item in context["criterion_verification"]
            ))
            inventory = context["slice_acceptance_inventory"]
            self.assertEqual(inventory["source"], "CORE_AGREEMENTS")
            self.assertEqual(inventory["total"], 1)
            self.assertTrue(inventory["complete"])
            self.assertEqual(inventory["history_scope"], "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE")
            self.assertEqual(inventory["items"][0]["slice_agreement_ref"], accepted["slice_agreement_ref"])
            self.assertEqual(inventory["items"][0]["criteria_revision"]["id"], accepted["criteria_revision"]["id"])
            provisional = context["provisional_slice_inventory"]
            self.assertEqual(provisional["source"], "CORE_PROVISIONAL_SLICE_HEADS")
            self.assertEqual(provisional["availability"], "AVAILABLE")
            self.assertEqual(provisional["total"], 1)
            self.assertTrue(provisional["complete"])
            self.assertFalse(provisional["truncated"])
            self.assertEqual(provisional["items"][0]["proposal_id"], proposal.proposal_id)
            self.assertEqual(provisional["items"][0]["head"]["revision_id"], proposal.revision_id)
            self.assertEqual(provisional["items"][0]["title"], "Read-only dashboard proposal")
            environment = os.environ.copy()
            environment["EVOSPEC_DASHBOARD_ACCEPTANCE_CONTEXT"] = json.dumps(context, separators=(",", ":"))
            completed = subprocess.run(
                (node, "--test", str(Path(__file__).with_name("dashboard_acceptance_index.test.mjs"))),
                cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True,
                text=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_d5_adopted_slice_get_projection_is_agreed_pending_and_read_only(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the dependency-free dashboard UI regressions")
        from tests.test_d5_proposal_adoption import D5ProposalAdoptionTests, _simulated_tty_adoption

        with TemporaryDirectory(prefix="evospec-d5-dashboard-") as temporary:
            root = Path(temporary)
            fixture = D5ProposalAdoptionTests()
            workflow, fixture_context = fixture._workspace(root)
            before = fixture_context["state"]
            values = fixture._adoption_inputs(fixture_context)
            statement = fixture._agreement(workflow, before, fixture_context, values)
            _, shown_statement, output = _simulated_tty_adoption(
                workflow,
                fixture_context["repository_id"],
                fixture_context["feature_id"],
                values,
                before.revision,
            )
            self.assertEqual(shown_statement, statement)
            self.assertIn(statement, output)
            adopted = CoreService.open(root).recover()
            adoption = adopted.provisional_slice_adoptions[0]
            before_get = self._files(root)
            server, thread, base = self._start_server(root)
            try:
                with urlopen(f"{base}/api/context") as response:
                    self.assertEqual(response.status, 200)
                    context = json.loads(response.read())
            finally:
                self._stop_server(server, thread)

            self.assertEqual(self._files(root), before_get)
            self.assertEqual(CoreService.open(root).recover(), adopted)
            accepted = context["accepted_slice"]
            self.assertEqual(accepted["slice_agreement_ref"], adoption.slice_agreement_ref)
            self.assertEqual(accepted["slice_title"], adoption.slice_title)
            inventory = context["slice_acceptance_inventory"]
            self.assertEqual(inventory["source"], "CORE_AGREEMENTS")
            self.assertEqual(inventory["total"], 2)
            adopted_items = [
                item for item in inventory["items"]
                if item["slice_agreement_ref"] == adoption.slice_agreement_ref
            ]
            self.assertEqual(len(adopted_items), 1)
            self.assertEqual(adopted_items[0]["authority_status"], "CANONICAL")
            self.assertEqual(adopted_items[0]["scope_status"], "ACTIVE")
            self.assertEqual(adopted_items[0]["criteria_status"], "RECORDED")
            self.assertEqual(
                adopted_items[0]["criteria_revision"]["id"],
                adoption.criteria_revision_id,
            )
            self.assertEqual(
                {item["slice_agreement_ref"] for item in inventory["items"]},
                {item.id for item in before.agreements if item.kind == "SLICE"}
                | {adoption.slice_agreement_ref},
            )
            criteria_ids = {
                item["id"] for item in accepted["criteria_revision"]["criteria"]
            }
            verifications = [
                item for item in context["criterion_verification"]
                if item["criteria_revision_id"] == adoption.criteria_revision_id
            ]
            self.assertEqual(
                {item["criterion_id"] for item in verifications},
                criteria_ids,
            )
            self.assertTrue(all(item["status"] == "UNVERIFIED" for item in verifications))
            outcome = context["slice_outcome"]
            self.assertTrue(outcome is None or outcome["status"] != "CURRENT")

            provisional = context["provisional_slice_inventory"]
            self.assertEqual(provisional["source"], "CORE_PROVISIONAL_SLICE_HEADS")
            self.assertEqual(provisional["availability"], "AVAILABLE")
            self.assertEqual(provisional["total"], 1)
            self.assertTrue(provisional["complete"])
            self.assertFalse(provisional["truncated"])
            self.assertEqual(
                [item["proposal_id"] for item in provisional["items"]],
                [fixture_context["other_head"].proposal_id],
            )
            self.assertNotIn(
                adoption.proposal_id,
                [item["proposal_id"] for item in provisional["items"]],
            )

            environment = os.environ.copy()
            environment.pop("EVOSPEC_DASHBOARD_ACCEPTANCE_CONTEXT", None)
            environment["EVOSPEC_D5_DASHBOARD_CONTEXT"] = json.dumps(
                context, separators=(",", ":"),
            )
            completed = subprocess.run(
                (node, "--test", str(Path(__file__).with_name("dashboard_acceptance_index.test.mjs"))),
                cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True,
                text=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(self._files(root), before_get)

    def test_schema23_dashboard_reads_only_the_current_core_head(self) -> None:
        with TemporaryDirectory(prefix="evospec-d4-dashboard-") as temporary:
            root = Path(temporary)

            def run(action: str, **values: object) -> dict[str, Any]:
                payload = json.dumps({"action": action, **values}, ensure_ascii=False)
                return json.loads(execute(root, payload))

            initialized = run("init", expected_revision=0)
            repository_id = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
            admitted = run(
                "accept_first_slice_session",
                expected_revision=initialized["result"]["revision"],
                repository_id=repository_id,
                session_id="ses_d4dashboard123",
                message_id="msg_d4dashboard123",
                request_text="Implement this bounded dashboard request.",
            )
            feature_id = admitted["context"]["feature_id"]
            activated = run(
                "activate_provisional_slice_writes",
                expected_revision=admitted["result"]["revision"],
                repository_id=repository_id,
                feature_id=feature_id,
            )
            first = run(
                "propose_provisional_slice",
                expected_revision=activated["result"]["revision"],
                repository_id=repository_id,
                feature_id=feature_id,
                expected_head=None,
                title="Dashboard proposal",
                outcome="The first provisional outcome",
                example=None,
                inclusions=["A narrow boundary"],
                exclusions=[],
                considerations=[],
                open_questions=[],
                criteria=[{
                    "id": "c-1",
                    "expected_result": "An observable outcome",
                    "expected_evidence": "A visible check",
                }],
            )
            first_item = first["context"]["provisional_slice_inventory"]["items"][0]
            superseded = run(
                "propose_provisional_slice",
                expected_revision=first["result"]["revision"],
                repository_id=repository_id,
                feature_id=feature_id,
                expected_head={
                    "proposal_id": first_item["proposal_id"],
                    **first_item["head"],
                },
                title="Dashboard proposal",
                outcome="The current provisional outcome",
                example=None,
                inclusions=["A narrow boundary"],
                exclusions=[],
                considerations=[],
                open_questions=[],
                criteria=[{
                    "id": "c-1",
                    "expected_result": "An observable outcome",
                    "expected_evidence": "A visible check",
                }],
            )
            expected_head = superseded["context"]["provisional_slice_inventory"]["items"][0]["head"]
            before = self._files(root)
            server, thread, base = self._start_server(root)
            try:
                snapshots: list[dict[str, Any]] = []
                for _ in range(2):
                    with urlopen(f"{base}/api/context") as response:
                        self.assertEqual(response.status, 200)
                        snapshots.append(json.loads(response.read()))
            finally:
                self._stop_server(server, thread)

            self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(self._files(root), before)
            context = snapshots[0]
            self.assertEqual(context["provisional_slice_write_ready"], True)
            inventory = context["provisional_slice_inventory"]
            self.assertEqual(inventory["total"], 1)
            self.assertTrue(inventory["complete"])
            self.assertEqual(inventory["items"][0]["head"], expected_head)
            self.assertEqual(inventory["items"][0]["title"], "Dashboard proposal")

            accepted = superseded["context"]["accepted_slice"]
            progressed = run(
                "accepted_task_progress",
                expected_revision=superseded["result"]["revision"],
                repository_id=repository_id,
                task_id=accepted["task_id"],
                feature_agreement_ref=accepted["feature_agreement_ref"],
                slice_agreement_ref=accepted["slice_agreement_ref"],
                checkpoint="Initial implementation step",
                statement="Recorded an unverified implementation update.",
                evidence_refs=["local observation"],
            )
            reviewed = run(
                "review",
                expected_revision=progressed["result"]["revision"],
                review={"verdict": "PASS", "findings": [], "safe_to_proceed": True},
            )
            verified = run(
                "verify",
                expected_revision=reviewed["result"]["revision"],
                verification={"evidence": ["focused checks passed"], "sufficient": True},
            )
            converged = run(
                "converge",
                expected_revision=verified["result"]["revision"],
                assessment={
                    "change_id": verified["result"]["change_id"],
                    "product_truth_changed": False,
                    "confidence": "HIGH",
                    "evidence": [{"source": "agent", "reference": "tests/test_dashboard.py"}],
                    "rationale": "accepted terminal work does not publish Product Truth",
                },
            )
            closed = CoreService.open(root).recover()
            self.assertEqual(converged["result"]["code"], "DONE")
            self.assertEqual(closed.revision, verified["result"]["revision"] + 3)
            after_close = self._files(root)
            server, thread, base = self._start_server(root)
            try:
                with urlopen(f"{base}/api/context") as response:
                    self.assertEqual(response.status, 200)
                    closed_context = json.loads(response.read())
                with urlopen(f"{base}/api/context") as response:
                    self.assertEqual(response.status, 200)
                    repeated_context = json.loads(response.read())
            finally:
                self._stop_server(server, thread)
            self.assertEqual(closed_context, repeated_context)
            self.assertEqual(self._files(root), after_close)
            closed_inventory = closed_context["provisional_slice_inventory"]
            self.assertEqual(closed_inventory["total"], 1)
            self.assertTrue(closed_inventory["complete"])
            self.assertEqual(closed_inventory["items"][0]["head"], expected_head)
            self.assertEqual(closed_inventory["items"][0]["title"], "Dashboard proposal")

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
            initialization = CoreService.open(root).recover().initialization
            if initialization is None:
                self.fail("initialized dashboard fixture must retain repository identity")
            repository_id = initialization.repository_id
            before_stream = self._files(root)
            server, thread, _ = self._start_server(root)
            stream = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                status, headers = stream.open()
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("content-type", "").split(";", 1)[0], "text/event-stream")

                initial = stream.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(initial.data), {"repository_id": repository_id, "revision": initial_revision})
                unchanged = stream.next_frame()
                self.assertIsNone(unchanged.event)
                self.assertTrue(unchanged.comments)
                self.assertEqual(self._files(root), before_stream)

                _ = workflow.new("Add dashboard event stream")
                changed_revision = workflow.status().revision
                self.assertGreater(changed_revision, initial_revision)
                # Un intake puede persistir varias revisiones autorizadas antes de terminar.
                # El stream puede observar alguna intermedia; esperamos la revisión final
                # sin confundir un evento válido y anterior con un fallo de sincronización.
                changed = stream.wait_for(
                    lambda frame: frame.event == "revision"
                    and json.loads(frame.data) == {"repository_id": repository_id, "revision": changed_revision}
                )
                self.assertEqual(json.loads(changed.data), {"repository_id": repository_id, "revision": changed_revision})

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
            initialization = CoreService.open(root).recover().initialization
            if initialization is None:
                self.fail("initialized dashboard fixture must retain repository identity")
            repository_id = initialization.repository_id
            server, thread, _ = self._start_server(root)
            stream = _EventStream(DEFAULT_HOST, server.server_port)
            reconnected = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                self.assertEqual(stream.open()[0], 200)
                initial = stream.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(initial.data), {"repository_id": repository_id, "revision": initial_revision})

                transition_started = time.monotonic()
                _ = workflow.new("Add rapid dashboard workflow coverage")
                _ = workflow.record_progress("implement", "dashboard", (), "verify events", (), "reconnect")
                _ = workflow.pause()
                self.assertLess(time.monotonic() - transition_started, 0.5)
                final_revision = workflow.status().revision
                self.assertGreater(final_revision, initial_revision)

                latest = stream.wait_for(
                    lambda frame: frame.event == "revision"
                    and json.loads(frame.data) == {"repository_id": repository_id, "revision": final_revision}
                )
                self.assertEqual(json.loads(latest.data), {"repository_id": repository_id, "revision": final_revision})

                after_changes = self._files(root)
                stream.close()
                self.assertEqual(reconnected.open()[0], 200)
                reconnected_initial = reconnected.wait_for(lambda frame: frame.event == "revision")
                self.assertEqual(json.loads(reconnected_initial.data), {"repository_id": repository_id, "revision": final_revision})
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
            initialization = CoreService.open(root).recover().initialization
            if initialization is None:
                self.fail("initialized dashboard fixture must retain repository identity")
            repository_id = initialization.repository_id
            server, thread, _ = self._start_server(root)
            first = _EventStream(DEFAULT_HOST, server.server_port)
            second = _EventStream(DEFAULT_HOST, server.server_port)
            try:
                self.assertEqual(first.open()[0], 200)
                self.assertEqual(second.open()[0], 200)
                for stream in (first, second):
                    initial = stream.wait_for(lambda frame: frame.event == "revision")
                    self.assertEqual(json.loads(initial.data), {"repository_id": repository_id, "revision": initial_revision})

                first.close()
                _ = workflow.new("Add independent dashboard client")
                changed_revision = workflow.status().revision
                # El cliente puede recibir revisiones intermedias ya encoladas;
                # comprobamos que converge a la revisión final persistida.
                changed = second.wait_for(
                    lambda frame: frame.event == "revision"
                    and json.loads(frame.data) == {"repository_id": repository_id, "revision": changed_revision}
                )
                self.assertEqual(json.loads(changed.data), {"repository_id": repository_id, "revision": changed_revision})
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
        shell = files("evospec").joinpath("resources", "dashboard", "index.html").read_text(encoding="utf-8")
        self.assertRegex(source, r"function\s+renderAcceptanceIndex\s*\(")
        self.assertRegex(source, r"function\s+renderTechnical\s*\(")
        self.assertIn('<details id="technical-inspection"', shell)
        self.assertRegex(source, r'new\s+EventSource\(\s*["\']/api/events["\']\s*\)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']revision["\'].*?loadContext\(')
        self.assertRegex(source, r'(?s)(?:onopen|addEventListener\(\s*["\']open["\']).*?(?:clearInterval|stop\w*Fallback)')
        self.assertRegex(source, r'(?s)(?:onerror|addEventListener\(\s*["\']error["\']).*?(?:setInterval|start\w*Fallback)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']pagehide["\'].*?\.close\(\)')
        self.assertRegex(source, r'(?s)addEventListener\(\s*["\']pagehide["\'].*?(?:clearInterval|stop\w*Fallback)')

    def test_dashboard_browser_projects_primary_acceptance_and_disclosed_diagnostics(self) -> None:
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
  const fetchCalls = [];
  window.__dashboardFixture = state.context;
  window.__dashboardFixtures = fixtures;
  window.fetch = (url, options) => {{
    fetchCalls.push({{ url, options }});
    return Promise.resolve({{ ok: true, json: async () => state.context }});
  }};
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
    fetchCalls,
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
  const click = (element) => element.click();
  const output = document.createElement("output");
  output.id = "dashboard-test-results";
  document.body.append(output);

  setTimeout(async () => {
    await sleep(20);

    check("focused skip link reserves space above the acceptance index", () => {
      const skipLink = document.querySelector(".skip-link");
      const index = document.querySelector("#active-slice-section");
      require(skipLink && index && visible(index), "the acceptance index and skip link must render");
      skipLink.focus();
      const skipStyle = getComputedStyle(skipLink);
      const skipBounds = skipLink.getBoundingClientRect();
      require(document.activeElement === skipLink, "skip link must be the active focused element");
      require(visible(skipLink), "focused skip link must be visible");
      require(skipStyle.position === "static", "focused skip link must remain in normal flow");
      const indexBounds = index.getBoundingClientRect();
      require(skipBounds.right <= indexBounds.left || skipBounds.left >= indexBounds.right || skipBounds.bottom <= indexBounds.top || skipBounds.top >= indexBounds.bottom, "focused skip link must not intersect the acceptance index");
    });

    check("acceptance index is the only primary projection", () => {
      const index = document.querySelector("#active-slice-section");
      const inspection = document.querySelector("#technical-inspection");
      require(index && index.querySelector("#active-slice-content"), "Feature/Slice acceptance must be the primary section");
      require(inspection && !inspection.open, "technical inspection must start collapsed");
      require(!index.textContent.includes("Current work") && !index.textContent.includes("Published capabilities"), "operational projections must not appear in the primary acceptance view");
      require(document.querySelector('#load-status').textContent.trim() === "Live", "a connected event stream must use the simple Live status");
    });

    check("proposals remain visibly provisional and separate from agreements", () => {
      const panel = document.querySelector(".provisional-slice-panel");
      const proposal = document.querySelector(".provisional-slice");
      require(panel && proposal, "the read-only proposal panel and current head must render");
      require(panel.textContent.includes("Provisional proposals"), "proposal content must have a distinct heading");
      require(panel.textContent.includes("User-expressed — not approval"), "user expression must not imply approval");
      require(proposal.querySelector(".status-label")?.textContent.trim() === "Provisional", "proposal status must remain provisional");
      require(proposal.textContent.includes("The proposed result is visible"), "proposal expected results must be readable");
      require(!document.querySelector(".acceptance-slice .provisional-slice"), "proposals must not be rendered as agreed Slices");
      require(!proposal.querySelector("button, input, a, [role=checkbox]"), "proposals must not offer an acceptance action");
    });

    const previousFetchCount = window.__dashboardTest.fetchCalls.length;
    click(document.querySelector("#refresh-context"));
    await sleep(20);
    check("manual refresh only requests the read-only Core context", () => {
      const calls = window.__dashboardTest.fetchCalls.slice(previousFetchCount);
      require(calls.length > 0 && calls.every((call) => call.url === "/api/context" && call.options.method === undefined), "manual refresh must use the context GET only");
    });

    check("native disclosure provides keyboard access to technical projections", () => {
      const inspection = document.querySelector("#technical-inspection");
      const summary = inspection.querySelector("summary");
      require(summary && !inspection.open, "the technical summary must be present and collapsed");
      summary.focus();
      require(document.activeElement === summary, "technical inspection summary must receive keyboard focus");
      click(summary);
      require(inspection.open, "activating the summary must disclose diagnostics");
      require(inspection.querySelector("#current-work-section") && inspection.querySelector("#blocked-section"), "work and blocker diagnostics must be inside the disclosure");
      require(inspection.querySelector("#features-list"), "capability publication must be inside the disclosure");
      require(document.querySelectorAll("#features-list > article[data-feature-id]").length === 9, "all capabilities must remain inspectable");
      require(Array.from(document.querySelectorAll("#features-list > article")).every((row) => !row.querySelector(".status-label")), "Task state must not become Feature completion");
      require(inspection.querySelector("#blocked-list .status-label")?.textContent.trim() === "Blocked", "blocked work remains available in diagnostics");
    });

    check("technical records start closed and retain the complete raw context last", () => {
      const inspection = document.querySelector("#technical-inspection");
      const details = Array.from(inspection.querySelectorAll("#technical-content details"));
      const raw = document.querySelector('#technical-raw');
      require(details.length > 1 && details.every((entry) => !entry.open), "technical records must be separate disclosures closed on first entry");
      require(details.at(-1) === raw && raw && raw.querySelectorAll('summary').length === 1 && raw.querySelector('summary').textContent.trim() === "Raw JSON" && raw.querySelectorAll('pre').length === 1, "the final technical detail must expose one Raw JSON summary and one preformatted payload");
      require(JSON.stringify(JSON.parse(raw.querySelector('pre').textContent)) === JSON.stringify(window.__dashboardFixture), "raw technical JSON must exactly equal the received fixture");
      click(raw.querySelector('summary'));
      require(raw.open, "raw technical JSON must be independently expandable");
    });

    await sleep(20);
    window.__dashboardTest.setContext(window.__dashboardFixtures.inactive);
    window.__dashboardTest.normalRefresh();
    await sleep(20);
    check("normal context refresh preserves the inspection disclosure and open raw record", () => {
      const raw = document.querySelector('#technical-raw');
      require(document.querySelector("#technical-inspection").open, "context refresh must preserve the outer technical disclosure");
      require(raw.open, "normal context refresh must retain the raw disclosure state");
      const current = document.querySelector('#current-work-section');
      require(current.hidden && !visible(current), "current work must hide when queue.active is null despite leftover progress");
    });
    window.__dashboardTest.setContext(window.__dashboardFixtures.live);
    window.__dashboardTest.revision();
    await sleep(20);
    check("SSE refresh preserves diagnostics while unpublished active work stays diagnostic", () => {
      require(document.querySelector("#technical-inspection").open, "SSE refresh must preserve the open inspection disclosure");
      require(document.querySelector('#current-work-section').hidden === false, "an active unpublished work item must remain visible as Current Work");
      require(document.querySelectorAll('#features-list > article[data-feature-id]').length === 9, "live fixture must render eight Accepted capabilities and one Blocked capability");
      require(!document.querySelector('#features-list [data-work-id="work-live-unpublished"]'), "unpublished active work must not fabricate a feature row");
      require(Array.from(document.querySelectorAll("#features-list > article")).every((row) => !row.querySelector(".status-label")), "Task statuses must not create Feature completion badges");
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
    check("polling fallback preserves the open inspection disclosure without overflow", () => {
      require(document.querySelector("#technical-inspection").open, "polling refresh must preserve the open inspection disclosure");
      require(document.querySelector('#load-status').textContent.trim() === "Polling", "a closed event stream must retain the Polling status");
      require(document.documentElement.scrollWidth <= document.documentElement.clientWidth, "dashboard must not overflow horizontally at the configured viewport width");
    });
    window.__dashboardTest.setContext({ ...window.__dashboardFixtures.linked, revision: 105 });
    window.__dashboardTest.reconnect();
    await sleep(20);
    check("SSE reconnect refresh preserves the technical disclosure", () => {
      require(document.querySelector("#technical-inspection").open, "reconnect refresh must preserve the open inspection disclosure");
      require(document.querySelector('#load-status').textContent.trim() === "Live", "a restored event stream must return to the Live status");
      require(document.querySelector('#technical-raw').open, "SSE reconnect must retain the open Raw JSON disclosure");
    });
    window.__dashboardTest.setContext({ ...window.__dashboardFixtures.empty, revision: 106 });
    window.__dashboardTest.revision();
    await sleep(20);
    check("empty capabilities retain an honest diagnostic state at all viewport widths", () => {
      const list = document.querySelector('#features-list');
      require(list.querySelectorAll('article[data-feature-id]').length === 0 && list.querySelector('.empty-state'), "empty context must render the feature empty state without phantom rows");
      require(document.documentElement.scrollWidth <= document.documentElement.clientWidth, "dashboard must not overflow horizontally at the configured viewport width");
      require(!document.querySelector("#active-slice-content").textContent.includes("Published capabilities"), "capability diagnostics must stay out of the primary acceptance view");
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
        proposal_id = "ps-" + "b" * 64
        linked: dict[str, object] = {
            "revision": 101,
            "repository_id": "repository-dashboard-fixture",
            "initialization_state": "INITIALIZED",
            "accepted_slice": None,
            "slice_acceptance_inventory": {
                "source": "CORE_AGREEMENTS",
                "total": 0,
                "complete": True,
                "truncated": False,
                "history_scope": "AGREEMENT_RECORDS_ONLY_NONEXHAUSTIVE",
                "items": [],
            },
            "provisional_slice_inventory": {
                "source": "CORE_PROVISIONAL_SLICE_HEADS",
                "availability": "AVAILABLE",
                "total": 1,
                "complete": True,
                "truncated": False,
                "items": [{
                    "proposal_id": proposal_id,
                    "repository_id": "repository-dashboard-fixture",
                    "feature_id": "feature-provisional-unmatched",
                    "title": "Read-only provisional dashboard proposal",
                    "status": "PROVISIONAL",
                    "provenance": "USER_EXPRESSED",
                    "head": {
                        "revision_id": f"{proposal_id}-r1",
                        "revision": 1,
                        "digest": "a" * 64,
                    },
                    "criteria": [{
                        "id": "c-1",
                        "expected_result": "The proposed result is visible",
                        "expected_evidence": "private-browser-proposal-evidence",
                    }],
                }],
            },
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
