from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from tests._tempdir import TemporaryDirectory

ROOT = Path(__file__).parents[1]
INSTALL_GUIDES = ("README.md", "docs/standalone-cli.md", "docs/opencode.md")
WINDOWS_GUIDES = INSTALL_GUIDES
INSTALLER_BOOTSTRAP_COMMAND = 'gh release download --repo Edulynch/OpenCode-EvoSpec --pattern install.ps1 --dir $env:TEMP --clobber; if ($LASTEXITCODE -ne 0) { throw "GitHub release download failed" }; & "$env:TEMP\\install.ps1" -Bootstrap'


def documented_windows_command(guide: str) -> str:
    """Return the mocked bootstrap used by Windows install-flow tests."""
    if guide not in WINDOWS_GUIDES:
        raise ValueError(f"unsupported install guide: {guide}")
    return INSTALLER_BOOTSTRAP_COMMAND


class SourceInstallDocumentationTests(unittest.TestCase):
    def test_private_source_install_is_documented_consistently(self) -> None:
        clone_command = "git clone https://github.com/Edulynch/OpenCode-EvoSpec.git"
        for guide in INSTALL_GUIDES:
            with self.subTest(guide=guide):
                content = (ROOT / guide).read_text(encoding="utf-8")
                self.assertIn(clone_command, content)
                self.assertIn("python -m pip install .", content)
                self.assertIn("Python 3.11", content)
                self.assertNotIn("gh release download", content)


