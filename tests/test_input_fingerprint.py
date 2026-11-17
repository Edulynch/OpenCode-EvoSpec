from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evospec import input_fingerprint
from evospec.input_fingerprint import (
    INPUT_FINGERPRINT_VERSION,
    INPUT_POLICY_DIGEST,
    INPUT_POLICY_ID,
    InputFingerprintError,
    repository_input_snapshot,
    sanitized_test_environment,
    test_environment_profile_digest,
)


def repository_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


class InputFingerprintTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.root = Path()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="evospec-r3b-fingerprint-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def snapshot(self):
        return repository_input_snapshot(self.root, repository_id(self.root))

    def test_01_hashes_all_nonexcluded_regular_files_in_canonical_order(self) -> None:
        (self.root / ".git").mkdir()
        (self.root / ".git" / "ignored-by-policy").write_text("git metadata")
        (self.root / ".evospec").mkdir()
        (self.root / ".evospec" / "state.json").write_text("managed state")
        (self.root / ".gitignore").write_text("ignored.txt\n")
        (self.root / "ignored.txt").write_text("still an input")
        (self.root / "z.txt").write_bytes(b"z")
        (self.root / "a.txt").write_bytes(b"a")
        snapshot = self.snapshot()

        self.assertEqual(snapshot.version, INPUT_FINGERPRINT_VERSION)
        self.assertEqual(snapshot.policy_id, INPUT_POLICY_ID)
        self.assertEqual(snapshot.policy_digest, INPUT_POLICY_DIGEST)
        self.assertEqual(snapshot.file_count, 4)
        self.assertEqual(snapshot.total_bytes, sum(len(item.read_bytes()) for item in (
            self.root / ".gitignore", self.root / "ignored.txt", self.root / "z.txt", self.root / "a.txt",
        )))
        entries = []
        for item in (self.root / ".gitignore", self.root / "a.txt", self.root / "ignored.txt", self.root / "z.txt"):
            info = item.stat()
            entries.append([
                item.relative_to(self.root).as_posix(),
                info.st_size,
                bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)),
                hashlib.sha256(item.read_bytes()).hexdigest(),
            ])
        root_identity = repository_id(self.root)
        expected = hashlib.sha256(json.dumps({
            "version": INPUT_FINGERPRINT_VERSION,
            "policy": {"id": INPUT_POLICY_ID, "digest": INPUT_POLICY_DIGEST},
            "root_identity": root_identity,
            "repository_id": root_identity,
            "files": entries,
            "directories": [],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(snapshot.fingerprint, expected)

    def test_02_ignored_and_untracked_content_and_executable_bit_change_fingerprint(self) -> None:
        source = self.root / "ignored.bin"
        source.write_bytes(b"first")
        first = self.snapshot().fingerprint
        source.write_bytes(b"other")
        second = self.snapshot().fingerprint
        self.assertNotEqual(first, second)
        if os.name != "nt":
            source.chmod(source.stat().st_mode | stat.S_IXUSR)
            self.assertNotEqual(second, self.snapshot().fingerprint)

    def test_02b_empty_directory_presence_changes_fingerprint(self) -> None:
        before = self.snapshot()
        directory = self.root / "empty"
        directory.mkdir()
        after = self.snapshot()
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(after.directory_paths, ("empty",))
        directory.rmdir()
        self.assertEqual(self.snapshot().fingerprint, before.fingerprint)

    def test_03_only_exact_root_managed_names_are_excluded(self) -> None:
        (self.root / ".GIT").write_text("included")
        (self.root / ".EVOSPEC").write_text("included")
        self.assertEqual(self.snapshot().file_count, 2)

    def test_04_symlinks_hardlinks_and_casefold_collisions_fail_closed(self) -> None:
        target = self.root / "target.txt"
        target.write_text("input")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(InputFingerprintError):
            self.snapshot()
        link.unlink()
        alias = self.root / "alias.txt"
        try:
            os.link(target, alias)
        except OSError:
            self.skipTest("hard links are unavailable")
        with self.assertRaises(InputFingerprintError):
            self.snapshot()
        alias.unlink()

    def test_05_casefold_collisions_fail_closed(self) -> None:
        (self.root / "Case.txt").write_text("one")
        first = self.root / "Case.txt"
        second = self.root / "case.txt"
        second.write_text("two")
        if first.samefile(second):
            self.skipTest("repository filesystem is case-insensitive")
        with self.assertRaises(InputFingerprintError):
            self.snapshot()

    def test_06_special_files_and_unreadable_entries_fail_closed(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO creation is unavailable")
        os.mkfifo(self.root / "pipe")
        with self.assertRaises(InputFingerprintError):
            self.snapshot()

    def test_07_file_and_entry_limits_fail_without_partial_snapshot(self) -> None:
        (self.root / "input").write_text("bounded")
        with patch.object(input_fingerprint, "MAX_INPUT_FILES", 0):
            with self.assertRaises(InputFingerprintError):
                self.snapshot()
        with patch.object(input_fingerprint, "MAX_INPUT_ENTRIES", 0):
            with self.assertRaises(InputFingerprintError):
                self.snapshot()

    def test_08_byte_and_depth_limits_fail_without_partial_snapshot(self) -> None:
        (self.root / "input").write_bytes(b"too much")
        with patch.object(input_fingerprint, "MAX_INPUT_BYTES", 2):
            with self.assertRaises(InputFingerprintError):
                self.snapshot()
        (self.root / "nested").mkdir()
        (self.root / "nested" / "input").write_bytes(b"x")
        with patch.object(input_fingerprint, "MAX_INPUT_DEPTH", 0):
            with self.assertRaises(InputFingerprintError):
                self.snapshot()

    def test_09_mutation_between_scans_and_wrong_root_identity_fail(self) -> None:
        (self.root / "input").write_text("before")
        original = input_fingerprint._scan_once
        calls = 0

        def mutate_between(root: Path):
            nonlocal calls
            calls += 1
            result = original(root)
            if calls == 1:
                (self.root / "input").write_text("after")
            return result

        with patch.object(input_fingerprint, "_scan_once", side_effect=mutate_between):
            with self.assertRaises(InputFingerprintError):
                self.snapshot()
        with self.assertRaises(InputFingerprintError):
            repository_input_snapshot(self.root, "wrong-root")

    def test_10_environment_profile_excludes_ambient_home_credentials_and_config(self) -> None:
        runtime = str(Path(sys.executable).resolve())
        environment = sanitized_test_environment(
            self.root,
            runtime,
            {
                "PATH": "ambient-path",
                "HOME": "secret-home",
                "APPDATA": "user-config",
                "TOKEN": "secret-token",
            },
        )
        self.assertEqual(environment["PATH"], str(Path(runtime).parent))
        self.assertEqual(environment["TEMP"], str(self.root.resolve()))
        self.assertEqual(environment["TMP"], str(self.root.resolve()))
        self.assertNotIn("HOME", environment)
        self.assertNotIn("APPDATA", environment)
        self.assertNotIn("TOKEN", environment)
        digest = test_environment_profile_digest(environment)
        self.assertEqual(len(digest), 64)
        self.assertNotIn("secret-token", digest)


if __name__ == "__main__":
    unittest.main()
