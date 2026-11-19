"""Core-owned, bounded fingerprints for the exact local repository inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import unicodedata
from collections.abc import Mapping

INPUT_FINGERPRINT_VERSION = "r3-root-input-v2"
INPUT_POLICY_ID = INPUT_FINGERPRINT_VERSION
MAX_INPUT_FILES = 50_000
MAX_INPUT_ENTRIES = 100_000
MAX_INPUT_BYTES = 1_073_741_824
MAX_INPUT_DEPTH = 128
_CHUNK_SIZE = 1024 * 1024

_INPUT_POLICY = {
    "version": INPUT_FINGERPRINT_VERSION,
    "excluded_root_entries": [".evospec", ".git"],
    "files": ["normalized_relative_path", "size", "executable_bit", "content_sha256"],
    "directories": ["normalized_relative_path", "presence"],
    "max_files": MAX_INPUT_FILES,
    "max_entries": MAX_INPUT_ENTRIES,
    "max_total_bytes": MAX_INPUT_BYTES,
    "max_depth": MAX_INPUT_DEPTH,
    "reject": ["symlinks", "reparse_points", "hard_links", "special_files", "unsafe_or_colliding_paths"],
}
INPUT_POLICY_DIGEST = hashlib.sha256(
    json.dumps(_INPUT_POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

TEST_ADMISSION_POLICY_ID = "r3-objective-test-v1"
TEST_ENVIRONMENT_PROFILE_ID = "evospec-sanitized-test-env-v1"
COVERAGE_CONTRACT_VERSION = "r3-known-input-contract-v1"
LEGACY_INPUT_POLICY_ID = "r3-root-input-v1"
_LEGACY_INPUT_POLICY = {
    "version": LEGACY_INPUT_POLICY_ID,
    "excluded_root_entries": [".evospec", ".git"],
    "entries": ["normalized_relative_path", "size", "executable_bit", "content_sha256"],
    "max_files": MAX_INPUT_FILES,
    "max_entries": MAX_INPUT_ENTRIES,
    "max_total_bytes": MAX_INPUT_BYTES,
    "max_depth": MAX_INPUT_DEPTH,
    "reject": ["symlinks", "reparse_points", "hard_links", "special_files", "unsafe_or_colliding_paths"],
}
LEGACY_INPUT_POLICY_DIGEST = hashlib.sha256(
    json.dumps(_LEGACY_INPUT_POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
LEGACY_R3B_COVERAGE_CONTRACT = (
    "r3-root-input-v1 covers every regular file below the canonical repository root, "
    "including ignored and untracked files, by sorted normalized relative path, size, "
    "executable bit, and content SHA-256; it excludes only root .git and .evospec. "
    "It rejects symlinks, reparse points/junctions, hard links, special or unreadable entries, "
    "collisions, concurrent changes, and inputs beyond 50000 files, 100000 entries, "
    "1073741824 bytes, or depth 128. This is a trusted-local operational boundary, "
    "not an OS sandbox or proof that arbitrary test code has no hidden reads."
)
R3B_COVERAGE_CONTRACT = (
    "r3-root-input-v2 covers every regular file and directory below the canonical repository root, "
    "including ignored and untracked entries. Files bind sorted normalized relative path, size, "
    "executable bit, and content SHA-256; directories bind sorted normalized relative path and presence. "
    "It excludes only root .git and .evospec. "
    "It rejects symlinks, reparse points, hard links, special or unreadable entries, "
    "collisions, concurrent changes, and inputs beyond 50000 files, 100000 entries, "
    "1073741824 bytes, or depth 128. Each criterion additionally requires a TTY-approved "
    "root-input declaration bound to this whole-root fingerprint. External, excluded, credential, "
    "unprofiled-environment, and unsupported metadata dependencies are rejected. This is a "
    "trusted-local declaration, not an observed-read claim, OS sandbox, or proof that arbitrary "
    "test code has no hidden reads."
)
_TEST_ENVIRONMENT_PROFILE_POLICY = {
    "id": TEST_ENVIRONMENT_PROFILE_ID,
    "included": [
        "PATH=<runtime-directory>", "PYTHONHASHSEED=0",
        "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
        "TEMP=<canonical-repository-root>", "TMP=<canonical-repository-root>",
        "TMPDIR=<canonical-repository-root>", "TZ=UTC",
        "Windows-only SystemRoot and WINDIR when present",
    ],
    "excluded": [
        "HOME", "USERPROFILE", "APPDATA", "credential variables",
        "user configuration variables", "all other inherited variables",
    ],
    "limitations": "trusted-local operational profile; not a sandbox",
}
TEST_ENVIRONMENT_PROFILE_DIGEST = hashlib.sha256(
    json.dumps(
        _TEST_ENVIRONMENT_PROFILE_POLICY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_TEST_ADMISSION_POLICY = {
    "id": TEST_ADMISSION_POLICY_ID,
    "input_policy_id": INPUT_POLICY_ID,
    "input_policy_digest": INPUT_POLICY_DIGEST,
    "coverage_contract": R3B_COVERAGE_CONTRACT,
    "supported_descriptors": ["python-unittest:tests.<module>", "node-test:tests/<file>.test.mjs"],
    "known_input_contract": "per-criterion TTY approval bound to exact descriptor and whole-root fingerprint",
    "supported_metadata": [
        "root-file path,size,executable_bit,content_sha256",
        "root-directory path,presence",
    ],
    "unsupported_dependencies": [
        "external paths or services", ".git or .evospec", "credentials",
        "unprofiled environment or home configuration", "other filesystem metadata",
    ],
    "environment_profile_id": TEST_ENVIRONMENT_PROFILE_ID,
    "environment_profile_policy_digest": TEST_ENVIRONMENT_PROFILE_DIGEST,
    "environment_variables": [
        "PATH=<runtime-directory>", "PYTHONHASHSEED=0", "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONNOUSERSITE=1", "TEMP=<repository-root>", "TMP=<repository-root>",
        "TMPDIR=<repository-root>", "TZ=UTC",
        "Windows-only SystemRoot and WINDIR when present",
    ],
    "runtime": ["absolute_executable", "sha256", "version", "exact_argv"],
    "evidence": "pre_and_post_input_fingerprints_equal",
    "limitation": "trusted-local operational evidence, not universal sandbox proof",
}
TEST_ADMISSION_POLICY_DIGEST = hashlib.sha256(
    json.dumps(_TEST_ADMISSION_POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
_LEGACY_TEST_ADMISSION_POLICY = {
    "id": TEST_ADMISSION_POLICY_ID,
    "input_policy_id": LEGACY_INPUT_POLICY_ID,
    "input_policy_digest": LEGACY_INPUT_POLICY_DIGEST,
    "coverage_contract": LEGACY_R3B_COVERAGE_CONTRACT,
    "supported_descriptors": ["python-unittest:tests.<module>", "node-test:tests/<file>.test.mjs"],
    "external_inputs": "none; known external or non-profile dependencies are unsupported",
    "environment_profile_id": TEST_ENVIRONMENT_PROFILE_ID,
    "environment_profile_policy_digest": TEST_ENVIRONMENT_PROFILE_DIGEST,
    "environment_variables": [
        "PATH=<runtime-directory>", "PYTHONHASHSEED=0", "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONNOUSERSITE=1", "TEMP=<repository-root>", "TMP=<repository-root>",
        "TMPDIR=<repository-root>", "TZ=UTC",
        "Windows-only SystemRoot and WINDIR when present",
    ],
    "runtime": ["absolute_executable", "sha256", "version", "exact_argv"],
    "evidence": "pre_and_post_input_fingerprints_equal",
    "limitation": "trusted-local operational evidence, not universal sandbox proof",
}
LEGACY_TEST_ADMISSION_POLICY_DIGEST = hashlib.sha256(
    json.dumps(
        _LEGACY_TEST_ADMISSION_POLICY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
R3B_APPROVAL_POLICY_TEXT = (
    f"input-policy {INPUT_POLICY_ID} sha256 {INPUT_POLICY_DIGEST}; "
    f"admission-policy {TEST_ADMISSION_POLICY_ID} sha256 {TEST_ADMISSION_POLICY_DIGEST}; "
    f"coverage {R3B_COVERAGE_CONTRACT}; "
    f"environment-profile {TEST_ENVIRONMENT_PROFILE_ID} policy-sha256 "
    f"{TEST_ENVIRONMENT_PROFILE_DIGEST}; "
    "per-criterion TTY-approved root-input declarations are bound to the whole-root fingerprint; "
    "known external, credential, home-configuration, and non-profile dependencies are unsupported; "
    "trusted-local operational evidence, not an OS sandbox"
)
LEGACY_R3B_APPROVAL_POLICY_TEXT = (
    f"input-policy {LEGACY_INPUT_POLICY_ID} sha256 {LEGACY_INPUT_POLICY_DIGEST}; "
    f"admission-policy {TEST_ADMISSION_POLICY_ID} sha256 {LEGACY_TEST_ADMISSION_POLICY_DIGEST}; "
    f"coverage {LEGACY_R3B_COVERAGE_CONTRACT}; "
    f"environment-profile {TEST_ENVIRONMENT_PROFILE_ID} policy-sha256 "
    f"{TEST_ENVIRONMENT_PROFILE_DIGEST}; external-input-contract none; "
    "known external, credential, home-configuration, and non-profile dependencies are unsupported; "
    "trusted-local operational evidence, not an OS sandbox"
)


class InputFingerprintError(Exception):
    """Raised when a complete, stable repository snapshot cannot be established."""


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    version: str
    policy_id: str
    policy_digest: str
    repository_id: str
    fingerprint: str
    file_count: int
    directory_count: int
    total_bytes: int
    file_paths: tuple[str, ...] = ()
    directory_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FileEntry:
    path: str
    size: int
    executable: bool
    content_sha256: str


_WINDOWS_STAT_CTIME_DIFFERENCE = os.name == "nt"


def _same_file(left: os.stat_result, right: os.stat_result, *, path_to_descriptor: bool = False) -> bool:
    # En Windows, lstat() puede exponer la creación en ctime y fstat() el último
    # cambio de metadatos. Comparar birthtime evita rechazar el mismo archivo.
    if path_to_descriptor and _WINDOWS_STAT_CTIME_DIFFERENCE:
        left_birthtime = getattr(left, "st_birthtime_ns", None)
        right_birthtime = getattr(right, "st_birthtime_ns", None)
        same_creation_time = (
            left_birthtime == right_birthtime
            if left_birthtime is not None and right_birthtime is not None
            else left.st_ctime_ns == right.st_ctime_ns
        )
    else:
        # Dos fstat() siguen exigiendo el mismo ctime para detectar cambios
        # mientras el descriptor está abierto.
        same_creation_time = left.st_ctime_ns == right.st_ctime_ns

    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and same_creation_time
        and left.st_nlink == right.st_nlink
        and getattr(left, "st_file_attributes", 0) == getattr(right, "st_file_attributes", 0)
    )


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _read_file(path: Path, before: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InputFingerprintError("repository file cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or opened.st_nlink != 1
            or not _same_file(before, opened, path_to_descriptor=True)
        ):
            raise InputFingerprintError("repository file changed or is unsafe during fingerprinting")
        digest = hashlib.sha256()
        read_bytes = 0
        try:
            while chunk := os.read(descriptor, _CHUNK_SIZE):
                read_bytes += len(chunk)
                if read_bytes > MAX_INPUT_BYTES:
                    raise InputFingerprintError("repository input exceeds the fixed byte limit")
                digest.update(chunk)
            after = os.fstat(descriptor)
            current = path.lstat()
        except OSError as error:
            raise InputFingerprintError("repository file changed or became unreadable during fingerprinting") from error
        if (
            _is_reparse_point(after)
            or _is_reparse_point(current)
            or not _same_file(opened, after)
            or not _same_file(after, current, path_to_descriptor=True)
            or read_bytes != after.st_size
        ):
            raise InputFingerprintError("repository file changed during fingerprinting")
        return digest.hexdigest()
    finally:
        try:
            os.close(descriptor)
        except OSError as error:
            raise InputFingerprintError("repository file handle could not be closed safely") from error


def _scan_once(root: Path) -> tuple[tuple[_FileEntry, ...], tuple[str, ...], int]:
    entries: list[_FileEntry] = []
    directories: list[str] = []
    seen_paths: set[str] = set()
    seen_folded: set[str] = set()
    total_bytes = 0
    entry_count = 0

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        nonlocal entry_count, total_bytes
        try:
            with os.scandir(directory) as iterator:
                children = []
                for child in iterator:
                    if not parts and child.name in (".git", ".evospec"):
                        continue
                    entry_count += 1
                    if entry_count > MAX_INPUT_ENTRIES:
                        raise InputFingerprintError("repository inputs exceed the fixed entry limit")
                    children.append(child)
                children.sort(key=lambda item: item.name)
        except OSError as error:
            raise InputFingerprintError("repository contains an unreadable directory") from error
        for child in children:
            name = child.name
            if not name or name in (".", "..") or "\\" in name or ":" in name:
                raise InputFingerprintError("repository contains an unsafe path")
            if unicodedata.normalize("NFC", name) != name:
                raise InputFingerprintError("repository contains a non-normalized path")
            relative = "/".join((*parts, name))
            if relative.startswith("/") or any(part in ("", ".", "..") for part in relative.split("/")):
                raise InputFingerprintError("repository contains an unsafe path")
            folded = relative.casefold()
            if relative in seen_paths or folded in seen_folded:
                raise InputFingerprintError("repository contains colliding normalized paths")
            seen_paths.add(relative)
            seen_folded.add(folded)
            try:
                info = Path(child.path).lstat()
            except OSError as error:
                raise InputFingerprintError("repository entry cannot be inspected") from error
            if _is_reparse_point(info) or stat.S_ISLNK(info.st_mode):
                raise InputFingerprintError("repository contains a symlink or reparse point")
            if stat.S_ISDIR(info.st_mode):
                if len(parts) >= MAX_INPUT_DEPTH:
                    raise InputFingerprintError("repository input path exceeds the fixed depth limit")
                directories.append(relative)
                walk(Path(child.path), (*parts, name))
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InputFingerprintError("repository contains a special file or hard-link alias")
            if info.st_size < 0:
                raise InputFingerprintError("repository file has an invalid size")
            if len(entries) >= MAX_INPUT_FILES or total_bytes + info.st_size > MAX_INPUT_BYTES:
                raise InputFingerprintError("repository inputs exceed the fixed scan limits")
            digest = _read_file(Path(child.path), info)
            total_bytes += info.st_size
            executable = bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            entries.append(_FileEntry(relative, info.st_size, executable, digest))

    walk(root, ())
    return (
        tuple(sorted(entries, key=lambda item: item.path)),
        tuple(sorted(directories)),
        total_bytes,
    )


def repository_input_snapshot(root: Path, repository_id: str) -> InputSnapshot:
    """Return a full stable digest, or fail closed without a partial result."""
    try:
        original_info = root.lstat()
        if stat.S_ISLNK(original_info.st_mode) or _is_reparse_point(original_info):
            raise InputFingerprintError("repository root is a symlink or reparse point")
        resolved = root.resolve(strict=True)
        root_info = resolved.stat()
    except InputFingerprintError:
        raise
    except (OSError, RuntimeError) as error:
        raise InputFingerprintError("repository root cannot be inspected") from error
    if not stat.S_ISDIR(root_info.st_mode) or _is_reparse_point(root_info):
        raise InputFingerprintError("repository root is not a regular directory")
    if type(repository_id) is not str or not repository_id:
        raise InputFingerprintError("repository identity is unavailable")

    first, first_directories, first_total = _scan_once(resolved)
    second, second_directories, second_total = _scan_once(resolved)
    try:
        root_after = resolved.stat()
    except OSError as error:
        raise InputFingerprintError("repository root changed during fingerprinting") from error
    if (
        first != second
        or first_directories != second_directories
        or first_total != second_total
        or root_info.st_dev != root_after.st_dev
        or root_info.st_ino != root_after.st_ino
        or root_info.st_mode != root_after.st_mode
        or root_info.st_mtime_ns != root_after.st_mtime_ns
        or root_info.st_ctime_ns != root_after.st_ctime_ns
    ):
        raise InputFingerprintError("repository changed while its input fingerprint was computed")
    try:
        root_identity = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    except UnicodeError as error:
        raise InputFingerprintError("repository root cannot be encoded canonically") from error
    if repository_id != root_identity:
        raise InputFingerprintError("repository identity does not match the local root")
    payload = {
        "version": INPUT_FINGERPRINT_VERSION,
        "policy": {"id": INPUT_POLICY_ID, "digest": INPUT_POLICY_DIGEST},
        "root_identity": root_identity,
        "repository_id": repository_id,
        "files": [
            [item.path, item.size, item.executable, item.content_sha256]
            for item in first
        ],
        "directories": list(first_directories),
    }
    try:
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    except (UnicodeError, ValueError) as error:
        raise InputFingerprintError("repository paths cannot be encoded canonically") from error
    return InputSnapshot(
        INPUT_FINGERPRINT_VERSION,
        INPUT_POLICY_ID,
        INPUT_POLICY_DIGEST,
        repository_id,
        fingerprint,
        len(first),
        len(first_directories),
        first_total,
        tuple(item.path for item in first),
        first_directories,
    )


def sanitized_test_environment(
    root: Path,
    runtime_executable: str,
    inherited_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the fixed child profile from only its runtime and repository root."""
    try:
        repository = str(root.resolve(strict=True))
        executable = Path(runtime_executable)
        if not executable.is_absolute() or str(executable.resolve(strict=True)) != runtime_executable:
            raise InputFingerprintError("test runtime executable is not canonical")
    except (OSError, RuntimeError) as error:
        raise InputFingerprintError("test environment profile cannot be resolved") from error
    environment = {
        "PATH": str(executable.parent),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TEMP": repository,
        "TMP": repository,
        "TMPDIR": repository,
        "TZ": "UTC",
    }
    inherited = os.environ if inherited_environment is None else inherited_environment
    if os.name == "nt":
        for name in ("SystemRoot", "WINDIR"):
            value = inherited.get(name)
            if value is not None:
                if not value or len(value) > 2048:
                    raise InputFingerprintError("test environment contains an invalid system path")
                environment[name] = value
    return environment


