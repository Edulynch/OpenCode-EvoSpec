"""Verify a bounded personal-tasks objective in an isolated real Chromium page.

Evidence identity is ``core-personal-tasks-browser-v1``. Real-browser proof is
supported only on Windows with a verified Job Object; Linux returns
``REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE`` before creating a worker.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, cast

_BrowserErrorCode: TypeAlias = Literal[
    "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE",
    "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED",
]
_UNAVAILABLE: _BrowserErrorCode = "REQUEST_OBJECTIVE_BROWSER_UNAVAILABLE"
_NOT_VERIFIED: _BrowserErrorCode = "REQUEST_OBJECTIVE_CONTENT_NOT_VERIFIED"
_MAX_CONTENT_BYTES = 262_144
_MAX_TASK_ROWS = 128
_MAX_WORKER_OUTPUT = 128
_WINDOWS_JOB_MEMORY_LIMIT_BYTES = 2 * 1024**3
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WORKER_MAGIC = b"EVOB"
_WORKER_ARGUMENT = "--_evospec-browser-worker"
_WORKER_BROWSER = 0
_WORKER_TREE_TEST = 1
_WORKER_OUTPUT_TEST = 2
_TOTAL_TIMEOUT_SECONDS = 30.0
_CLEANUP_RESERVE_SECONDS = 5.0
_APP_URL = "https://personal-tasks.objective.invalid/index.html"
_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; frame-src 'none'; child-src 'none'; "
    "worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "font-src 'none'; media-src 'none'; manifest-src 'none'"
)


class _WindowsPopenHandle(Protocol):
    _handle: int


class _WorkerOutputPipe(Protocol):
    def read1(self, size: int = -1, /) -> bytes: ...


class BrowserObjectiveError(RuntimeError):
    """Stable failure code for unavailable browser evidence or failed behavior."""

    def __init__(self, code: _BrowserErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _not_verified(message: str) -> BrowserObjectiveError:
    return BrowserObjectiveError(_NOT_VERIFIED, message)


def _unavailable(message: str) -> BrowserObjectiveError:
    return BrowserObjectiveError(_UNAVAILABLE, message)


def _load_playwright() -> Any:
    try:
        module = importlib.import_module("playwright.sync_api")
        return getattr(module, "sync_playwright")
    except Exception as exc:
        raise _unavailable(
            "The optional Playwright browser runtime is unavailable."
        ) from exc


class _WindowsJob:
    """Windows whole-tree containment with a verified collective memory cap."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise _unavailable("A Windows kill-on-close job object is unavailable.")
        self._kernel = kernel
        self._handle = handle
        self._accounting_type = BasicAccountingInformation
        self._extended_limit_type = ExtendedLimitInformation
        try:
            limits = ExtendedLimitInformation()
            # The job-wide cap includes Python, Playwright, Chromium and descendants.
            # No breakaway flags are set.
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            limits.JobMemoryLimit = _WINDOWS_JOB_MEMORY_LIMIT_BYTES
            if not kernel.SetInformationJobObject(
                handle,
                9,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise _unavailable(
                    "Windows job memory and kill-on-close limits could not be enabled."
                )
            confirmed = self._query_extended_limits()
            required_flags = (
                _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if (
                confirmed is None
                or (
                    int(confirmed.BasicLimitInformation.LimitFlags) & required_flags
                )
                != required_flags
                or (
                    int(confirmed.BasicLimitInformation.LimitFlags)
                    & (
                        _JOB_OBJECT_LIMIT_BREAKAWAY_OK
                        | _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
                    )
                )
                or int(confirmed.JobMemoryLimit)
                != _WINDOWS_JOB_MEMORY_LIMIT_BYTES
            ):
                raise _unavailable(
                    "Windows did not confirm the required job-wide memory and kill limits."
                )
        except Exception:
            kernel.CloseHandle(handle)
            self._handle = None
            raise

    def _query_extended_limits(self) -> Any | None:
        import ctypes

        info = self._extended_limit_type()
        if not self._kernel.QueryInformationJobObject(
            self._handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return None
        return info

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(
            cast(_WindowsPopenHandle, cast(object, process))._handle
        )
        if not self._kernel.AssignProcessToJobObject(self._handle, process_handle):
            raise _unavailable("The verifier worker could not be assigned to its job.")
        is_contained = wintypes.BOOL()
        if not self._kernel.IsProcessInJob(
            process_handle,
            self._handle,
            is_contained,
        ) or not is_contained.value:
            raise _unavailable("Windows did not confirm verifier job membership.")

    def _active_processes(self) -> int | None:
        import ctypes

        info = self._accounting_type()
        if not self._kernel.QueryInformationJobObject(
            self._handle,
            1,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return None
        return int(info.ActiveProcesses)

    def kill_and_confirm(self, deadline: float) -> bool:
        active = self._active_processes()
        if active is None:
            return False
        if active and not self._kernel.TerminateJobObject(self._handle, 1):
            return False
        while time.monotonic() < deadline:
            active = self._active_processes()
            if active == 0:
                return True
            if active is None:
                return False
            time.sleep(0.02)
        return self._active_processes() == 0

    def close(self, _deadline: float) -> bool:
        if self._handle is None:
            return True
        result = bool(self._kernel.CloseHandle(self._handle))
        self._handle = None
        return result


def _create_containment() -> _WindowsJob:
    if os.name == "nt":
        return _WindowsJob()
    raise _unavailable(
        "Bounded browser verification is supported only with Windows Job Objects."
    )


def _worker_environment() -> dict[str, str]:
    keys = (
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "USERPROFILE",
        "PLAYWRIGHT_BROWSERS_PATH",
    )
    return {key: os.environ[key] for key in keys if key in os.environ}


@dataclass(frozen=True, slots=True)
class _WorkerOutcome:
    output: bytes
    exit_code: int | None
    timed_out: bool


def _run_isolated_worker(
    content: bytes,
    operations: tuple[str, ...],
    *,
    worker_mode: int = _WORKER_BROWSER,
    timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
) -> _WorkerOutcome:
    started = time.monotonic()
    deadline = started + timeout_seconds
    cleanup_reserve = min(_CLEANUP_RESERVE_SECONDS, timeout_seconds / 3)
    work_deadline = deadline - cleanup_reserve
    containment = _create_containment()
    process: subprocess.Popen[bytes] | None = None
    attached = False
    stdout = bytearray()
    output_overflow = False
    thread_errors: list[bool] = []
    reader: threading.Thread | None = None
    writer: threading.Thread | None = None
    timed_out = False
    pending: BrowserObjectiveError | None = None
    worker_exit: int | None = None

    def drain_output() -> None:
        nonlocal output_overflow
        try:
            if process is None or process.stdout is None:
                thread_errors.append(True)
                return
            output_pipe = cast(_WorkerOutputPipe, cast(object, process.stdout))
            while True:
                chunk = output_pipe.read1(256)
                if not chunk:
                    return
                available = _MAX_WORKER_OUTPUT - len(stdout)
                if available > 0:
                    stdout.extend(chunk[:available])
                if len(chunk) > available:
                    output_overflow = True
        except Exception:
            thread_errors.append(True)

    def send_input(payload: bytes) -> None:
        try:
            if process is None or process.stdin is None:
                thread_errors.append(True)
                return
            process.stdin.write(payload)
            process.stdin.flush()
            process.stdin.close()
        except Exception:
            thread_errors.append(True)

    try:
        if time.monotonic() >= work_deadline:
            raise _unavailable("The verifier startup budget expired.")
        script = Path(__file__).resolve(strict=True)
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(script), _WORKER_ARGUMENT],
            cwd=str(script.parent.parent),
            env=_worker_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        # The Windows job limit is already set; the worker blocks on stdin until
        # this confirmed assignment is complete.
        containment.attach(process)
        attached = True
        if process.stdout is None or process.stdin is None:
            raise _unavailable("The verifier worker pipes are unavailable.")
        reader = threading.Thread(target=drain_output, daemon=True)
        reader.start()
        operation_code = 1 if "complete" in operations else 0
        payload = (
            _WORKER_MAGIC
            + bytes((worker_mode, operation_code))
            + len(content).to_bytes(4, "big")
            + content
        )
        if time.monotonic() >= work_deadline:
            timed_out = True
        else:
            writer = threading.Thread(
                target=send_input,
                args=(payload,),
                daemon=True,
            )
            writer.start()
            try:
                process.wait(timeout=max(0.0, work_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    except BrowserObjectiveError as exc:
        pending = exc
    except Exception as exc:
        pending = _unavailable("The contained verifier worker could not start safely.")
        pending.__cause__ = exc
    finally:
        cleanup_ok = True
        if process is not None and not attached:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    cleanup_ok = False
        try:
            cleanup_ok = containment.kill_and_confirm(deadline) and cleanup_ok
        except Exception:
            cleanup_ok = False
        if process is not None:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
                worker_exit = process.returncode
            except subprocess.TimeoutExpired:
                cleanup_ok = False
            for thread in (writer, reader):
                if thread is not None:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                    if thread.is_alive():
                        cleanup_ok = False
            if process.stdin is not None and (
                writer is None or not writer.is_alive()
            ):
                try:
                    process.stdin.close()
                except OSError:
                    cleanup_ok = False
            if process.stdout is not None and (
                reader is None or not reader.is_alive()
            ):
                try:
                    process.stdout.close()
                except OSError:
                    cleanup_ok = False
        try:
            cleanup_ok = containment.close(deadline) and cleanup_ok
        except Exception:
            cleanup_ok = False

    if not cleanup_ok:
        raise _unavailable("Whole-tree worker cleanup could not be confirmed.")
    if pending is not None:
        raise pending
    if output_overflow or thread_errors:
        raise _unavailable("The verifier worker exceeded bounded I/O.")
    return _WorkerOutcome(bytes(stdout), worker_exit, timed_out)


def _run_containment_tree_test() -> _WorkerOutcome:
    return _run_isolated_worker(
        b"",
        ("create", "consult"),
        worker_mode=_WORKER_TREE_TEST,
        timeout_seconds=3.0,
    )


def _run_worker_output_test() -> _WorkerOutcome:
    return _run_isolated_worker(
        b"",
        ("create", "consult"),
        worker_mode=_WORKER_OUTPUT_TEST,
        timeout_seconds=3.0,
    )


def _matching_rows(
    page: Any,
    task_text: str,
    *,
    visible_only: bool = True,
) -> list[int]:
    rows = page.locator("[data-evo-task]")
    count = rows.count()
    if count > _MAX_TASK_ROWS:
        raise _not_verified("The task list exceeds the bounded row limit.")
    matches: list[int] = []
    for index in range(count):
        row = rows.nth(index)
        exact_text = row.get_by_text(task_text, exact=True)
        if exact_text.count() == 1 and (
            not visible_only
            or (row.is_visible() and exact_text.is_visible())
        ):
            matches.append(index)
    return matches


def _verify_in_context(context: Any, content: bytes, operations: tuple[str, ...]) -> None:
    violations = False
    served_index = False
    page: Any | None = None

    def mark_violation() -> None:
        nonlocal violations
        violations = True

    def route_request(route: Any) -> None:
        nonlocal served_index
        request = route.request
        if (
            not served_index
            and page is not None
            and request.url == _APP_URL
            and request.method == "GET"
            and request.resource_type == "document"
            and request.is_navigation_request()
            and request.frame == page.main_frame
        ):
            served_index = True
            route.fulfill(
                status=200,
                body=content,
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "content-security-policy": _CONTENT_SECURITY_POLICY,
                    "x-content-type-options": "nosniff",
                },
            )
            return
        mark_violation()
        route.abort()

    def reject_websocket(_websocket: Any) -> None:
        mark_violation()

    try:
        context.route("**/*", route_request)
        context.route_web_socket("**/*", reject_websocket)
        page = context.new_page()
    except Exception as exc:
        raise _unavailable(
            "Required browser isolation controls are unavailable."
        ) from exc
    if page is None:
        raise _unavailable("An isolated browser page could not be created.")
    active_page = page

    def observe_request(request: Any) -> None:
        if (
            request.url != _APP_URL
            or request.method != "GET"
            or request.resource_type != "document"
        ):
            mark_violation()

    def observe_navigation(frame: Any) -> None:
        if page is not None and frame == page.main_frame and frame.url != _APP_URL:
            mark_violation()

    def observe_console(message: Any) -> None:
        if message.type == "error":
            mark_violation()

    def observe_popup(popup: Any) -> None:
        mark_violation()
        try:
            popup.close()
        except Exception:
            pass

    active_page.on("request", observe_request)
    active_page.on("framenavigated", observe_navigation)
    active_page.on("console", observe_console)
    active_page.on("pageerror", lambda _error: mark_violation())
    active_page.on("popup", observe_popup)
    active_page.on("download", lambda _download: mark_violation())
    active_page.set_default_timeout(5_000)
    active_page.set_default_navigation_timeout(10_000)

    try:
        response = active_page.goto(_APP_URL, wait_until="load")
        if response is None or response.status != 200 or not served_index:
            raise _not_verified("The exact local index document did not load.")
        if violations:
            raise _not_verified("The page attempted behavior outside the local proof.")

        task_input = active_page.locator('[data-evo-action="task-input"]')
        add_button = active_page.locator('[data-evo-action="add-task"]')
        if (
            task_input.count() != 1
            or add_button.count() != 1
            or not task_input.is_visible()
            or not task_input.is_enabled()
            or not add_button.is_visible()
            or not add_button.is_enabled()
        ):
            raise _not_verified("The page has no single visible task-entry interaction.")

        task_texts = (
            f"EvoSpec task {secrets.token_hex(16)}",
            f"EvoSpec task {secrets.token_hex(16)}",
        )
        if task_texts[0] == task_texts[1]:
            raise _not_verified("Distinct verifier task inputs could not be generated.")

        for task_text in task_texts:
            if _matching_rows(active_page, task_text, visible_only=False):
                raise _not_verified("A verifier task existed before the add action.")
            task_input.fill(task_text)
            if _matching_rows(active_page, task_text, visible_only=False):
                raise _not_verified("The task appeared before the add action.")
            add_button.click()
            active_page.get_by_text(task_text, exact=True).wait_for(
                state="visible", timeout=5_000
            )
            if len(_matching_rows(active_page, task_text)) != 1:
                raise _not_verified("The add action did not create one visible task row.")

        first_rows = _matching_rows(active_page, task_texts[0])
        second_rows = _matching_rows(active_page, task_texts[1])
        if (
            len(first_rows) != 1
            or len(second_rows) != 1
            or first_rows[0] == second_rows[0]
        ):
            raise _not_verified("The two added tasks are not distinct visible rows.")

        if "complete" in operations:
            first_row = active_page.locator("[data-evo-task]").nth(first_rows[0])
            second_row = active_page.locator("[data-evo-task]").nth(second_rows[0])
            first_checkbox = first_row.locator(
                'input[type="checkbox"][data-evo-action="complete-task"]'
            )
            second_checkbox = second_row.locator(
                'input[type="checkbox"][data-evo-action="complete-task"]'
            )
            if (
                first_checkbox.count() != 1
                or second_checkbox.count() != 1
                or not first_checkbox.is_visible()
                or not second_checkbox.is_visible()
                or first_checkbox.is_checked()
                or second_checkbox.is_checked()
            ):
                raise _not_verified("Each task needs its own pending native checkbox.")
            first_checkbox.check()
            if not first_checkbox.is_checked() or second_checkbox.is_checked():
                raise _not_verified("Completion did not affect only the selected task.")

        if violations:
            raise _not_verified("The page attempted behavior outside the local proof.")
    except BrowserObjectiveError:
        raise
    except Exception as exc:
        raise _not_verified("The page did not satisfy the observable task behavior.") from exc


def _verify_personal_tasks_in_browser(
    content: bytes, operations: tuple[str, ...]
) -> None:
    sync_playwright = _load_playwright()
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=True,
                    chromium_sandbox=True,
                    timeout=15_000,
                )
            except Exception as exc:
                raise _unavailable(
                    "Sandboxed bundled Chromium could not be launched."
                ) from exc
            try:
                context = browser.new_context(
                    accept_downloads=False,
                    offline=True,
                    permissions=[],
                    service_workers="block",
                    viewport={"width": 1280, "height": 800},
                )
            except Exception as exc:
                browser.close()
                raise _unavailable(
                    "A private isolated browser context is unavailable."
                ) from exc
            try:
                try:
                    _verify_in_context(context, content, operations)
                finally:
                    context.close()
            finally:
                browser.close()
    except BrowserObjectiveError:
        raise
    except Exception as exc:
        raise _unavailable(
            "The isolated browser runtime could not complete safely."
        ) from exc


def _read_exact(stream: Any, size: int) -> bytes | None:
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            return None
        result.extend(chunk)
    return bytes(result)


def _worker_result(message: bytes) -> None:
    sys.stdout.buffer.write(message + b"\n")
    sys.stdout.buffer.flush()


def _worker_main() -> int:
    try:
        stream = sys.stdin.buffer
        header = _read_exact(stream, 10)
        if header is None or header[:4] != _WORKER_MAGIC:
            _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
            return 0
        mode, operation_code = header[4], header[5]
        content_length = int.from_bytes(header[6:10], "big")
        if content_length > _MAX_CONTENT_BYTES:
            _worker_result(f"ERR {_NOT_VERIFIED}".encode("ascii"))
            return 0
        content = _read_exact(stream, content_length)
        if content is None or stream.read(1):
            _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
            return 0
        if mode == _WORKER_TREE_TEST:
            if content or operation_code != 0:
                _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
                return 0
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import time; print('READY', flush=True); time.sleep(60)",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            if child.stdout is None or child.stdout.readline().strip() != b"READY":
                return 1
            child.stdout.close()
            _worker_result(b"TREE")
            time.sleep(60)
            return 0
        if mode == _WORKER_OUTPUT_TEST:
            if content or operation_code != 0:
                _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
                return 0
            sys.stdout.buffer.write(b"X" * (_MAX_WORKER_OUTPUT * 2))
            sys.stdout.buffer.flush()
            return 0
        if mode != _WORKER_BROWSER or operation_code not in (0, 1):
            _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
            return 0
        operations = (
            ("create", "consult", "complete")
            if operation_code == 1
            else ("create", "consult")
        )
        if not content:
            _worker_result(f"ERR {_NOT_VERIFIED}".encode("ascii"))
            return 0
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _worker_result(f"ERR {_NOT_VERIFIED}".encode("ascii"))
            return 0
        if "\x00" in decoded:
            _worker_result(f"ERR {_NOT_VERIFIED}".encode("ascii"))
            return 0
        _verify_personal_tasks_in_browser(content, operations)
        _worker_result(b"OK")
        return 0
    except BrowserObjectiveError as exc:
        _worker_result(f"ERR {exc.code}".encode("ascii"))
        return 0
    except Exception:
        _worker_result(f"ERR {_UNAVAILABLE}".encode("ascii"))
        return 0


def verify_personal_tasks(content: bytes, operations: tuple[str, ...]) -> str:
    """Return the exact-byte SHA-256 only after bounded, contained browser proof.

    ``operations`` must be exactly ``("create", "consult")`` or that tuple
    followed by ``"complete"``. Browser execution is delegated to a
    time-bounded worker in a confirmed whole-tree OS containment.
    """
    if (
        type(content) is not bytes
        or not content
        or len(content) > _MAX_CONTENT_BYTES
        or type(operations) is not tuple
        or operations
        not in (("create", "consult"), ("create", "consult", "complete"))
    ):
        raise _not_verified("The bounded browser objective input is malformed.")
    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _not_verified("The index document is not strict UTF-8.") from exc
    if "\x00" in decoded:
        raise _not_verified("The index document contains invalid NUL content.")

    outcome = _run_isolated_worker(content, operations)
    if outcome.timed_out:
        raise _unavailable("The browser verifier exceeded its wall-clock budget.")
    if outcome.exit_code != 0:
        raise _unavailable("The contained browser verifier exited abnormally.")
    if outcome.output == b"OK\n":
        pass
    elif outcome.output == f"ERR {_NOT_VERIFIED}\n".encode("ascii"):
        raise _not_verified("The browser did not verify the requested behavior.")
    elif outcome.output == f"ERR {_UNAVAILABLE}\n".encode("ascii"):
        raise _unavailable("The optional browser runtime or isolation is unavailable.")
    else:
        raise _unavailable("The verifier worker returned an invalid result.")
    return hashlib.sha256(content).hexdigest()


__all__ = ("BrowserObjectiveError", "verify_personal_tasks")


if __name__ == "__main__":
    if sys.argv[1:] != [_WORKER_ARGUMENT]:
        raise SystemExit(2)
    raise SystemExit(_worker_main())
