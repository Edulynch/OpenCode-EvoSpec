from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).parents[1]
WORKFLOW: Final = ROOT / ".github" / "workflows" / "phase4c-validation.yml"


def job_block(name: str) -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)", workflow)
    assert match is not None, f"Missing job: {name}"
    return match.group(1)


class Phase4CValidationWorkflowTests(unittest.TestCase):
    def runs_on(self, name: str) -> str:
        match = re.search(r"(?m)^    runs-on: (.+)$", job_block(name))
        self.assertIsNotNone(match, f"Missing runs-on for {name}")
        return match.group(1) if match else ""

    def test_triggers_manual_input_and_permissions(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  pull_request:\n  push:\n  workflow_dispatch:", workflow)
        smoke = workflow.split("      smoke_only:\n")[1].split("\npermissions:")[0]
        for field in ("type: boolean", "required: true", "default: false"):
            self.assertIn(field, smoke)
        for removed in ("runner_target", "RUNNER_TARGET", "self-hosted"):
            self.assertNotIn(removed, workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_all_jobs_use_hosted_runners_and_keep_check_names(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        expected = {
            "runner-smoke": "ubuntu-22.04",
            "windows-runner-smoke": "windows-2022",
            "validate": "${{ matrix.os }}",
            "native": "${{ matrix.runner }}",
            "release-candidate": "ubuntu-22.04",
        }
        self.assertEqual({name: self.runs_on(name) for name in expected}, expected)
        self.assertEqual(re.findall(r"(?m)^    runs-on: (.+)$", workflow), list(expected.values()))
        for name, check in (
            ("runner-smoke", "Linux runner smoke"),
            ("windows-runner-smoke", "Windows runner smoke"),
            ("validate", "${{ matrix.os }} / Python ${{ matrix.python }}"),
            ("native", "Native / ${{ matrix.platform }}"),
            ("release-candidate", "Release candidate"),
        ):
            self.assertIn(f"name: {check}", job_block(name))
        validate = job_block("validate")
        self.assertIn("os: [ubuntu-22.04, windows-2022]", validate)
        self.assertIn('python: ["3.11"]', validate)
        native = job_block("native")
        self.assertEqual(re.findall(r"- runner: ([\w.-]+)", native), ["windows-2022", "ubuntu-22.04"])
        self.assertEqual(re.findall(r"platform: ([\w-]+)", native), ["windows-x86_64", "linux-x86_64"])

    def test_manual_smokes_verify_hosted_environment_os_and_architecture(self) -> None:
        linux = job_block("runner-smoke")
        windows = job_block("windows-runner-smoke")
        gate = "if: ${{ github.event_name == 'workflow_dispatch' && inputs.smoke_only }}"
        for job in (linux, windows):
            self.assertIn(gate, job)
            self.assertIn("timeout-minutes: 5", job)
            self.assertEqual(job.count("      - name:"), 1)
            self.assertNotRegex(job, r"uses:|needs:|checkout|install|build|upload|assemble|secrets\.")
            for variable, context in (("RUNNER_OS", "os"), ("RUNNER_ARCH", "arch"), ("RUNNER_ENVIRONMENT", "environment"), ("RUNNER_NAME", "name")):
                self.assertIn(f"{variable}: ${{{{ runner.{context} }}}}", job)
            self.assertNotIn("RUNNER_TARGET", job)
        linux_script = linux.split("        run: |\n")[1]
        self.assertIn('test "$RUNNER_OS" = Linux', linux_script)
        self.assertIn('test "$RUNNER_ARCH" = X64', linux_script)
        self.assertIn('test "$RUNNER_ENVIRONMENT" = github-hosted', linux_script)
        self.assertIn("printf 'name=%s os=%s arch=%s environment=%s\\n'", linux_script)
        windows_script = windows.split("        run: |\n")[1]
        self.assertIn("if ($env:RUNNER_OS -ne 'Windows')", windows_script)
        self.assertIn("if ($env:RUNNER_ARCH -ne 'X64')", windows_script)
        self.assertIn("if ($env:RUNNER_ENVIRONMENT -ne 'github-hosted')", windows_script)
        self.assertIn('Write-Output "name=$env:RUNNER_NAME os=$env:RUNNER_OS arch=$env:RUNNER_ARCH environment=$env:RUNNER_ENVIRONMENT"', windows_script)

    def test_full_dispatch_runs_and_fork_release_guard_is_preserved(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        full_dispatch = "if: ${{ github.event_name != 'workflow_dispatch' || !inputs.smoke_only }}"
        for name in ("validate", "native"):
            self.assertIn(full_dispatch, job_block(name))
        candidate = job_block("release-candidate")
        self.assertIn(
            "if: ${{ github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && !inputs.smoke_only) || (github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository) }}",
            candidate,
        )
        self.assertIn("needs: native", candidate)
        self.assertIn("actions/download-artifact", candidate)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_pinned_checks_native_assets_and_release_candidate_remain_intact(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        validate = job_block("validate")
        for step in ("Source tests", "Typecheck", "Validate release identity", "Build release artifacts", "Installed artifact smoke"):
            self.assertIn(step, validate)
        self.assertIn("uv python install ${{ matrix.python }}", validate)
        self.assertIn("tests/installed_runtime_smoke.py", validate)
        native = job_block("native")
        for step in ("Build native bundle", "Smoke native bundle", "Test PowerShell installer", "Test documented Windows native bootstrap", "Test POSIX installer", "Create versioned native archive", "Validate native archive before upload"):
            self.assertIn(step, native)
        self.assertIn("name: native-${{ matrix.platform }}", native)
        self.assertIn("path: dist/native/*.zip", native)
        self.assertIn("if-no-files-found: error", native)
        self.assertLess(native.index("Validate native archive before upload"), native.index("actions/upload-artifact"))
        candidate = job_block("release-candidate")
        self.assertIn("pattern: native-*", candidate)
        self.assertIn('expected_files="SHA256SUMS evospec-${version}-linux-x86_64.zip evospec-${version}-windows-x86_64.zip install.ps1 install.sh"', candidate)
        self.assertIn('test "$actual_files" = "$expected_files"', candidate)
        self.assertIn('test "$(find dist/release-candidate -maxdepth 1 -type f | wc -l | tr -d \' \')" = 5', candidate)
        self.assertIn('test "$(wc -l < dist/release-candidate/SHA256SUMS | tr -d \' \')" = 2', candidate)
        self.assertIn("cp scripts/install.ps1 scripts/install.sh dist/release-candidate/", candidate)
        self.assertEqual(workflow.count("uses: actions/checkout@"), 3)
        self.assertEqual(workflow.count("persist-credentials: false"), 3)
        for forbidden in ("action-gh-release", "gh release", "git tag"):
            self.assertNotIn(forbidden, workflow)
        actions = re.findall(r"uses: ([^\s]+)", workflow)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action) for action in actions))

    def test_tag_restore_uses_step_scoped_secret_and_safe_environment_ref(self) -> None:
        validate = job_block("validate")
        step = validate.split("      - name: Restore triggering tag object\n", 1)[1].split("\n      - uses:", 1)[0]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", step)
        self.assertIn("shell: bash", step)
        self.assertIn("TRIGGERING_REF: ${{ github.ref }}", step)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", step)
        run = step.split("        run: |\n", 1)[1]
        self.assertNotIn("${{ github.ref }}", run)
        self.assertIn("set +x", run)
        self.assertNotIn("set -x", run)
        self.assertIn("GIT_CONFIG_COUNT=1", run)
        self.assertIn("GIT_CONFIG_KEY_0='http.https://github.com/.extraheader'", run)
        self.assertIn('GIT_CONFIG_VALUE_0="$auth_header"', run)
        self.assertLess(run.index("::add-mask::"), run.index("git fetch"))
        self.assertIn('git fetch --no-tags origin "+${TRIGGERING_REF}:${TRIGGERING_REF}"', run)

    def test_tag_restore_masks_synthetic_header_before_git_and_treats_shell_ref_as_data(self) -> None:
        validate = job_block("validate")
        step = validate.split("      - name: Restore triggering tag object\n", 1)[1].split("\n      - uses:", 1)[0]
        run = step.split("        run: |\n", 1)[1]
        bash = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe" if git else None
            bash = str(git_bash) if git_bash and git_bash.is_file() else None
        if bash is None:
            self.skipTest("a Bash executable suitable for the workflow shell fixture is required")
        ref = "refs/tags/$(printf${IFS}SHELL_INJECTED)"
        self.assertEqual(
            subprocess.run(("git", "check-ref-format", ref), check=False, capture_output=True).returncode,
            0,
            "synthetic shell-significant ref must be a valid Git ref",
        )
        token = "synthetic-token-only"
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        stub = (
            "git() { printf 'GIT_CONFIG_COUNT=%s\n' \"$GIT_CONFIG_COUNT\"; "
            "printf 'GIT_CONFIG_KEY_0=%s\n' \"$GIT_CONFIG_KEY_0\"; "
            "printf 'GIT_CONFIG_VALUE_0=%s\n' \"$GIT_CONFIG_VALUE_0\"; "
            "printf 'ARG=%s\n' \"$@\"; }\n"
        )
        completed = subprocess.run(
            (bash, "-c", stub + run),
            check=False,
            capture_output=True,
            text=True,
            env=os.environ | {"TRIGGERING_REF": ref, "GITHUB_TOKEN": token},
        )
        self.assertEqual(completed.returncode, 0, "synthetic tag-fetch harness failed")
        self.assertEqual(completed.stderr, "", "synthetic tag-fetch harness wrote diagnostics")
        lines = completed.stdout.splitlines()
        expected = [
            f"::add-mask::AUTHORIZATION: basic {encoded}",
            "GIT_CONFIG_COUNT=1",
            "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
            f"GIT_CONFIG_VALUE_0=AUTHORIZATION: basic {encoded}",
            "ARG=fetch",
            "ARG=--no-tags",
            "ARG=origin",
            f"ARG=+{ref}:{ref}",
        ]
        self.assertEqual(lines, expected, "masking or shell-safe ref argument order changed")
    def test_ci_documentation_describes_hosted_workflow_and_historical_runners(self) -> None:
        documentation = (ROOT / "docs" / "self-hosted-ci.md").read_text(encoding="utf-8")
        for required in ("GitHub-hosted", "ubuntu-22.04", "windows-2022", "smoke_only", "Fork pull requests", "self-hosted", "does not delete"):
            self.assertIn(required, documentation)
        for obsolete in ("C:\\actions-runners", "Administrator PowerShell", "Get-Service", "Start-Service", "registration token"):
            self.assertNotIn(obsolete, documentation)