@unittest.skipUnless(os.name == "nt", "PowerShell installer requires Windows")
class PowerShellInstallerTests(unittest.TestCase):
    def test_bootstrap_executes_complete_file_in_invoking_process(self) -> None:
        # Given: the installer bootstrap command and a multiline downloaded script
        commands = [INSTALLER_BOOTSTRAP_COMMAND for _ in INSTALL_GUIDES]
        self.assertEqual(commands, [commands[0]] * len(commands))
        self.assertEqual(len(commands[0].splitlines()), 1)
        self.assertIn('& "$env:TEMP\\install.ps1" -Bootstrap', commands[0])
        self.assertNotIn("Remove-Item", commands[0])
        for guide, command in zip(INSTALL_GUIDES, commands):
            with self.subTest(guide=guide), self._fixture() as fixture:
                _ = (fixture.assets / "install.ps1").write_text(
                    "[CmdletBinding()]\nparam([switch]$Bootstrap)\n"
                    "if (-not $Bootstrap) { throw 'bootstrap switch missing' }\n"
                    "function Get-Message {\n    @'\nfirst line\nsecond line\n'@\n}\n"
                    "[Console]::Error.WriteLine('visible diagnostic')\n"
                    "Write-Output $PID\nWrite-Output $PSCommandPath\nGet-Message\n"
                    "Remove-Item -LiteralPath $PSCommandPath -Force\n",
                    encoding="utf-8",
                )
                unrelated_download = fixture.downloads / "unrelated.ps1"
                _ = unrelated_download.write_text("preserve me", encoding="utf-8")
                _ = (fixture.downloads / "install.ps1").write_text("replace me", encoding="utf-8")
                # When: the bootstrap command is run in PowerShell
                completed = fixture.run_powershell("Write-Output $PID\n" + command)
                # Then: a complete file runs in the caller with visible output and no source echo
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotRegex(completed.stdout, r"(?m)^(PS[^\r\n]*>|>>)")
                lines = completed.stdout.splitlines()
                self.assertEqual(len(lines), 5, completed.stdout)
                self.assertEqual(lines[1], lines[0])
                self.assertEqual(lines[3:], ["first line", "second line"])
                self.assertEqual(Path(lines[2]).suffix, ".ps1")
                self.assertFalse(Path(lines[2]).exists())
                self.assertIn("visible diagnostic", completed.stderr.splitlines())
                self.assertEqual(tuple(fixture.downloads.iterdir()), (unrelated_download,))

    def test_successful_bootstrap_removes_only_its_downloaded_source(self) -> None:
        # Given: the real installer, release assets, and an unrelated temporary PowerShell file
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            unrelated_download = fixture.downloads / "unrelated.ps1"
            _ = unrelated_download.write_text("preserve me", encoding="utf-8")
            # When: the documented bootstrap performs a normal installation
            completed = fixture.run_powershell(INSTALLER_BOOTSTRAP_COMMAND)
            # Then: only the downloaded invocation source is removed and normal installation remains available
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse((fixture.downloads / "install.ps1").exists())
            self.assertTrue(unrelated_download.is_file())
            self.assertTrue((fixture.current / "evospec.exe").is_file())

    def test_documented_bootstrap_failure_is_visible_and_catchable(self) -> None:
        # Given: failed acquisition, a terminating script error, or an explicit failed exit
        for mode, source, diagnostic in (
            ("bootstrap", "Write-Output 'must not execute'", "fake download failure"),
            ("", "throw 'installer failure'", "installer failure"),
            ("", "[Console]::Error.WriteLine('native failure'); & cmd /c exit 17", "native failure"),
        ):
            with self.subTest(mode=mode, source=source), self._fixture() as fixture:
                _ = (fixture.assets / "install.ps1").write_text(
                    "param([switch]$Bootstrap)\n"
                    "try {\n"
                    + source
                    + "\n} finally {\n"
                    "if ($Bootstrap) { Remove-Item -LiteralPath $PSCommandPath -Force }\n"
                    "}\n"
                    "if ($LASTEXITCODE) { throw 'native failure' }\n",
                    encoding="utf-8",
                )
                fixture.gh_failure = mode
                command = INSTALLER_BOOTSTRAP_COMMAND
                # When: the command runs with the interactive default error preference
                completed = fixture.run_powershell("$ErrorActionPreference = 'Continue'\n" + command)
                # Then: automation fails and callers can catch errors without losing the session
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(diagnostic, completed.stderr)
                self.assertNotIn("must not execute", completed.stdout)
                self.assertEqual(tuple(fixture.downloads.iterdir()), ())
                caught = fixture.run_powershell(
                    "$ErrorActionPreference = 'Continue'\ntry { " + command
                    + " } catch { Write-Output 'caught' }; Write-Output 'session alive'\n"
                    "if ($ErrorActionPreference -ne 'Continue') { throw 'caller preference changed' }"
                )
                self.assertEqual(caught.returncode, 0, caught.stderr)
                self.assertEqual(caught.stdout.splitlines(), ["caught", "session alive"], caught.stderr)
                self.assertEqual(tuple(fixture.downloads.iterdir()), ())

    def test_bootstrap_cleanup_failure_preserves_primary_or_terminates_alone(self) -> None:
        # Given: the real downloaded installer and a deletion seam that fails only for PowerShell files
        for primary in (True, False):
            with self.subTest(primary=primary), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                failure = "promotion" if primary else ""
                # When: bootstrap cleanup fails after an installer failure or a successful installation
                completed = fixture.run_powershell("\n".join((
                    f"$env:EVOSPEC_INSTALL_TEST_FAIL_AT = '{failure}'",
                    "$global:bootstrapCleanup = [IO.IOException]::new('distinct bootstrap cleanup')",
                    "function Remove-Item {",
                    "[CmdletBinding()] param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)",
                    "if ((Split-Path -Leaf $LiteralPath) -like '*.ps1') {",
                    "Write-Output ('bootstrap cleanup attempt: ' + $LiteralPath)",
                    "throw $global:bootstrapCleanup",
                    "}",
                    "Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters",
                    "}",
                    "try { " + INSTALLER_BOOTSTRAP_COMMAND + " } catch {",
                    "Write-Output ('caught: ' + $_.Exception.Message); throw",
                    "}",
                )))
                # Then: only the invoked download is targeted and an installer failure wins over deletion failure
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                attempts = [line.removeprefix("bootstrap cleanup attempt: ") for line in completed.stdout.splitlines() if line.startswith("bootstrap cleanup attempt: ")]
                self.assertEqual(len(attempts), 1)
                self.assertTrue(os.path.samefile(attempts[0], fixture.downloads / "install.ps1"))
                expected = "injected installer failure: promotion" if primary else "distinct bootstrap cleanup"
                self.assertIn(f"caught: {expected}", completed.stdout.splitlines())
                self.assertTrue(any(
                    line.endswith(": distinct bootstrap cleanup") and line.startswith("cleanup failed for ")
                    for line in completed.stderr.splitlines()
                ), completed.stderr)
                if not primary:
                    self.assertTrue((fixture.current / "evospec.exe").is_file())

    def test_installer_cleanup_attempts_are_independent_and_preserve_failure_identity(self) -> None:
        # Given: deterministic failures at filesystem boundaries, not in production cleanup hooks
        for primary, retain, failures in (
            (True, False, "stage,download"),
            (True, False, "transaction"),
            (False, False, "stage,transaction,download"),
            (True, True, "stage,download"),
        ):
            with self.subTest(primary=primary, retain=retain, failures=failures), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                # When: a reinstall fails at marker write or only during final cleanup
                completed = fixture.run_powershell("\n".join((
                    "& $env:EVOSPEC_TEST_INSTALL_SCRIPT",
                    "$global:primaryError = [InvalidOperationException]::new('distinct installer primary')",
                    f"$global:failPrimary = ${str(primary).lower()}",
                    f"$env:EVOSPEC_INSTALL_TEST_FAIL_AT = '{'rollback-current' if retain else ''}'",
                    "$global:cleanupErrors = @{}",
                    f"'{failures}'.Split(',') | ForEach-Object {{ $global:cleanupErrors[$_] = [IO.IOException]::new('distinct cleanup ' + $_) }}",
                    "function Set-Content {",
                    "[CmdletBinding()] param([string]$LiteralPath, [string]$Value, [switch]$NoNewline)",
                    "if ($global:failPrimary -and (Split-Path -Leaf $LiteralPath) -eq '.evospec-install') { throw $global:primaryError }",
                    "Microsoft.PowerShell.Management\\Set-Content @PSBoundParameters",
                    "}",
                    "function Remove-Item {",
                    "[CmdletBinding()] param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)",
                    "if ((Split-Path -Leaf $LiteralPath) -match '^(?:\\.|evospec-)(stage|transaction|download)-') {",
                    "$kind = $Matches[1]; Write-Output ('attempt: ' + $kind)",
                    "if ($global:cleanupErrors.ContainsKey($kind)) { throw $global:cleanupErrors[$kind] }",
                    "}",
                    "Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters",
                    "}",
                    "$expected = if ($global:failPrimary) { $global:primaryError } else { $global:cleanupErrors['stage'] }",
                    "try { & $env:EVOSPEC_TEST_INSTALL_SCRIPT } catch {",
                    "Write-Output ('identity: ' + [object]::ReferenceEquals($_.Exception, $expected))",
                    "Write-Output ('caught: ' + $_.Exception.Message); throw",
                    "}",
                )))
                # Then: every eligible cleanup runs and the first relevant exception terminates
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn("identity: True", completed.stdout.splitlines(), completed.stderr)
                expected_message = "distinct installer primary" if primary else "distinct cleanup stage"
                self.assertIn(f"caught: {expected_message}", completed.stdout.splitlines())
                attempts = [line for line in completed.stdout.splitlines() if line.startswith("attempt: ")]
                eligible = ("stage", "download") if retain else ("stage", "transaction", "download")
                self.assertEqual(attempts, [f"attempt: {kind}" for kind in eligible])
                for kind in failures.split(","):
                    self.assertTrue(any(
                        line.startswith("cleanup failed for ") and line.endswith(f": distinct cleanup {kind}")
                        for line in completed.stderr.splitlines()
                    ), completed.stderr)
                for kind in eligible:
                    cleanup_root = fixture.downloads if kind == "download" else fixture.install_root
                    cleanup_pattern = "evospec-download-*" if kind == "download" else f".{kind}-*"
                    self.assertEqual(bool(tuple(cleanup_root.glob(cleanup_pattern))), kind in failures.split(","))
                if retain:
                    transaction, = fixture.install_root.glob(".transaction-*")
                    self.assertTrue((transaction / "current" / "evospec.exe").is_file())
                    prefix = "rollback failed; recovery data retained at "
                    reported, = [
                        line.removeprefix(prefix) for line in completed.stderr.splitlines()
                        if line.startswith(prefix)
                    ]
                    self.assertTrue(os.path.samefile(reported, transaction))
                self.assertEqual(completed.stdout.splitlines(), [
                    "EvoSpec 1.2.3 installed successfully.",
                    *[f"attempt: {kind}" for kind in eligible],
                    "identity: True",
                    f"caught: {expected_message}",
                ])

    def test_success_confirmation_uses_archive_version_once_per_install(self) -> None:
        # Given: local or remote archives resolved through latest or an explicit tag
        for remote in (False, True):
            for tag in ("latest", "1.2.3"):
                with self.subTest(remote=remote, tag=tag), self._fixture() as fixture:
                    self._write_asset(fixture, "1.2.3")
                    for operation, arguments, expected in (
                        ("install", ("-Tag", tag), "EvoSpec 1.2.3 installed successfully.\n"),
                        ("reinstall", ("-Tag", tag), "EvoSpec 1.2.3 installed successfully.\n"),
                        ("uninstall", ("-Uninstall",), ""),
                    ):
                        with self.subTest(operation=operation):
                            # When: the real installer performs one lifecycle operation
                            completed = self._run(fixture, *arguments, remote=remote, check=False)
                            # Then: only successful installs emit exactly one resolved-version line
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                            self.assertEqual(completed.stderr, "")
                            self.assertEqual(completed.stdout, expected)
                    self.assertEqual(tuple(fixture.downloads.glob("evospec-download-*")), ())
                    for pattern in (".stage-*", ".transaction-*"):
                        self.assertEqual(tuple(fixture.install_root.glob(pattern)), ())

    def test_invalid_marker_types_do_not_authorize_install_or_uninstall(self) -> None:
        for marker_type in ("directory", "wrong-content"):
            with self.subTest(marker_type=marker_type), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                fixture.install_root.mkdir()
                fixture.current.mkdir()
                (fixture.current / "keep.txt").write_text("unowned current", encoding="utf-8")
                legacy = fixture.install_root / "versions" / "legacy"
                legacy.mkdir(parents=True)
                (legacy / "keep.txt").write_text("unowned version", encoding="utf-8")
                if marker_type == "directory":
                    fixture.marker.mkdir()
                else:
                    fixture.marker.write_text("unowned marker", encoding="utf-8")
                fixture.path_file.write_text(f"{fixture.current};C:\\unrelated", encoding="utf-8")
                before_path_file = fixture.path_file.read_bytes()

                install = self._run(fixture, check=False)
                self.assertNotEqual(install.returncode, 0)
                self.assertIn("refusing to replace an installation not owned by EvoSpec", install.stderr)
                self.assertTrue((fixture.current / "keep.txt").is_file())
                self.assertEqual((fixture.current / "keep.txt").read_text(encoding="utf-8"), "unowned current")
                self.assertEqual(legacy.joinpath("keep.txt").read_text(encoding="utf-8"), "unowned version")
                self.assertEqual(fixture.path_file.read_bytes(), before_path_file)
                if marker_type == "directory":
                    self.assertTrue(fixture.marker.is_dir())
                else:
                    self.assertEqual(fixture.marker.read_text(encoding="utf-8"), "unowned marker")

                uninstall = fixture.run_powershell("& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall")
                self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
                self.assertEqual((fixture.current / "keep.txt").read_text(encoding="utf-8"), "unowned current")
                self.assertEqual(legacy.joinpath("keep.txt").read_text(encoding="utf-8"), "unowned version")
                self.assertEqual(fixture.path_file.read_bytes(), before_path_file)
                if marker_type == "directory":
                    self.assertTrue(fixture.marker.is_dir())
                else:
                    self.assertEqual(fixture.marker.read_text(encoding="utf-8"), "unowned marker")

    def test_reparse_marker_does_not_authorize_install_or_uninstall(self) -> None:
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            fixture.install_root.mkdir()
            fixture.current.mkdir()
            (fixture.current / "keep.txt").write_text("unowned current", encoding="utf-8")
            legacy = fixture.install_root / "versions" / "legacy"
            legacy.mkdir(parents=True)
            (legacy / "keep.txt").write_text("unowned version", encoding="utf-8")
            marker_target = fixture.assets / "marker-target"
            marker_target.write_text("EvoSpec installer owns this directory", encoding="utf-8")
            try:
                fixture.marker.symlink_to(marker_target)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks unavailable without extra privileges")
            fixture.path_file.write_text(f"{fixture.current};C:\\unrelated", encoding="utf-8")
            before_path_file = fixture.path_file.read_bytes()

            install = self._run(fixture, check=False)
            self.assertNotEqual(install.returncode, 0)
            self.assertIn("refusing to replace an installation not owned by EvoSpec", install.stderr)
            self.assertTrue(fixture.marker.is_symlink())
            self.assertEqual(marker_target.read_text(encoding="utf-8"), "EvoSpec installer owns this directory")
            self.assertEqual(fixture.path_file.read_bytes(), before_path_file)

            uninstall = fixture.run_powershell("& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall")
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertTrue(fixture.marker.is_symlink())
            self.assertEqual(marker_target.read_text(encoding="utf-8"), "EvoSpec installer owns this directory")
            self.assertEqual((fixture.current / "keep.txt").read_text(encoding="utf-8"), "unowned current")
            self.assertEqual(legacy.joinpath("keep.txt").read_text(encoding="utf-8"), "unowned version")
            self.assertEqual(fixture.path_file.read_bytes(), before_path_file)
    def test_unmarked_uninstall_preserves_existing_paths_and_missing_path_file(self) -> None:
        for path_file_exists in (False, True):
            with self.subTest(path_file_exists=path_file_exists), self._fixture() as fixture:
                fixture.install_root.mkdir()
                fixture.current.mkdir()
                (fixture.current / "keep.txt").write_text("unowned", encoding="utf-8")
                legacy = fixture.install_root / "versions" / "legacy"
                legacy.mkdir(parents=True)
                (legacy / "keep.txt").write_text("unowned", encoding="utf-8")
                before_path_file = None
                if path_file_exists:
                    fixture.path_file.write_text(f"{fixture.current};C:\\unrelated", encoding="utf-8")
                    before_path_file = fixture.path_file.read_bytes()

                completed = fixture.run_powershell(
                    "$before = $env:PATH\n"
                    "& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall\n"
                    "if ($env:PATH -cne $before) { throw 'unmarked uninstall changed process PATH' }"
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue((fixture.current / "keep.txt").is_file())
                self.assertTrue((legacy / "keep.txt").is_file())
                self.assertFalse(fixture.marker.exists())
                self.assertEqual(fixture.path_file.exists(), path_file_exists)
                if before_path_file is not None:
                    self.assertEqual(fixture.path_file.read_bytes(), before_path_file)
    def test_same_process_install_reinstall_and_uninstall_preserve_unrelated_path(self) -> None:
        # Given: a real installer with an equivalent owned path and unrelated empty entries
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            command = INSTALLER_BOOTSTRAP_COMMAND
            python = sys.executable.replace("'", "''")
            identity_check = (
                "import os, sys; "
                "assert os.path.samefile(sys.argv[1], sys.argv[2]), 'wrong first PATH entry'; "
                "assert os.path.samefile(sys.argv[3], os.path.join(sys.argv[2], 'evospec.exe')), 'stale version won'"
            ).replace("'", "''")
            # When: installation, reinstallation, and removal share one process
            completed = fixture.run_powershell(
                "$original = 'C:\\stale;;C:\\other;' + $env:PATH + ';'\n"
                "$owned = Join-Path $env:EVOSPEC_TEST_INSTALL_ROOT 'current'\n"
                "$env:PATH = $original + ';' + $owned.ToUpper() + '\\'\n"
                + command + "\n"
                "$installed = $env:PATH\n"
                "$first, $suffix = $installed -split ';', 2\n"
                "if ($suffix -cne $original) { throw 'process PATH suffix changed' }\n"
                f"& '{python}' -I -c '{identity_check}' $first $owned (Get-Command evospec).Source\n"
                "if ($LASTEXITCODE -ne 0) { throw 'installed path identity check failed' }\n"
                + command + "\n"
                "if ($env:PATH -cne $installed) { throw 'reinstall changed PATH' }\n"
                "& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall\n"
                "if ($env:PATH -cne $original) { throw 'uninstall changed unrelated PATH' }\n"
                "Write-Output 'same-process lifecycle passed'"
            )
            # Then: owned precedence and unrelated PATH bytes survive the lifecycle
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(completed.stdout, (
                "EvoSpec 1.2.3 installed successfully.\n"
                "EvoSpec 1.2.3 installed successfully.\n"
                "same-process lifecycle passed\n"
            ))
            self.assertEqual(tuple(fixture.downloads.iterdir()), ())

    def test_transaction_failure_restores_exact_process_path(self) -> None:
        # Given: distinct process PATH snapshots and failures across the transaction
        for point in ("promotion", "after-marker", "after-path", "after-path,rollback-current"):
            with self.subTest(point=point), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                # When: installation fails, including during recovery
                completed = fixture.run_powershell(
                    "& $env:EVOSPEC_TEST_INSTALL_SCRIPT\n"
                    "$env:PATH = 'C:\\one;;' + $env:PATH + ';C:\\two;'\n"
                    "$before = $env:PATH\n"
                    f"$env:EVOSPEC_INSTALL_TEST_FAIL_AT = '{point}'\n"
                    "try { & $env:EVOSPEC_TEST_INSTALL_SCRIPT; throw 'failure not injected' }\n"
                    "catch { if ($_ -notmatch 'injected installer failure') { throw } }\n"
                    "if ($env:PATH -cne $before) { throw 'process PATH snapshot was not restored' }\n"
                    "Write-Output 'rollback passed'"
                )
                # Then: even a rollback error cannot prevent exact process PATH restoration
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertEqual(completed.stdout, (
                    "EvoSpec 1.2.3 installed successfully.\n"
                    "rollback passed\n"
                ))

    def test_remote_failure_allows_retry_in_same_process(self) -> None:
        # Given: recoverable failures throughout acquisition, validation, and extraction
        for failure in ("download", "manifest-download", "archive-download", "manifest", "checksum", "missing", "unsafe", "extraction"):
            with self.subTest(failure=failure), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                archive = fixture.assets / "evospec-1.2.3-windows-x86_64.zip"
                manifest = fixture.assets / "SHA256SUMS"
                _ = (fixture.assets / "valid.zip").write_bytes(archive.read_bytes())
                _ = (fixture.assets / "valid-manifest").write_bytes(manifest.read_bytes())
                match failure:
                    case "download" | "manifest-download" | "archive-download":
                        fixture.gh_failure = failure
                    case "manifest":
                        _ = manifest.write_text("invalid manifest\n", encoding="utf-8")
                    case "checksum":
                        _ = manifest.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
                    case "missing":
                        archive.unlink()
                    case "unsafe":
                        self._write_asset(fixture, "1.2.3", extra=("evospec/rogue.exe",))
                    case "extraction":
                        self._write_asset(fixture, "1.2.3", extra=("evospec/_internal/runtime/nested",))
                # When: the failed attempt is followed by a repaired release in the same pwsh
                completed = fixture.run_powershell("\n".join((
                    "$before = $env:PATH; $failed = $false",
                    "try { & $env:EVOSPEC_TEST_INSTALL_SCRIPT } catch { $failed = $true; Write-Output ('first failure: ' + $_.Exception.Message) }",
                    "if (-not $failed) { throw 'first attempt unexpectedly succeeded' }",
                    "if ($env:PATH -cne $before) { throw 'failed attempt changed process PATH' }",
                    "$env:EVOSPEC_FAKE_GH_FAILURE = ''",
                    "Copy-Item -LiteralPath (Join-Path $env:EVOSPEC_FAKE_GH_ROOT 'valid.zip') -Destination (Join-Path $env:EVOSPEC_FAKE_GH_ROOT 'evospec-1.2.3-windows-x86_64.zip') -Force",
                    "Copy-Item -LiteralPath (Join-Path $env:EVOSPEC_FAKE_GH_ROOT 'valid-manifest') -Destination (Join-Path $env:EVOSPEC_FAKE_GH_ROOT 'SHA256SUMS') -Force",
                    "& $env:EVOSPEC_TEST_INSTALL_SCRIPT",
                    "Write-Output 'same-process retry passed'",
                )))
                # Then: fresh acquisition succeeds without retained downloads or staging
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertIn("first failure:", completed.stdout)
                self.assertIn("same-process retry passed", completed.stdout.splitlines())
                self.assertTrue((fixture.current / "evospec.exe").is_file())
                self.assertEqual(tuple(fixture.downloads.glob("evospec-download-*")), ())
                for pattern in (".stage-*", ".transaction-*"):
                    self.assertEqual(tuple(fixture.install_root.glob(pattern)), ())
                lines = completed.stdout.splitlines()
                self.assertTrue(lines[0].startswith("first failure: "), completed.stdout)
                self.assertEqual(lines[1:], [
                    "EvoSpec 1.2.3 installed successfully.",
                    "same-process retry passed",
                ])

    def test_uninstall_path_write_failure_preserves_process_path_and_installation(self) -> None:
        # Given: an installed bundle and a PathFile readable but locked against writes
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            # When: uninstall reaches a rejected persistent PATH mutation
            completed = fixture.run_powershell("\n".join((
                "& $env:EVOSPEC_TEST_INSTALL_SCRIPT",
                "$env:PATH = 'C:\\before;;' + $env:PATH + ';C:\\after;'",
                "$before = $env:PATH; $failed = $false",
                "$pathBefore = [IO.File]::ReadAllText($env:EVOSPEC_TEST_PATH_FILE)",
                "$handle = [IO.File]::Open($env:EVOSPEC_TEST_PATH_FILE, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)",
                "try { & $env:EVOSPEC_TEST_INSTALL_SCRIPT -Uninstall } catch { $failed = $true; Write-Output ('uninstall failure: ' + $_.Exception.Message) } finally { $handle.Dispose() }",
                "if (-not $failed) { throw 'uninstall unexpectedly succeeded' }",
                "if ([IO.File]::ReadAllText($env:EVOSPEC_TEST_PATH_FILE) -cne $pathBefore) { throw 'persistent PATH changed' }",
                "if ($env:PATH -cne $before) { throw 'failed uninstall changed process PATH' }",
                "Write-Output 'uninstall atomicity passed'",
            )))
            # Then: process PATH and the still-owned installation remain unchanged
            self.assertTrue((fixture.current / "evospec.exe").is_file())
            self.assertTrue(fixture.marker.is_file())
            self.assertTrue((fixture.install_root / "versions" / "1.2.3" / "evospec.exe").is_file())
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("uninstall atomicity passed", completed.stdout.splitlines())
            lines = completed.stdout.splitlines()
            self.assertEqual(len(lines), 3, completed.stdout)
            self.assertEqual(lines[0], "EvoSpec 1.2.3 installed successfully.")
            self.assertTrue(lines[1].startswith("uninstall failure: "), completed.stdout)
            self.assertEqual(lines[2], "uninstall atomicity passed")

    def test_fake_gh_rejects_existing_output_without_overwriting(self) -> None:
        # Given: an existing destination with bytes distinct from the release asset
        with self._fixture() as fixture:
            _ = fixture.path_file.write_text("preserve me", encoding="utf-8")
            # When: gh is asked to download without an overwrite option
            completed = fixture.run_powershell(
                "gh release download --pattern install.ps1 --output $env:EVOSPEC_TEST_PATH_FILE"
            )
            # Then: the fake models gh's refusal rather than silently clobbering the file
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already exists", completed.stderr)
            self.assertEqual(fixture.path_file.read_text(encoding="utf-8"), "preserve me")

    def test_remote_latest_resolves_once_and_path_collection_is_isolated(self) -> None:
        # Given: a deterministic authenticated GitHub CLI and every required PATH shape
        for path_kind in ("empty", "one", "multiple", "owned"):
            with self.subTest(path_kind=path_kind), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                path_value = {
                    "empty": "",
                    "one": "C:\\other",
                    "multiple": "C:\\one;C:\\two",
                    "owned": str(fixture.current),
                }[path_kind]
                fixture.path_file.write_text(path_value, encoding="utf-8")

                # When: remote mode downloads only the manifest and selected latest asset
                self._run(fixture, remote=True)

                # Then: unrelated entries survive and the owned entry occurs exactly once
                self.assertEqual(tuple(fixture.install_root.glob(".transaction-*")), ())
                entries = [entry for entry in fixture.path_file.read_text(encoding="utf-8").split(";") if entry]
                owned_entries = [entry for entry in entries if Path(entry).exists() and os.path.samefile(entry, fixture.current)]
                unrelated_entries = [entry for entry in entries if not (Path(entry).exists() and os.path.samefile(entry, fixture.current))]
                self.assertEqual(len(owned_entries), 1)
                self.assertEqual(unrelated_entries, [entry for entry in path_value.split(";") if entry and not (Path(entry).exists() and os.path.samefile(entry, fixture.current))])
                calls = fixture.gh_log.read_text(encoding="utf-8").splitlines()
                self.assertEqual(calls.count("auth status --hostname github.com"), 1)
                self.assertEqual(calls.count("release view --repo Edulynch/OpenCode-EvoSpec --json tagName --jq .tagName"), 1)
                downloads = [call for call in calls if call.startswith("release download")]
                self.assertEqual(len(downloads), 2)
                self.assertTrue(all(call.startswith("release download v1.2.3 --repo Edulynch/OpenCode-EvoSpec") for call in downloads))
                self.assertEqual(["--pattern SHA256SUMS" in downloads[0], "--pattern evospec-1.2.3-windows-x86_64.zip" in downloads[1]], [True, True])
                self._run(fixture, "-Uninstall", remote=True)
                self.assertEqual(
                    [entry for entry in fixture.path_file.read_text(encoding="utf-8").split(";") if entry],
                    unrelated_entries,
                )

    def test_remote_explicit_version_uses_prefixed_release_tag(self) -> None:
        # Given: a local fake GitHub release with an unprefixed archive version
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")

            # When: remote installation names the requested archive version
            self._run(fixture, "-Tag", "1.2.3", remote=True)

            # Then: gh receives the release tag while archive validation retains the version
            calls = fixture.gh_log.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("release view --repo Edulynch/OpenCode-EvoSpec --json tagName --jq .tagName", calls)
            downloads = [call for call in calls if call.startswith("release download")]
            self.assertEqual(len(downloads), 2)
            self.assertTrue(all(call.startswith("release download v1.2.3 ") for call in downloads))

    def test_local_assets_do_not_invoke_github_cli(self) -> None:
        # Given: a complete local asset directory and a fake gh available on PATH
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")

            # When: local acquisition installs the bundle
            self._run(fixture, "-Tag", "1.2.3")

            # Then: the installer has not invoked gh
            self.assertEqual(fixture.gh_log.read_text(encoding="utf-8"), "")

    def test_anonymous_public_download_resolves_manifest_then_pins_archive(self) -> None:
        for requested_tag, version in (("latest", "1.2.3"), ("1.2.4", "1.2.4")):
            with self.subTest(tag=requested_tag), self._fixture() as fixture:
                self._write_asset(fixture, version)
                fixture.gh_failure = "auth"
                tag_argument = "" if requested_tag == "latest" else f"-Tag '{requested_tag}'"

                completed = fixture.run_powershell(
                    "& $env:EVOSPEC_TEST_INSTALL_SCRIPT " + tag_argument
                )

                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                manifest_url = (
                    "https://github.com/Edulynch/OpenCode-EvoSpec/releases/latest/download/SHA256SUMS"
                    if requested_tag == "latest"
                    else f"https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v{requested_tag}/SHA256SUMS"
                )
                archive_url = f"https://github.com/Edulynch/OpenCode-EvoSpec/releases/download/v{version}/evospec-{version}-windows-x86_64.zip"
                self.assertEqual(fixture.public_log.read_text(encoding="utf-8").splitlines(), [manifest_url, archive_url])
                self.assertEqual(fixture.gh_log.read_text(encoding="utf-8").splitlines(), ["auth status --hostname github.com"])
                self.assertTrue((fixture.current / "evospec.exe").is_file())
                self.assertFalse(any(fixture.downloads.glob("evospec-download-*")))

    def test_anonymous_public_download_failure_is_clear_and_safe_for_private_repo(self) -> None:
        with self._fixture() as fixture:
            fixture.gh_failure = "auth"
            fixture.public_failure = True
            completed = fixture.run_powershell("& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Tag latest")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "Public GitHub release download failed; the repository and release must be public, or authenticate with gh.",
                completed.stderr,
            )
            self.assertEqual(
                fixture.public_log.read_text(encoding="utf-8").splitlines(),
                ["https://github.com/Edulynch/OpenCode-EvoSpec/releases/latest/download/SHA256SUMS"],
            )
            self.assertFalse(fixture.install_root.exists())
            self.assertFalse(fixture.path_file.exists())
            self.assertFalse(any(fixture.downloads.glob("evospec-download-*")))

    def test_anonymous_public_download_rejects_unsafe_tag_before_request(self) -> None:
        with self._fixture() as fixture:
            fixture.gh_failure = "auth"
            completed = fixture.run_powershell(
                "& $env:EVOSPEC_TEST_INSTALL_SCRIPT -Tag '1.2.3/../../outside'"
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("invalid release path segment", completed.stderr)
            self.assertEqual(fixture.public_log.read_text(encoding="utf-8"), "")
            self.assertFalse(fixture.install_root.exists())
    def test_remote_auth_and_download_failures_leave_installation_untouched(self) -> None:
        # Given: fake gh failures before and during remote acquisition
        for failure in ("auth", "download"):
            with self.subTest(failure=failure), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3")
                fixture.public_failure = failure == "auth"

                # When: acquisition cannot authenticate or download
                completed = self._run(fixture, remote=True, gh_failure=failure, check=False)

                # Then: no installation state is activated and the failure is actionable
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(fixture.current.exists())
                self.assertFalse(fixture.marker.exists())
                expected = "Public GitHub release download failed; the repository and release must be public, or authenticate with gh." if failure == "auth" else "GitHub release download failed"
                self.assertIn(expected, completed.stderr)
                if failure == "auth":
                    self.assertFalse(fixture.install_root.exists())
                self.assertEqual(completed.stdout, "")

    def test_owned_path_equivalence_preserves_unrelated_entries(self) -> None:
        # Given: case, separator, and canonical existing aliases of an owned current path
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            self._run(fixture)
            alias = fixture.install_root / "current-alias"
            _ = subprocess.run(("cmd", "/c", "mklink", "/J", str(alias), str(fixture.current)), check=True, text=True, capture_output=True)
            unrelated = ("C:\\unrelated-one", "C:\\unrelated-two")
            fixture.path_file.write_text(";".join((unrelated[0], str(alias) + "\\", unrelated[1])), encoding="utf-8")

            # When: install and uninstall update owned paths
            self._run(fixture)

            # Then: install removes the canonical alias before appending only current
            entries = [entry for entry in fixture.path_file.read_text(encoding="utf-8").split(";") if entry]
            self.assertEqual([entry for entry in entries if not (Path(entry).exists() and os.path.samefile(entry, fixture.current))], list(unrelated))
            self.assertEqual(len([entry for entry in entries if Path(entry).exists() and os.path.samefile(entry, fixture.current)]), 1)
            fixture.path_file.write_text(";".join((unrelated[0], str(fixture.current).upper() + "\\", unrelated[1])), encoding="utf-8")
            self._run(fixture, "-Uninstall")
            self.assertEqual(
                [entry for entry in fixture.path_file.read_text(encoding="utf-8").split(";") if entry],
                list(unrelated),
            )

    def test_failures_restore_previous_upgrade_and_same_version_installations(self) -> None:
        # Given: a known-good owned version with isolated PATH state
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            self._run(fixture, "-Tag", "1.2.3")
            before_path = fixture.path_file.read_bytes()
            before_marker = fixture.marker.read_bytes()
            self._write_asset(fixture, "1.2.4")

            # When: each fallible transaction state fails
            for point in ("pre-swap", "promotion", "final-smoke", "ownership"):
                completed = self._run(fixture, "-Tag", "1.2.4", failure=point, check=False)

                # Then: prior activation and ownership state are restored
                self.assertNotEqual(completed.returncode, 0, point)
                self.assertTrue(fixture.current.exists())
                self.assertEqual(fixture.path_file.read_bytes(), before_path)
                self.assertEqual(fixture.marker.read_bytes(), before_marker)
                self.assertEqual(tuple(fixture.install_root.glob(".transaction-*")), ())

                self.assertEqual(completed.stdout, "")

            completed = self._run(fixture, "-Tag", "1.2.3", failure="final-smoke", check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue(fixture.current.exists())
            self.assertEqual(fixture.path_file.read_bytes(), before_path)
            self.assertEqual(fixture.marker.read_bytes(), before_marker)
            self.assertEqual(tuple(fixture.install_root.glob(".transaction-*")), ())
            self.assertEqual(completed.stdout, "")

    def test_post_mutation_failures_restore_exact_marker_and_path_snapshots(self) -> None:
        # Given: an owned installation with byte-distinct marker and PATH snapshots
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            self._run(fixture, "-Tag", "1.2.3")
            fixture.marker.write_bytes(b"owned marker\r\n")
            fixture.path_file.write_bytes(b"C:\\before-one;C:\\before-two\r\n")
            before_marker = fixture.marker.read_bytes()
            before_path = fixture.path_file.read_bytes()
            self._write_asset(fixture, "1.2.4")

            # When: failure is injected immediately after each persistent mutation
            for point in ("after-marker", "after-path"):
                completed = self._run(fixture, "-Tag", "1.2.4", failure=(point,), check=False)

                # Then: rollback restores the original byte snapshots, not rewritten equivalents
                self.assertNotEqual(completed.returncode, 0, point)
                self.assertEqual(fixture.marker.read_bytes(), before_marker)
                self.assertEqual(fixture.path_file.read_bytes(), before_path)
                self.assertEqual(completed.stdout, "")

    def test_rollback_failure_retains_backup_and_reports_recovery_location(self) -> None:
        # Given: an upgrade that fails after mutation and a deterministic rollback failure
        with self._fixture() as fixture:
            self._write_asset(fixture, "1.2.3")
            self._run(fixture, "-Tag", "1.2.3")
            before_marker = fixture.marker.read_bytes()
            before_path = fixture.path_file.read_bytes()
            self._write_asset(fixture, "1.2.4")

            # When: current restoration fails during rollback
            completed = self._run(
                fixture,
                "-Tag",
                "1.2.4",
                failure=("after-marker", "rollback-current"),
                check=False,
            )

            # Then: recovery data survives and its exact location is reported
            transactions = tuple(fixture.install_root.glob(".transaction-*"))
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(fixture.marker.read_bytes(), before_marker)
            self.assertEqual(fixture.path_file.read_bytes(), before_path)
            self.assertEqual(len(transactions), 1)
            self.assertTrue((transactions[0] / "current" / "evospec.exe").is_file())
            prefix = "rollback failed; recovery data retained at "
            reported = next(
                (line.removeprefix(prefix) for line in completed.stderr.splitlines() if line.startswith(prefix)),
                None,
            )
            if reported is None:
                self.fail("rollback recovery location was not reported")
            reported_path = Path(reported)
            self.assertTrue(reported_path.is_dir())
            self.assertTrue(os.path.samefile(reported_path, transactions[0]))
            self.assertEqual(completed.stdout, "")

    def test_archive_rejects_unsafe_and_incomplete_members_before_installation(self) -> None:
        # Given: malicious local archives with otherwise matching checksums
        for member in (
            "../escape",
            "evospec\\..\\escape",
            "/absolute",
            "outside/file",
            "evospec/unexpected",
            "evospec/rogue.exe",
            "EVOSPEC/EVOSPEC.EXE",
            "evospec//rogue.exe",
        ):
            with self.subTest(member=member), self._fixture() as fixture:
                self._write_asset(fixture, "1.2.3", extra=(member,))

                # When: the archive is installed
                completed = self._run(fixture, check=False)

                # Then: validation fails before persistent state changes
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(fixture.current.exists())
                self.assertFalse(fixture.marker.exists())
                self.assertEqual(completed.stdout, "")

    def _fixture(self) -> "PowerShellFixture":
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        fixture = PowerShellFixture(temporary, root / "assets", root / "install", root / "path.txt")
        return fixture

    @staticmethod
    def _write_asset(fixture: "PowerShellFixture", version: str, extra: tuple[str, ...] = ()) -> None:
        archive = fixture.assets / f"evospec-{version}-windows-x86_64.zip"
        with zipfile.ZipFile(archive, "w") as zip_file:
            for name in ("evospec.exe", "evospec-agent.exe", "evospec-agent-protocol.exe"):
                zip_file.write(Path(os.environ["SystemRoot"]) / "System32" / "attrib.exe", f"evospec/{name}")
            zip_file.writestr("evospec/_internal/runtime", "private")
            for member in extra:
                zip_file.writestr(member, "unsafe")
        (fixture.assets / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")

    def _run(self, fixture: "PowerShellFixture", *arguments: str, remote: bool = False, failure: str | tuple[str, ...] | None = None, gh_failure: str = "", check: bool = True) -> subprocess.CompletedProcess[str]:
        failure_value = failure if isinstance(failure, str) else ",".join(failure or ())
        environment = os.environ | {
            "EVOSPEC_FAKE_GH_ROOT": str(fixture.assets),
            "EVOSPEC_FAKE_GH_LOG": str(fixture.gh_log),
            "EVOSPEC_FAKE_GH_TAG": "v1.2.3",
            "EVOSPEC_FAKE_GH_FAILURE": gh_failure,
            "PATH": str(fixture.gh_directory) + os.pathsep + os.environ["PATH"],
        } | ({"EVOSPEC_INSTALL_TEST_FAIL_AT": failure_value} if failure else {})
        local_arguments = () if remote else ("-AssetDirectory", str(fixture.assets), "-ManifestPath", str(fixture.assets / "SHA256SUMS"))
        return subprocess.run(("pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "install.ps1"), *local_arguments, "-InstallRoot", str(fixture.install_root), "-PathFile", str(fixture.path_file), *arguments), check=check, text=True, capture_output=True, env=environment)


class PowerShellFixture:
    def __init__(self, temporary: TemporaryDirectory[str], assets: Path, install_root: Path, path_file: Path) -> None:
        self._temporary = temporary
        self.assets = assets
        self.assets.mkdir()
        self.install_root = install_root
        self.path_file = path_file
        self.gh_failure: str = ""
        self.release_tag: str = "v1.2.3"
        self.downloads: Path = assets.parent / "temporary downloads"
        self.downloads.mkdir()
        _ = (assets / "install.ps1").write_bytes((ROOT / "scripts" / "install.ps1").read_bytes())
        self.gh_directory = assets.parent / "fake-gh"
        self.gh_directory.mkdir()
        self.gh_log = assets.parent / "gh.log"
        self.gh_log.write_text("", encoding="utf-8")
        self.public_log = assets.parent / "public-download.log"
        self.public_log.write_text("", encoding="utf-8")
        self.public_failure = False
        (self.gh_directory / "gh.cmd").write_text(
            "@echo off\n"
            "echo %*>>\"%EVOSPEC_FAKE_GH_LOG%\"\n"
            "if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"auth\" if /I \"%~1\"==\"auth\" exit /b 1\n"
            "if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"download\" if /I \"%~1\"==\"release\" if /I \"%~2\"==\"download\" exit /b 1\n"
            "if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"bootstrap\" if /I \"%~1\"==\"release\" if /I \"%~2\"==\"download\" (echo fake download failure 1>&2 & exit /b 23)\n"
            "if /I \"%~1\"==\"auth\" (\n"
            "  exit /b 0\n"
            ")\n"
            "if /I \"%~1\"==\"release\" if /I \"%~2\"==\"view\" (\n"
            "  echo %EVOSPEC_FAKE_GH_TAG%\n"
            "  exit /b 0\n"
            ")\n"
            "if /I \"%~1\"==\"release\" if /I \"%~2\"==\"download\" (\n"
            "  shift\n"
            "  shift\n"
            "  set \"output=\"\n"
            "  set \"directory=\"\n"
            "  set \"clobber=\"\n"
            "  :arguments\n"
            "  if \"%~1\"==\"\" goto copy\n"
            "  if /I \"%~1\"==\"--pattern\" (set \"pattern=%~2\" & shift & shift & goto arguments)\n"
            "  if /I \"%~1\"==\"--output\" (set \"output=%~2\" & shift & shift & goto arguments)\n"
            "  if /I \"%~1\"==\"--dir\" (set \"directory=%~2\" & shift & shift & goto arguments)\n"
            "  if /I \"%~1\"==\"--clobber\" (set \"clobber=1\" & shift & goto arguments)\n"
            "  shift\n"
            "  goto arguments\n"
            "  :copy\n"
            "  if not defined output if defined directory set \"output=%directory%\\%pattern%\"\n"
            "  if \"%output%\"==\"-\" (type \"%EVOSPEC_FAKE_GH_ROOT%\\%pattern%\" & goto result)\n"
            "  if exist \"%output%\" if not defined clobber (echo output already exists 1>&2 & exit /b 1)\n"
            "  copy /Y \"%EVOSPEC_FAKE_GH_ROOT%\\%pattern%\" \"%output%\" >nul\n"
            "  if errorlevel 1 exit /b 1\n"
            "  if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"manifest-download\" if \"%pattern%\"==\"SHA256SUMS\" (echo manifest download interrupted 1>&2 & exit /b 23)\n"
            "  if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"archive-download\" if \"%pattern%\"==\"evospec-1.2.3-windows-x86_64.zip\" (echo archive download interrupted 1>&2 & exit /b 23)\n"
            "  :result\n"
            "  if \"%EVOSPEC_FAKE_GH_FAILURE%\"==\"bootstrap\" (echo fake download failure 1>&2 & exit /b 23)\n"
            "  exit /b %errorlevel%\n"
            ")\n"
            "exit /b 1\n",
            encoding="utf-8",
        )

    def run_powershell(self, script: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ | {
            "EVOSPEC_FAKE_GH_ROOT": str(self.assets),
            "EVOSPEC_FAKE_GH_LOG": str(self.gh_log),
            "EVOSPEC_FAKE_GH_TAG": self.release_tag,
            "EVOSPEC_FAKE_GH_FAILURE": self.gh_failure,
            "EVOSPEC_TEST_PUBLIC_LOG": str(self.public_log),
            "EVOSPEC_FAKE_PUBLIC_FAILURE": "1" if self.public_failure else "0",
            "EVOSPEC_TEST_INSTALL_ROOT": str(self.install_root),
            "EVOSPEC_TEST_PATH_FILE": str(self.path_file),
            "EVOSPEC_TEST_INSTALL_SCRIPT": str(ROOT / "scripts" / "install.ps1"),
            "PATH": str(self.gh_directory) + os.pathsep + os.environ["PATH"],
            "TEMP": str(self.downloads),
            "TMP": str(self.downloads),
            "LOCALAPPDATA": str(self.assets.parent),
        }
        setup = (
            "$ErrorActionPreference = 'Stop'\n"
            "$PSDefaultParameterValues = @{'*:InstallRoot'=$env:EVOSPEC_TEST_INSTALL_ROOT; '*:PathFile'=$env:EVOSPEC_TEST_PATH_FILE}\n"
            "function Invoke-WebRequest {\n"
            "[CmdletBinding()] param([uri]$Uri, [string]$OutFile)\n"
            "if ($Uri.Scheme -ne 'https' -or $Uri.Host -ne 'github.com') { throw 'unexpected download host' }\n"
            "Add-Content -LiteralPath $env:EVOSPEC_TEST_PUBLIC_LOG -Value $Uri.AbsoluteUri\n"
            "if ($env:EVOSPEC_FAKE_PUBLIC_FAILURE -eq '1') { throw 'synthetic public download failure' }\n"
            "$asset = [IO.Path]::GetFileName($Uri.AbsolutePath)\n"
            "Copy-Item -LiteralPath (Join-Path $env:EVOSPEC_FAKE_GH_ROOT $asset) -Destination $OutFile\n"
            "}\n"
        )
        return subprocess.run(
            ("pwsh", "-NoProfile", "-Command", setup + script),
            text=True, capture_output=True, env=environment, cwd=self.assets.parent, timeout=120,
        )

    @property
    def current(self) -> Path:
        return self.install_root / "current"

    @property
    def marker(self) -> Path:
        return self.install_root / ".evospec-install"

    def __enter__(self) -> "PowerShellFixture":
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self._temporary.cleanup()
