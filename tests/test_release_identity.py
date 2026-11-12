from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from typing import Final

from tests._tempdir import TemporaryDirectory

VALIDATOR: Final = Path(__file__).parents[1] / "scripts" / "validate_release_identity.py"


class ReleaseIdentityTests(unittest.TestCase):
    def test_identity_accepts_annotated_head_tag_matching_pep440_prerelease_version(self) -> None:
        # Given: a repository with an annotated HEAD tag exactly matching its prerelease version
        with TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary), "0.1.0rc1")
            self._git(repository, "tag", "-a", "v0.1.0rc1", "-m", "release")

            # When: release identity is validated
            result = self._validate(repository)

            # Then: exact PEP 440 version identity is accepted
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_identity_rejects_lightweight_matching_tag(self) -> None:
        # Given: a repository with a lightweight tag that otherwise matches its version
        with TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary), "1.2.3")
            self._git(repository, "tag", "v1.2.3")

            # When: release identity is validated
            result = self._validate(repository)

            # Then: an annotated tag is required
            self.assertEqual(result.returncode, 1)
            self.assertIn("release tag v1.2.3 must be annotated", result.stderr)

    def test_identity_rejects_malformed_or_mismatched_tag_name(self) -> None:
        # Given: repositories whose HEAD tags omit the v prefix or use another version
        for tag in ("1.2.3", "v1.2.4"):
            with self.subTest(tag=tag), TemporaryDirectory() as temporary:
                repository = self._repository(Path(temporary), "1.2.3")
                self._git(repository, "tag", "-a", tag, "-m", "release")

                # When: release identity is validated
                result = self._validate(repository)

                # Then: only the exact v<pyproject version> tag can satisfy the gate
                self.assertEqual(result.returncode, 1)
                self.assertIn("missing release tag v1.2.3", result.stderr)

    def test_identity_rejects_annotated_matching_tag_not_pointing_at_head(self) -> None:
        # Given: a matching annotated tag on an earlier commit
        with TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary), "1.2.3")
            self._git(repository, "tag", "-a", "v1.2.3", "-m", "release")
            _ = (repository / "release.txt").write_text("later commit\n", encoding="utf-8")
            self._git(repository, "add", "release.txt")
            self._git(repository, "commit", "-m", "later")

            # When: release identity is validated
            result = self._validate(repository)

            # Then: the annotated tag must point to HEAD
            self.assertEqual(result.returncode, 1)
            self.assertIn("release tag v1.2.3 must point at HEAD", result.stderr)

    @staticmethod
    def _repository(root: Path, version: str) -> Path:
        ReleaseIdentityTests._git(root, "init")
        ReleaseIdentityTests._git(root, "config", "user.name", "Release Test")
        ReleaseIdentityTests._git(root, "config", "user.email", "release@example.invalid")
        _ = (root / "pyproject.toml").write_text(
            f'[project]\nname = "release-test"\nversion = "{version}"\n', encoding="utf-8"
        )
        ReleaseIdentityTests._git(root, "add", "pyproject.toml")
        ReleaseIdentityTests._git(root, "commit", "-m", "initial")
        return root

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        _ = subprocess.run(("git", *arguments), cwd=repository, check=True)

    @staticmethod
    def _validate(repository: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (sys.executable, str(VALIDATOR)),
            cwd=repository,
            capture_output=True,
            check=False,
            text=True,
        )
