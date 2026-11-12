"""Run the existing unittest suite with Windows cleanup forensics.

Usage: uv run python tests/windows_fs_forensics.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

active_forensics: Forensics | None = None
audit_installed = False
EVENTS = frozenset(
    {
        "open",
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.rename",
        "os.listdir",
        "os.scandir",
        "os.chdir",
        "subprocess.Popen",
    }
)

class Forensics:
    """Scoped, best-effort observation that cannot affect the tested operations."""

    def __init__(self) -> None:
        self.temp_root = Path(tempfile.gettempdir()).resolve()
        self.log_dir = self.temp_root / "evospec-forensics"
        self.log_dir.mkdir(exist_ok=True)
        self.log_path = self.log_dir / f"windows-fs-{os.getpid()}-{time.time_ns()}.jsonl"
        self.log = self.log_path.open("w", encoding="utf-8")
        self.events: list[dict[str, str | int | list[str]]] = []
        self.children: dict[int, dict[str, str | int]] = {}
        self.first_145 = False
        self.original_cleanup = tempfile.TemporaryDirectory.cleanup
        self.original_scandir = os.scandir
        self.originals = {name: getattr(os, name) for name in ("unlink", "remove", "rmdir", "rename", "replace")}
        self.original_popen_init = subprocess.Popen.__init__
        self.original_popen_poll = subprocess.Popen.poll
        self.patches = []

    def emit(self, kind: str, **fields: str | int | list[str]) -> None:
        """Persist a bounded record without ever changing test behavior."""
        record = {"t": time.monotonic_ns(), "kind": kind, **fields}
        self.events.append(record)
        del self.events[:-64]
        try:
            _ = self.log.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.log.flush()
        except BaseException:  # noqa: BLE001 - Diagnostic output must never change the observed operation.
            return

    def relevant(self, value: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None) -> bool:
        """Limit filesystem records to temporary test roots and EvoSpec state."""
        if value is None:
            return False
        try:
            path = Path(self.path(value)).resolve(strict=False)
        except (OSError, TypeError, ValueError):
            return False
        if path.is_relative_to(self.log_dir):
            return False
        return path.is_relative_to(self.temp_root) or any(part.casefold() == ".evospec" for part in path.parts)

    @staticmethod
    def path(value: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> str:
        """Render paths only, never file contents or subprocess environments."""
        return os.fsdecode(value)

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        """Capture CPython audit events without allowing audit-hook failures to propagate."""
        try:
            if event not in EVENTS:
                return
            match event:
                case "subprocess.Popen":
                    cwd = args[2] if len(args) > 2 else None
                    if isinstance(cwd, (str, bytes, os.PathLike)) and self.relevant(cwd):
                        self.emit("AUDIT", event=event, path=self.path(cwd))
                case _:
                    paths = [self.path(value) for value in args[:2] if isinstance(value, (str, bytes, os.PathLike)) and self.relevant(value)]
                    if paths:
                        self.emit("AUDIT", event=event, path=paths)
        except BaseException:  # noqa: BLE001 - Audit hooks must be non-raising by contract.
            return

    def snapshot(self, failed_path: str) -> None:
        """Record a read-only, bounded view of the failed path and its parent."""
        for candidate in (Path(failed_path), Path(failed_path).parent):
            try:
                with self.original_scandir(candidate) as entries:
                    names = [entry.name for _, entry in zip(range(24), entries)]
                self.emit("SNAPSHOT", path=str(candidate), entries=names)
            except OSError as error:
                self.emit("SNAPSHOT_FAIL", path=str(candidate), error=repr(error))

    def capture_145(self, operation: str, failed_path: str, error: BaseException) -> None:
        """Capture the first exact Windows directory-not-empty failure with nearby state."""
        if self.first_145 or getattr(error, "winerror", None) != 145:
            return
        self.first_145 = True
        active_children = [str(pid) for pid, child in self.children.items() if child.get("poll") == "running"]
        self.emit(
            "WINERROR_145",
            operation=operation,
            path=failed_path,
            error=repr(error),
            recent=[f"{record['kind']}:{record.get('operation', record.get('event', record.get('path', '')))}" for record in self.events[-16:]],
            children=active_children,
        )
        self.snapshot(failed_path)

    def wrap_operation(self, name: str, original):
        """Create a transparent destructive-operation observer."""
        def wrapped(*args, **kwargs):
            failed_path = self.path(args[0]) if args and isinstance(args[0], (str, bytes, os.PathLike)) else "<non-path>"
            self.emit("BEGIN", operation=name, path=failed_path)
            try:
                result = original(*args, **kwargs)
            except BaseException as error:  # Re-raise the exact original exception unchanged.
                self.emit("FAIL", operation=name, path=failed_path, error=repr(error))
                self.capture_145(name, failed_path, error)
                raise
            self.emit("SUCCESS", operation=name, path=failed_path)
            return result
        return wrapped

    def install(self) -> None:
        """Install wrappers only for this suite execution."""
        global active_forensics, audit_installed
        active_forensics = self
        if not audit_installed:
            sys.addaudithook(audit_hook)
            audit_installed = True

        def cleanup(directory) -> None:
            path = self.path(directory.name)
            self.emit("CLEANUP_BEGIN", path=path)
            try:
                self.original_cleanup(directory)
            except BaseException as error:  # Re-raise the exact original exception unchanged.
                self.emit("CLEANUP_FAIL", path=path, error=repr(error))
                self.capture_145("TemporaryDirectory.cleanup", path, error)
                raise
            self.emit("CLEANUP_END", path=path)

        def popen_init(process, *args, **kwargs) -> None:
            self.original_popen_init(process, *args, **kwargs)
            try:
                command = process.args
                entry = command[0] if isinstance(command, (list, tuple)) and command else command
                executable = Path(os.fsdecode(entry)).name if isinstance(entry, (str, bytes, os.PathLike)) else "<unavailable>"
                self.children[process.pid] = {"executable": executable, "poll": "running"}
                self.emit("POPEN", pid=process.pid, executable=executable)
            except BaseException:  # noqa: BLE001 - Observation must not alter a successful child creation.
                self.emit("POPEN", pid=process.pid, executable="<unavailable>")

        def poll(process):
            result = self.original_popen_poll(process)
            child = self.children.get(process.pid)
            if child is not None:
                child["poll"] = "running" if result is None else str(result)
                self.emit("POLL", pid=process.pid, result=child["poll"])
            return result

        self.patches = [
            *(patch.object(os, name, self.wrap_operation(name, original)) for name, original in self.originals.items()),
            patch.object(tempfile.TemporaryDirectory, "cleanup", cleanup),
            patch.object(subprocess.Popen, "__init__", popen_init),
            patch.object(subprocess.Popen, "poll", poll),
        ]
        for patcher in self.patches:
            _ = patcher.start()

    def restore(self) -> None:
        """Restore every scoped monkeypatch even when the suite fails."""
        global active_forensics
        for patcher in reversed(self.patches):
            patcher.stop()
        active_forensics = None
        self.log.close()

    def summary(self, successful: bool) -> None:
        """Print the classification and evidence location without dumping sensitive command data."""
        classification = "clean" if not self.first_145 else "winerror-145-with-running-child" if any(child.get("poll") == "running" for child in self.children.values()) else "winerror-145-no-running-child-observed"
        print(f"FORENSICS result={'PASS' if successful else 'FAIL'} classification={classification} events={len(self.events)} log={self.log_path}")

def audit_hook(event: str, args: tuple[object, ...]) -> None:
    """Global CPython audit entry point; it remains inert outside a harness run."""
    if active_forensics is not None:
        active_forensics.audit(event, args)


def run_once() -> int:
    """Run normal unittest discovery once under scoped instrumentation."""
    forensic = Forensics()
    forensic.install()
    successful = False
    try:
        root = Path(__file__).resolve().parents[1]
        os.chdir(root)
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        successful = result.wasSuccessful()
    finally:
        forensic.restore()
    forensic.summary(successful)
    return 0 if successful else 1


def main() -> int:
    """Run three independent process executions by default, without retries or waits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="internal single-suite execution")
    parser.add_argument("--runs", type=int, default=3, help="independent suite executions")
    arguments = parser.parse_args()
    if arguments.once:
        return run_once()
    if arguments.runs < 1:
        parser.error("--runs must be positive")
    statuses = []
    for number in range(1, arguments.runs + 1):
        print(f"FORENSICS run={number}/{arguments.runs}")
        statuses.append(subprocess.run([sys.executable, __file__, "--once"], check=False).returncode)
    return 0 if all(status == 0 for status in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
