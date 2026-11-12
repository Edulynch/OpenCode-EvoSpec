"""Install only manifest-owned OpenCode V2 resources transactionally."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Protocol, cast

from .persistence import (
    IntegrationRegistryError,
    integration_registry_content,
    integration_registry_path,
    load_integration_providers,
)

PLUGIN = ".opencode/plugins/evospec/index.ts"
CONTINUATION = ".opencode/plugins/evospec/continuation.mjs"
MANIFEST = ".evospec/opencode-resources.json"
SKILL = ".opencode/skills/evospec/SKILL.md"
COMMANDS = (
    "evospec.init.md", "evospec.start.md", "evospec.continue.md", "evospec.status.md",
    "evospec.clarify.md", "evospec.review.md", "evospec.converge.md", "evospec.context.md",
    "evospec.doctor.md",
)
AGENT_COMMAND_PLACEHOLDER = "__EVOSPEC_AGENT_COMMAND__"
CLI_COMMAND_PLACEHOLDER = "__EVOSPEC_CLI_COMMAND__"
LEGACY_V1_ONLY_RESOURCES = (
    ".opencode/tools/evospec.ts",
    ".opencode/instructions/evospec.md",
)
LEGACY_V1_MARKERS = (
    "<!-- EvoSpec-managed: opencode-v1 -->",
    "// EvoSpec-managed: opencode-v1",
)


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    changed: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenCodeIntegrationInspection:
    """Read-only local projection of OpenCode V2 integration ownership."""

    registration: str
    manifest: str
    resources: tuple[tuple[str, str], ...]

    @property
    def state(self) -> str:
        statuses = (self.registration, self.manifest) + tuple(
            status for _, status in self.resources
        )
        for status in ("UNREADABLE", "CONFLICT", "STALE", "MISSING"):
            if status in statuses:
                return status
        return "CURRENT"


@dataclass(frozen=True, slots=True)
class _Operation:
    path: Path
    content: bytes | None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    path: Path
    exists: bool
    content: bytes


@dataclass(frozen=True, slots=True)
class OpenCodeIntegrationProvider:
    """The OpenCode V2 adapter registered by the generic integration registry."""

    @property
    def name(self) -> str:
        return "opencode"

    def discover_legacy(self, root: Path) -> bool:
        """Select only a V2 manifest or detectable V1 residue for safe preflight."""
        safe_root = root.resolve()
        manifest = root / MANIFEST
        return manifest.exists() or bool(_legacy_v1_conflicts(root, safe_root))

    def inspect(self, root: Path) -> OpenCodeIntegrationInspection:
        """Inspect manifest ownership and packaged resources without remediation."""
        resources = _resource_bytes()
        safe_root = root.resolve()
        manifest_state, ownership = _read_manifest(root, safe_root, resources)
        if manifest_state == "CURRENT" and ownership is not None:
            manifest_state = "CURRENT" if ownership == _resource_hashes(resources) else "STALE"
        inspected = tuple(
            (
                target,
                _inspect_resource(root / target, safe_root, content, None if ownership is None else ownership.get(target)),
            )
            for target, content in resources.items()
        )
        legacy = tuple((path, "CONFLICT") for path in _legacy_v1_conflicts(root, safe_root))
        try:
            registered = self.name in load_integration_providers(root)
        except IntegrationRegistryError:
            registration = "UNREADABLE"
        else:
            registration = "CURRENT" if registered else "MISSING"
        return OpenCodeIntegrationInspection(
            registration,
            manifest_state,
            inspected + legacy,
        )

    def integrate(
        self, root: Path, *, dry_run: bool, remove: bool, registry_content: str | None = None
    ) -> IntegrationResult:
        return integrate(
            root, dry_run=dry_run, remove=remove, registry_content=registry_content,
        )


class IntegrationProvider(Protocol):
    """One project-local integration provider."""

    @property
    def name(self) -> str: ...

    def discover_legacy(self, root: Path) -> bool: ...

    def integrate(
        self, root: Path, *, dry_run: bool, remove: bool, registry_content: str | None = None
    ) -> IntegrationResult: ...


@dataclass(frozen=True, slots=True)
class IntegrationRegistry:
    """Dispatch registered integration providers without lifecycle-state coupling."""

    providers: tuple[IntegrationProvider, ...]

    def __init__(self, providers: Iterable[IntegrationProvider]) -> None:
        entries = tuple(providers)
        names = tuple(provider.name for provider in entries)
        if not entries or len(names) != len(set(names)):
            raise ValueError("integration providers must have unique names")
        object.__setattr__(self, "providers", entries)

    def integrate(
        self, root: Path, provider_name: str, *, dry_run: bool, remove: bool
    ) -> IntegrationResult:
        provider = self._provider(provider_name)
        registered = set(load_integration_providers(root))
        if remove:
            registered.discard(provider.name)
        else:
            registered.add(provider.name)
        return provider.integrate(
            root,
            dry_run=dry_run,
            remove=remove,
            registry_content=integration_registry_content(registered),
        )

    def refresh(self, root: Path, *, dry_run: bool) -> IntegrationResult:
        registered = set(load_integration_providers(root))
        discovered = {provider.name for provider in self.providers if provider.discover_legacy(root)}
        selected = registered | discovered
        selected_providers = tuple(
            provider for provider in self.providers if provider.name in selected
        )
        registry_content = integration_registry_content(selected) if discovered else None
        planned = tuple(
            provider.integrate(
                root,
                dry_run=True,
                remove=False,
                registry_content=registry_content if index == len(selected_providers) - 1 else None,
            )
            for index, provider in enumerate(selected_providers)
        )
        conflicts = tuple(conflict for result in planned for conflict in result.conflicts)
        if conflicts:
            return IntegrationResult((), tuple(sorted(set(conflicts))))
        if dry_run:
            return IntegrationResult(
                tuple(change for result in planned for change in result.changed), ()
            )
        changed: list[str] = []
        for index, provider in enumerate(selected_providers):
            result = provider.integrate(
                root,
                dry_run=False,
                remove=False,
                registry_content=registry_content if index == len(selected_providers) - 1 else None,
            )
            changed.extend(result.changed)
        return IntegrationResult(tuple(changed), ())

    def _provider(self, name: str) -> IntegrationProvider:
        for provider in self.providers:
            if provider.name == name:
                return provider
        raise ValueError(f"unknown integration provider {name}")


def _resource_templates() -> dict[str, str]:
    source = files("evospec").joinpath("resources", "opencode")
    paths = {
        PLUGIN: "plugins/evospec/index.ts",
        CONTINUATION: "plugins/evospec/continuation.mjs",
        SKILL: "skills/evospec/SKILL.md",
    }
    paths.update({f".opencode/commands/{name}": f"commands/{name}" for name in COMMANDS})
    return {target: source.joinpath(template).read_text(encoding="utf-8") for target, template in paths.items()}


def _resources() -> dict[str, str]:
    resources = _resource_templates()
    resources[PLUGIN] = resources[PLUGIN].replace(
        AGENT_COMMAND_PLACEHOLDER, json.dumps(_agent_command()),
    )
    resources[PLUGIN] = resources[PLUGIN].replace(
        CLI_COMMAND_PLACEHOLDER, json.dumps(_cli_command()),
    )
    return resources


def _resource_bytes() -> dict[str, bytes]:
    return {path: content.encode("utf-8") for path, content in _resources().items()}


def _agent_command() -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        return (str(Path(sys.executable).with_name(f"evospec-agent{suffix}")),)
    return (sys.executable, "-m", "evospec.agent_protocol")


def _cli_command() -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        return (str(Path(sys.executable).with_name(f"evospec{suffix}")),)
    return (sys.executable, "-m", "evospec.cli")


def _safe_file(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        if path.is_symlink():
            return False
        if not path.exists():
            return True
        return path.is_file() and path.stat().st_nlink <= 1
    except (OSError, ValueError):
        return False


def _resource_hashes(resources: dict[str, bytes]) -> dict[str, str]:
    return {path: sha256(content).hexdigest() for path, content in sorted(resources.items())}


def _manifest_content(resources: dict[str, bytes]) -> bytes:
    value = {
        "integration": "opencode",
        "resources": _resource_hashes(resources),
        "schema": 1,
    }
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_manifest(
    root: Path,
    safe_root: Path,
    resources: dict[str, bytes],
) -> tuple[str, dict[str, str] | None]:
    path = root / MANIFEST
    if not _safe_file(path, safe_root):
        return "CONFLICT", None
    if not path.exists():
        return "MISSING", None
    try:
        text = path.read_text(encoding="utf-8")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate manifest field")
                value[key] = item
            return value

        decoded: object = json.loads(text, object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return "UNREADABLE", None
    if not isinstance(decoded, dict) or set(decoded) != {"integration", "resources", "schema"}:
        return "UNREADABLE", None
    value = cast(dict[str, object], decoded)
    if value["integration"] != "opencode" or type(value["schema"]) is not int or value["schema"] != 1:
        return "UNREADABLE", None
    raw_resources = value["resources"]
    if not isinstance(raw_resources, dict):
        return "UNREADABLE", None
    allowed = set(resources)
    ownership: dict[str, str] = {}
    for target, digest in cast(dict[object, object], raw_resources).items():
        if not isinstance(target, str) or target not in allowed:
            return "UNREADABLE", None
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return "UNREADABLE", None
        ownership[target] = digest
    return "CURRENT", ownership


def _inspect_resource(
    path: Path,
    safe_root: Path,
    expected: bytes,
    recorded_hash: str | None,
) -> str:
    if not _safe_file(path, safe_root):
        return "CONFLICT"
    if not path.exists():
        return "MISSING"
    if recorded_hash is None:
        return "CONFLICT"
    try:
        current = path.read_bytes()
    except OSError:
        return "UNREADABLE"
    if sha256(current).hexdigest() != recorded_hash:
        return "CONFLICT"
    return "CURRENT" if current == expected else "STALE"


def _inspect_unowned_resource(path: Path, safe_root: Path, expected: bytes) -> str:
    """Allow exact packaged V2 bytes to be claimed without rewriting them."""
    if not _safe_file(path, safe_root):
        return "CONFLICT"
    if not path.exists():
        return "MISSING"
    try:
        current = path.read_bytes()
    except OSError:
        return "UNREADABLE"
    return "CURRENT" if current == expected else "CONFLICT"


def _legacy_v1_conflicts(root: Path, safe_root: Path) -> tuple[str, ...]:
    conflicts: list[str] = []
    for target in LEGACY_V1_ONLY_RESOURCES:
        path = root / target
        if not path.exists():
            continue
        if not _safe_file(path, safe_root):
            conflicts.append(target)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            conflicts.append(target)
            continue
        if any(marker in content for marker in LEGACY_V1_MARKERS):
            conflicts.append(target)
    return tuple(sorted(conflicts))


def _snapshot(path: Path) -> _Snapshot:
    if not path.exists():
        return _Snapshot(path, False, b"")
    return _Snapshot(path, True, path.read_bytes())


def _missing_parents(path: Path) -> tuple[Path, ...]:
    missing: list[Path] = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    return tuple(reversed(missing))


def _restore(snapshot: _Snapshot) -> None:
    if snapshot.exists:
        snapshot.path.write_bytes(snapshot.content)
    elif snapshot.path.exists():
        snapshot.path.unlink()


def _apply(operations: tuple[_Operation, ...]) -> None:
    snapshots = tuple(_snapshot(operation.path) for operation in operations)
    journal: list[_Snapshot] = []
    created_directories: list[Path] = []
    active: _Snapshot | None = None
    try:
        for operation, snapshot in zip(operations, snapshots, strict=True):
            active = snapshot
            if operation.content is None:
                operation.path.unlink()
            else:
                missing_parents = _missing_parents(operation.path.parent)
                created_directories.extend(missing_parents)
                operation.path.parent.mkdir(parents=True, exist_ok=True)
                operation.path.write_bytes(operation.content)
            journal.append(snapshot)
            active = None
    except OSError as original_error:
        rollback_error: OSError | None = None
        restorations = ([active] if active is not None else []) + list(reversed(journal))
        for snapshot in restorations:
            try:
                _restore(snapshot)
            except OSError as error:
                rollback_error = error
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError as error:
                rollback_error = error
        if rollback_error is not None:
            raise original_error from rollback_error
        raise


def integrate(
    root: Path,
    *,
    dry_run: bool = False,
    remove: bool = False,
    registry_content: str | None = None,
) -> IntegrationResult:
    """Apply a preflighted, manifest-owned OpenCode V2 resource update."""
    root = root.resolve()
    resources = _resource_bytes()
    safe_root = root
    manifest_path = root / MANIFEST
    manifest_state, ownership = _read_manifest(root, safe_root, resources)
    conflicts: list[str] = []
    operations: list[_Operation] = []
    legacy = _legacy_v1_conflicts(root, safe_root)
    if manifest_state in ("CONFLICT", "UNREADABLE"):
        conflicts.append(MANIFEST)
    conflicts.extend(legacy)

    if not conflicts and ownership is None:
        for target, expected in resources.items():
            path = root / target
            status = _inspect_unowned_resource(path, safe_root, expected)
            if status == "MISSING":
                if not remove:
                    operations.append(_Operation(path, expected))
            elif status != "CURRENT" or remove:
                conflicts.append(target)
        if not conflicts and not remove:
            operations.append(_Operation(manifest_path, _manifest_content(resources)))
    elif not conflicts and ownership is not None:
        if remove:
            for target, recorded_hash in ownership.items():
                path = root / target
                if not _safe_file(path, safe_root):
                    conflicts.append(target)
                    continue
                if not path.exists():
                    continue
                try:
                    current_hash = sha256(path.read_bytes()).hexdigest()
                except OSError:
                    conflicts.append(target)
                    continue
                if current_hash != recorded_hash:
                    conflicts.append(target)
                else:
                    operations.append(_Operation(path, None))
            for target in set(resources) - set(ownership):
                path = root / target
                if not _safe_file(path, safe_root) or path.exists():
                    conflicts.append(target)
            if not conflicts:
                operations.append(_Operation(manifest_path, None))
        else:
            for target, expected in resources.items():
                status = _inspect_resource(
                    root / target, safe_root, expected, ownership.get(target),
                )
                if status in ("CONFLICT", "UNREADABLE"):
                    conflicts.append(target)
                elif status != "CURRENT":
                    operations.append(_Operation(root / target, expected))
            if not conflicts:
                desired_manifest = _manifest_content(resources)
                try:
                    current_manifest = manifest_path.read_bytes()
                except OSError:
                    conflicts.append(MANIFEST)
                else:
                    if current_manifest != desired_manifest:
                        operations.append(_Operation(manifest_path, desired_manifest))

    if conflicts:
        return IntegrationResult((), tuple(sorted(set(conflicts))))

    if registry_content is not None:
        registry_path = integration_registry_path(root)
        if not _safe_file(registry_path, safe_root):
            return IntegrationResult((), (".evospec/integrations.json",))
        desired_registry = registry_content.encode("utf-8")
        try:
            current_registry = registry_path.read_bytes() if registry_path.exists() else None
        except OSError:
            return IntegrationResult((), (".evospec/integrations.json",))
        if current_registry != desired_registry:
            operations.append(_Operation(registry_path, desired_registry))
    changes = tuple(operation.path.relative_to(root).as_posix() for operation in operations)
    if dry_run:
        return IntegrationResult(changes, ())
    _apply(tuple(operations))
    return IntegrationResult(changes, ())
