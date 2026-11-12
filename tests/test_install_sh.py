from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import unittest
import zipfile
from pathlib import Path

from tests._tempdir import TemporaryDirectory

ROOT = Path(__file__).parents[1]


class ShellInstallerChecksumPortabilityTests(unittest.TestCase):
    def test_installer_accepts_linux_x86_64_only(self) -> None:
        # Given: the active POSIX installer source
        installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        # When: its platform dispatch is inspected

        # Then: deferred macOS host detection remains reusable but cannot select a current asset
        self.assertIn("Linux-x86_64) platform=linux-x86_64", installer)
        self.assertIn("Darwin-arm64) platform=macos-arm64", installer)
        self.assertIn("Darwin-x86_64) platform=macos-x86_64", installer)
        self.assertIn("linux-x86_64) ;;", installer)


@unittest.skipUnless(
    platform.system() == "Linux" and platform.machine().casefold() in {"x86_64", "amd64"},
    "POSIX installer supports Linux x86_64 only",
)
class ShellInstallerTests(unittest.TestCase):
    def test_remote_latest_downloads_manifest_then_detected_archive_with_one_latest_resolution(self) -> None:
        # Given: a fake authenticated GitHub CLI serving the latest release assets
        with self._fixture() as fixture:
            remote_assets = fixture.assets.parent / "remote-assets"
            remote_assets.mkdir()
            self._write_asset(fixture, "1.2.3", "latest", assets=remote_assets)
            fake_bin = self._write_fake_commands(fixture)

            # When: remote installation uses the default latest selection
            _ = self._run_remote(fixture, remote_assets, fake_bin)

            # Then: GitHub resolves latest for the manifest once, then downloads only its archive
            self.assertEqual(self._current_value(fixture), "latest")
            commands = (fixture.assets.parent / "gh.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(commands[0], "auth status --hostname github.com")
            self.assertTrue(commands[1].startswith("release download --repo Edulynch/OpenCode-EvoSpec"))
            self.assertIn("--pattern SHA256SUMS", commands[1])
            self.assertTrue(commands[2].startswith("release download v1.2.3 --repo Edulynch/OpenCode-EvoSpec"))
            self.assertIn(f"--pattern evospec-1.2.3-{self._platform()}.zip", commands[2])
            self.assertEqual(len(commands), 3)

    def test_anonymous_public_download_resolves_manifest_then_pins_archive(self) -> None:
        for requested_tag, version in (("latest", "1.2.3"), ("1.2.4", "1.2.4")):
            with self.subTest(tag=requested_tag), self._fixture() as fixture:
                remote_assets = fixture.assets.parent / "remote-assets"
                remote_assets.mkdir()
                self._write_asset(fixture, version, "public", assets=remote_assets)
                fake_bin = self._write_fake_commands(fixture)
                arguments = () if requested_tag == "latest" else ("--tag", requested_tag)

                self._run_remote(fixture, remote_assets, fake_bin, *arguments, failure="auth")

                manifest_url = (
                    "https://github.com/Edulynch/OpenCode-EvoSpec/releases/latest/download/SHA256SUMS"
                    if requested_tag == "latest"
                    else f"https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v{requested_tag}/SHA256SUMS"
                )
                archive_url = f"https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v{version}/evospec-{version}-{self._platform()}.zip"
                curl_calls = (fixture.assets.parent / "curl.log").read_text(encoding="utf-8").splitlines()
                self.assertEqual(curl_calls, [manifest_url, archive_url])
                self.assertEqual((fixture.assets.parent / "gh.log").read_text(encoding="utf-8").splitlines(), ["auth status --hostname github.com"])
                self.assertEqual(self._current_value(fixture), "public")

    def test_anonymous_public_download_rejects_unsafe_release_tag_before_network(self) -> None:
        with self._fixture() as fixture:
            fake_bin = self._write_fake_commands(fixture)
            completed = self._run_remote(
                fixture, fixture.assets, fake_bin, "--tag", "1.2.3/../../outside", failure="auth", check=False
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("invalid release tag", completed.stderr)
            self.assertFalse(fixture.install_root.exists())
            self.assertFalse((fixture.assets.parent / "curl.log").exists())
    def test_remote_explicit_unprefixed_tag_uses_prefixed_release_tag(self) -> None:
        # Given: a fake authenticated GitHub CLI serving an explicitly requested version
        with self._fixture() as fixture:
            remote_assets = fixture.assets.parent / "remote-assets"
            remote_assets.mkdir()
            self._write_asset(fixture, "1.2.4", "explicit", assets=remote_assets)
            fake_bin = self._write_fake_commands(fixture)

            # When: remote installation receives an unprefixed archive version
            _ = self._run_remote(fixture, remote_assets, fake_bin, "--tag", "1.2.4")

            # Then: both release downloads use its GitHub v-tag and the manifest name remains unprefixed
            commands = (fixture.assets.parent / "gh.log").read_text(encoding="utf-8").splitlines()
            self.assertTrue(commands[1].startswith("release download v1.2.4 --repo Edulynch/OpenCode-EvoSpec"))
            self.assertTrue(commands[2].startswith("release download v1.2.4 --repo Edulynch/OpenCode-EvoSpec"))
            self.assertIn(f"--pattern evospec-1.2.4-{self._platform()}.zip", commands[2])

    def test_local_assets_and_uninstall_do_not_invoke_github_cli(self) -> None:
        # Given: local assets and a fake GitHub CLI that records every invocation
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3", "local")
            fake_bin = self._write_fake_commands(fixture)
            environment = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}

            # When: a local install is removed
            _ = self._run(fixture, environment=environment)
            _ = self._run(fixture, "--uninstall", environment=environment)

            # Then: neither local branch requires GitHub CLI access
            self.assertFalse((fixture.assets.parent / "gh.log").exists())

    def test_remote_authentication_and_download_failures_do_not_publish_installation(self) -> None:
        # Given: fake GitHub CLI authentication and download failures
        for failure in ("auth", "download"):
            with self.subTest(failure=failure), self._fixture() as fixture:
                fake_bin = self._write_fake_commands(fixture)

                # When: remote acquisition fails before a candidate can be staged
                completed = self._run_remote(fixture, fixture.assets, fake_bin, failure=failure, check=False)

                # Then: no installation is published, and authentication explains its recovery action
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(fixture.current.exists())
                self.assertFalse(fixture.marker.exists())
                self.assertFalse(fixture.profile.exists())
                if failure == "auth":
                    self.assertIn("Public GitHub release download failed; the repository and release must be public, or authenticate with gh.", completed.stderr)
                    self.assertFalse(fixture.install_root.exists())

    def test_unmarked_uninstall_preserves_matching_path_and_missing_profile(self) -> None:
        for profile_exists in (False, True):
            with self.subTest(profile_exists=profile_exists), self._fixture() as fixture:
                fixture.install_root.mkdir()
                fixture.current.mkdir()
                (fixture.current / "keep").write_text("unowned", encoding="utf-8")
                legacy = fixture.install_root / "versions" / "legacy"
                legacy.mkdir(parents=True)
                (legacy / "keep").write_text("unowned", encoding="utf-8")
                before_profile = None
                if profile_exists:
                    path_line = f'export PATH="{fixture.current}:$PATH" # evospec-installer\n'
                    fixture.profile.write_text("export OTHER=value\n" + path_line, encoding="utf-8")
                    before_profile = fixture.profile.read_bytes()

                self._run(fixture, "--uninstall")

                self.assertTrue((fixture.current / "keep").is_file())
                self.assertTrue((legacy / "keep").is_file())
                self.assertFalse(fixture.marker.exists())
                self.assertEqual(fixture.profile.exists(), profile_exists)
                if before_profile is not None:
                    self.assertEqual(fixture.profile.read_bytes(), before_profile)
                self.assertEqual(tuple(fixture.install_root.glob(".uninstall-*")), ())
    def test_install_reinstall_and_uninstall_preserve_owned_profile_entry(self) -> None:
        # Given: a local native fixture and unrelated profile content
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3", "installed")
            fixture.profile.write_text("export OTHER=value\n", encoding="utf-8")

            # When: the fixture is installed twice then removed
            self._run(fixture)
            self._run(fixture)
            self._run(fixture, "--uninstall")

            # Then: only the owned active path is removed
            self.assertFalse(fixture.current.exists())
            self.assertEqual(fixture.profile.read_text(encoding="utf-8"), "export OTHER=value\n")

    def test_failures_restore_previous_upgrade_and_same_version_installations(self) -> None:
        # Given: a runnable owned installation and candidates for an upgrade and reinstall
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3", "old")
            self._run(fixture, "--tag", "1.2.3")
            before_profile = b"\xef\xbb\xbfexport OTHER=value\r\nexport SPACED='a  b'\n"
            fixture.profile.write_bytes(before_profile)
            before_marker = fixture.marker.read_bytes()
            self._write_asset(fixture, "1.2.4", "new")

            # When: each fallible transaction state is injected
            for point in ("pre-swap", "promotion", "final-smoke", "ownership"):
                completed = self._run(fixture, "--tag", "1.2.4", failure=point, check=False)

                # Then: the original current path and ownership state remain usable
                self.assertNotEqual(completed.returncode, 0, point)
                self.assertEqual(self._current_value(fixture), "old")
                self.assertEqual(fixture.profile.read_bytes(), before_profile)
                self.assertEqual(fixture.marker.read_bytes(), before_marker)

            self._write_asset(fixture, "1.2.3", "replacement")
            completed = self._run(fixture, "--tag", "1.2.3", failure="final-smoke", check=False)

            # Then: a failed same-version reinstall keeps the original target runnable
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(self._current_value(fixture), "old")
            self.assertEqual(fixture.profile.read_bytes(), before_profile)
            self.assertEqual(fixture.marker.read_bytes(), before_marker)

    def test_archive_rejects_unsafe_and_incomplete_members_before_installation(self) -> None:
        # Given: malicious local archives with valid checksums
        invalid_members = (
            "../escape",
            "evospec\\..\\escape",
            "/absolute",
            "outside/file",
            "evospec/unexpected",
            "evospec/evospec",
            "evospec/rogue",
        )
        for member in invalid_members:
            with self.subTest(member=member), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3", "candidate", extra=(member,))

                # When: the installer reads the malformed fixture
                completed = self._run(fixture, "--tag", "1.2.3", check=False)

                # Then: no owned installation or profile mutation exists
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(fixture.current.exists())
                self.assertFalse(fixture.marker.exists())
                self.assertFalse(fixture.profile.exists())

    def _fixture(self) -> "InstallerFixture":
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        fixture = InstallerFixture(temporary, root / "assets", root / "install", root / "profile")
        fixture.assets.mkdir()
        return fixture

    def _write_asset(self, fixture: "InstallerFixture", version: str, value: str, extra: tuple[str, ...] = (), assets: Path | None = None) -> None:
        asset_directory = fixture.assets if assets is None else assets
        archive = asset_directory / f"evospec-{version}-{self._platform()}.zip"
        with zipfile.ZipFile(archive, "w") as zip_file:
            program = f"#!/bin/sh\nprintf '%s' '{value}'\n"
            for name in ("evospec", "evospec-agent", "evospec-agent-protocol"):
                zip_file.writestr(f"evospec/{name}", program)
            zip_file.writestr("evospec/_internal/runtime", "private")
            for member in extra:
                zip_file.writestr(member, "unsafe")
        manifest = asset_directory / "SHA256SUMS"
        manifest.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")

    def _write_fake_commands(self, fixture: "InstallerFixture") -> Path:
        fake_bin = fixture.assets.parent / "fake-bin"
        fake_bin.mkdir()
        (fake_bin / "gh").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$EVOSPEC_GH_LOG\"\ncase \"$1 $2\" in\n"
            "  'auth status') if [ \"${EVOSPEC_FAKE_GH_FAIL:-}\" = auth ]; then exit 1; fi ;;\n"
            "  'release download')\n    [ \"${EVOSPEC_FAKE_GH_FAIL:-}\" = download ] && exit 1\n    shift 2\n    [ \"$1\" = --repo ] || shift\n    while [ \"$#\" -gt 0 ]; do\n      case \"$1\" in\n        --pattern) pattern=$2; shift 2 ;;\n        --dir) directory=$2; shift 2 ;;\n        *) shift ;;\n      esac\n    done\n    cp \"$EVOSPEC_FAKE_GH_ASSETS/$pattern\" \"$directory/$pattern\"\n    ;;\n"
            "  *) exit 2 ;;\nesac\n",
            encoding="utf-8",
        )
        (fake_bin / "curl").write_text(
            "#!/bin/sh\nurl=\noutput=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --output|-o) output=$2; shift 2 ;;\n"
            "    *) url=$1; shift ;;\n"
            "  esac\n"
            "done\n"
            "printf '%s\\n' \"$url\" >> \"$EVOSPEC_CURL_LOG\"\n"
            "[ \"${EVOSPEC_FAKE_CURL_FAIL:-}\" != 1 ] || exit 22\n"
            "[ -n \"$output\" ] && [ -n \"$url\" ] || exit 2\n"
            "cp \"$EVOSPEC_CURL_ASSETS/${url##*/}\" \"$output\"\n",
            encoding="utf-8",
        )
        for command in (fake_bin / "gh", fake_bin / "curl"):
            command.chmod(0o755)
        return fake_bin

    def _run(self, fixture: "InstallerFixture", *arguments: str, failure: str | None = None, check: bool = True, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command_environment = os.environ | ({"EVOSPEC_INSTALL_TEST_FAIL_AT": failure} if failure else {}) | (environment or {})
        completed = subprocess.run(
            (
                "sh", str(ROOT / "scripts" / "install.sh"), "--asset-directory", str(fixture.assets),
                "--manifest-path", str(fixture.assets / "SHA256SUMS"), "--install-root", str(fixture.install_root),
                "--profile-file", str(fixture.profile), *arguments,
            ),
            check=False,
            text=True,
            capture_output=True,
            env=command_environment,
        )
        if check:
            self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def _run_remote(self, fixture: "InstallerFixture", remote_assets: Path, fake_bin: Path, *arguments: str, failure: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ("sh", str(ROOT / "scripts" / "install.sh"), "--install-root", str(fixture.install_root), "--profile-file", str(fixture.profile), *arguments),
            check=False,
            text=True,
            capture_output=True,
            env=os.environ | {
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "EVOSPEC_GH_LOG": str(fixture.assets.parent / "gh.log"),
                "EVOSPEC_FAKE_GH_ASSETS": str(remote_assets),
                "EVOSPEC_CURL_LOG": str(fixture.assets.parent / "curl.log"),
                "EVOSPEC_CURL_ASSETS": str(remote_assets),
                "EVOSPEC_FAKE_CURL_FAIL": "1" if failure == "public-download" else "",
            } | ({"EVOSPEC_FAKE_GH_FAIL": failure} if failure else {}),
        )
        if check:
            self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    @staticmethod
    def _current_value(fixture: "InstallerFixture") -> str:
        return subprocess.run((str(fixture.current / "evospec"),), check=True, text=True, capture_output=True).stdout

    @staticmethod
    def _platform() -> str:
        machine = platform.machine().casefold()
        match platform.system(), machine:
            case "Linux", "x86_64" | "amd64":
                return "linux-x86_64"
            case _:
                raise unittest.SkipTest("unsupported Linux installer host")


class InstallerFixture:
    def __init__(self, temporary: TemporaryDirectory[str], assets: Path, install_root: Path, profile: Path) -> None:
        self._temporary = temporary
        self.assets = assets
        self.install_root = install_root
        self.profile = profile

    @property
    def current(self) -> Path:
        return self.install_root / "current"

    @property
    def marker(self) -> Path:
        return self.install_root / ".evospec-install"

    def __enter__(self) -> "InstallerFixture":
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self._temporary.cleanup()