def test_environment_profile_digest(environment: dict[str, str]) -> str:
    """Hash the fixed environment profile without retaining its values in evidence."""
    normalized = {key: environment[key] for key in sorted(environment)}
    payload = {"environment": normalized, "profile_id": TEST_ENVIRONMENT_PROFILE_ID}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = (
    "INPUT_FINGERPRINT_VERSION",
    "INPUT_POLICY_DIGEST",
    "INPUT_POLICY_ID",
    "LEGACY_INPUT_POLICY_DIGEST",
    "LEGACY_INPUT_POLICY_ID",
    "LEGACY_R3B_APPROVAL_POLICY_TEXT",
    "LEGACY_R3B_COVERAGE_CONTRACT",
    "LEGACY_TEST_ADMISSION_POLICY_DIGEST",
    "COVERAGE_CONTRACT_VERSION",
    "InputFingerprintError",
    "InputSnapshot",
    "MAX_INPUT_BYTES",
    "MAX_INPUT_DEPTH",
    "MAX_INPUT_ENTRIES",
    "MAX_INPUT_FILES",
    "sanitized_test_environment",
    "test_environment_profile_digest",
    "TEST_ADMISSION_POLICY_DIGEST",
    "TEST_ADMISSION_POLICY_ID",
    "TEST_ENVIRONMENT_PROFILE_DIGEST",
    "TEST_ENVIRONMENT_PROFILE_ID",
    "R3B_COVERAGE_CONTRACT",
    "R3B_APPROVAL_POLICY_TEXT",
    "repository_input_snapshot",
)
