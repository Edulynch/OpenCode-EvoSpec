"""Deterministic, read-only repository discovery for workflow policy."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re

from .domain import DiscoveryEvidence, RepositoryPath


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    """Bounded project-native capability evidence with stable provenance."""

    name: str
    provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityDiscovery:
    """A bounded project capability scan with an explicit safe limit result."""

    candidates: tuple[CapabilityCandidate, ...]
    limited: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityRecovery:
    """Separate bounded evidence probe used only after native discovery is incomplete."""

    candidates: tuple[CapabilityCandidate, ...]
    limited: bool = False


@dataclass(frozen=True, slots=True)
class ExternalSDDDiscovery:
    """Bounded structural evidence for recognized external SDD layouts."""

    evidence: tuple[tuple[str, tuple[str, ...]], ...]
    limited: bool = False


_IGNORED_PARTS = frozenset(
    {
        ".evospec",
        ".git",
        ".pytest_cache",
        "__pycache__",
        ".venv",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)
_TEXT_SUFFIXES = frozenset((".md", ".py", ".rst", ".toml", ".txt", ".yaml", ".yml"))
_NON_CAPABILITY_WORDS = frozenset(
    {
        "add",
        "an",
        "at",
        "alternate",
        "behavior",
        "by",
        "capability",
        "change",
        "create",
        "disable",
        "enable",
        "experiment",
        "feature",
        "fix",
        "for",
        "foundation",
        "from",
        "implement",
        "initial",
        "into",
        "make",
        "migration",
        "modify",
        "of",
        "on",
        "patch",
        "remove",
        "rejected",
        "semantic",
        "support",
        "the",
        "tune",
        "update",
        "use",
        "uses",
        "with",
    }
)
_WORDS = re.compile(r"[a-z][a-z0-9-]{2,}")
MAX_ENTRIES = 512
MAX_ELIGIBLE_FILES = 128
MAX_BYTES = 65_536
MAX_RECOVERY_ENTRIES = 2_048
MAX_RECOVERY_ELIGIBLE_FILES = 256
MAX_EXTERNAL_SDD_COMMAND_ENTRIES = 128
_FOREIGN_SDD_PARTS = frozenset((".specify", ".openspec-store", "openspec"))


class DiscoveryPolicy:
    """Produce bounded, attributable evidence without executing repository content."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root

    def discover(self) -> tuple[DiscoveryEvidence, ...]:
        """Return relevant documentation evidence in stable repository-relative order."""
        evidence: list[DiscoveryEvidence] = []
        paths, _ = self._paths(_IGNORED_PARTS)
        for path in paths:
            if not path.is_file():
                continue
            match path.name:
                case "AGENTS.md":
                    kind, strength = "INSTRUCTION", "STRONG"
                case "README.md" | "CONSTITUTION.md":
                    kind, strength = "DOCUMENT", "STRONG"
                case _:
                    continue
            try:
                content = self._read_capped(path)
            except OSError:
                continue
            if content is None:
                continue
            evidence.append(
                DiscoveryEvidence(
                    RepositoryPath(self._relative(path).as_posix()),
                    kind,
                    strength,
                    sha256(content).hexdigest(),
                    "repository discovery",
                )
            )
        return tuple(evidence)

    def capability_candidates(self, intent: str) -> CapabilityDiscovery:
        """Find bounded, repeated native evidence for meaningful intent terms."""
        terms = tuple(
            dict.fromkeys(word for word in _WORDS.findall(intent.casefold()) if word not in _NON_CAPABILITY_WORDS)
        )
        phrases = tuple(
            " ".join(terms[start:end])
            for start in range(len(terms) - 1)
            for end in range(start + 2, len(terms) + 1)
        )
        if not phrases:
            return CapabilityDiscovery(())
        evidence_by_phrase: dict[str, list[str]] = {phrase: [] for phrase in phrases}
        paths, entries_limited = self._paths(_IGNORED_PARTS)
        if entries_limited:
            return CapabilityDiscovery((), True)
        eligible_files = 0
        for path in paths:
            if (
                not path.is_file()
                or path.suffix.casefold() not in _TEXT_SUFFIXES
            ):
                continue
            if eligible_files == MAX_ELIGIBLE_FILES:
                return CapabilityDiscovery((), True)
            eligible_files += 1
            try:
                content = self._read_capped(path)
            except OSError:
                continue
            if content is None:
                continue
            relative = self._relative(path)
            searchables = (
                relative.as_posix().casefold(),
                content.decode("utf-8", errors="ignore").casefold(),
            )
            fingerprint = sha256(content).hexdigest()
            source = self._source_kind(path)
            for phrase in phrases:
                if any(self._contains_complete_phrase(searchable, phrase) for searchable in searchables):
                    evidence_by_phrase[phrase].append(
                        f"project-native:{source}:{phrase}:{relative}:{fingerprint}"
                    )
        candidates = tuple(
            CapabilityCandidate(phrase, tuple(evidence))
            for phrase, evidence in evidence_by_phrase.items()
            if len(evidence) >= 2
        )
        return CapabilityDiscovery(candidates)

    def recovery_candidates(self, intent: str) -> CapabilityRecovery:
        """Probe additional native evidence without changing the global discovery bounds."""
        terms = tuple(
            dict.fromkeys(word for word in _WORDS.findall(intent.casefold()) if word not in _NON_CAPABILITY_WORDS)
        )
        phrases = tuple(
            " ".join(terms[start:end])
            for start in range(len(terms) - 1)
            for end in range(start + 2, len(terms) + 1)
        )
        if not phrases:
            return CapabilityRecovery(())
        evidence_by_phrase: dict[str, list[str]] = {phrase: [] for phrase in phrases}
        paths, entries_limited = self._paths(
            _IGNORED_PARTS | _FOREIGN_SDD_PARTS,
            entry_limit=MAX_RECOVERY_ENTRIES,
        )
        if entries_limited:
            return CapabilityRecovery((), True)
        eligible_files = 0
        for path in paths:
            if self._is_foreign_speckit_command(path):
                continue
            if not path.is_file() or path.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            if eligible_files == MAX_RECOVERY_ELIGIBLE_FILES:
                return CapabilityRecovery((), True)
            eligible_files += 1
            try:
                content = self._read_capped(path)
            except OSError:
                return CapabilityRecovery((), True)
            if content is None:
                return CapabilityRecovery((), True)
            relative = self._relative(path)
            searchables = (
                relative.as_posix().casefold(),
                content.decode("utf-8", errors="ignore").casefold(),
            )
            for phrase in phrases:
                if any(self._contains_complete_phrase(searchable, phrase) for searchable in searchables):
                    reference = relative.as_posix()
                    if reference not in evidence_by_phrase[phrase]:
                        evidence_by_phrase[phrase].append(reference)
        return CapabilityRecovery(
            tuple(
                CapabilityCandidate(phrase, tuple(references))
                for phrase, references in evidence_by_phrase.items()
                if len(references) >= 2
            )
        )

    def external_sdd_evidence(self) -> ExternalSDDDiscovery:
        """Project recognized external SDD paths without reading their contents."""
        relative_paths, limited = self._external_sdd_paths()
        spec_kit = sorted(
            path
            for path in relative_paths
            if path == ".specify/"
            or path.startswith(".opencode/commands/speckit.") and path.endswith(".md")
        )
        openspec = [
            path
            for path in (
                ".openspec-store/store.yaml",
                "openspec/changes/",
                "openspec/config.yaml",
                "openspec/specs/",
            )
            if path in relative_paths
        ]
        evidence = tuple(
            (framework, tuple(facts))
            for framework, facts in (("OPENSPEC", openspec), ("SPEC_KIT", spec_kit))
            if facts
        )
        return ExternalSDDDiscovery(evidence, limited)

    def _contains_complete_phrase(self, text: str, phrase: str) -> bool:
        """Match a whole phrase that is not embedded in a longer meaningful capability."""
        pattern = r"\b" + r"[^\S\r\n]+".join(re.escape(term) for term in phrase.split()) + r"\b"
        for match in re.finditer(pattern, text):
            before = re.search(r"([a-z][a-z0-9-]{2,})[^\w\r\n]*$", text[: match.start()])
            after = re.match(r"^[^\w\r\n]*([a-z][a-z0-9-]{2,})", text[match.end() :])
            if (
                (before is None or before.group(1) in _NON_CAPABILITY_WORDS)
                and (after is None or after.group(1) in _NON_CAPABILITY_WORDS)
            ):
                return True
        return False

    @classmethod
    def matches_capability(cls, title: str, intent: str) -> bool:
        """Apply the established-capability term rule without repository traversal."""
        intent_terms = frozenset(
            word for word in _WORDS.findall(intent.casefold()) if word not in _NON_CAPABILITY_WORDS
        )
        title_terms = frozenset(
            word for word in _WORDS.findall(title.casefold()) if word not in _NON_CAPABILITY_WORDS
        )
        return bool(title_terms) and title_terms <= intent_terms

    def _external_sdd_paths(self) -> tuple[frozenset[str], bool]:
        """Inspect only recognized foreign layout paths, without a capability scan."""
        root = self._root.resolve()
        paths: set[str] = set()
        limited = False
        for relative in (
            ".openspec-store/store.yaml",
            "openspec/changes",
            "openspec/config.yaml",
            "openspec/specs",
            ".specify",
        ):
            path = root / relative
            try:
                if path.is_symlink() or not self._inside_root(path, root) or not path.exists():
                    continue
                paths.add(relative + ("/" if path.is_dir() else ""))
            except OSError:
                limited = True
        command_root = root / ".opencode" / "commands"
        try:
            if command_root.is_symlink() or not self._inside_root(command_root, root):
                return frozenset(paths), limited
            command_paths: set[str] = set()
            entries = 0
            with os.scandir(command_root) as scanned:
                for entry in scanned:
                    entries += 1
                    if entries > MAX_EXTERNAL_SDD_COMMAND_ENTRIES:
                        return frozenset(paths), True
                    path = Path(entry.path)
                    if path.is_symlink() or not self._inside_root(path, root):
                        continue
                    if path.is_file() and path.name.startswith("speckit.") and path.name.endswith(".md"):
                        command_paths.add(f".opencode/commands/{path.name}")
            paths.update(command_paths)
        except FileNotFoundError:
            pass
        except OSError:
            limited = True
        return frozenset(paths), limited

    def _paths(
        self, ignored_parts: frozenset[str], *, entry_limit: int = MAX_ENTRIES
    ) -> tuple[tuple[Path, ...], bool]:
        """Enumerate only bounded, contained non-symlink repository paths."""
        root = self._root.resolve()
        directories = [root]
        paths: list[Path] = []
        entries = 0
        limited = False
        while directories:
            directory = directories.pop(0)
            try:
                with os.scandir(directory) as scanned:
                    children = []
                    for entry in scanned:
                        entries += 1
                        if entries > entry_limit:
                            return tuple(sorted(paths, key=lambda item: item.as_posix())), True
                        children.append(Path(entry.path))
            except OSError:
                limited = True
                continue
            for child in sorted(children, key=lambda item: item.as_posix()):
                if child.is_symlink() or not self._inside_root(child, root):
                    continue
                relative = self._relative(child)
                if any(part in ignored_parts for part in relative.parts):
                    continue
                if child.is_dir():
                    paths.append(child)
                    directories.append(child)
                else:
                    paths.append(child)
            directories.sort(key=lambda item: item.as_posix())
        return tuple(sorted(paths, key=lambda item: item.as_posix())), limited

    def _inside_root(self, path: Path, root: Path) -> bool:
        """Accept only paths that resolve beneath the configured repository root."""
        try:
            path.resolve().relative_to(root)
        except ValueError:
            return False
        return True

    def _relative(self, path: Path) -> Path:
        """Return the canonical contained path relative to the repository root."""
        return path.resolve().relative_to(self._root.resolve())

    def _is_foreign_speckit_command(self, path: Path) -> bool:
        """Keep recognized foreign Spec-Kit commands out of native recovery evidence."""
        relative = self._relative(path)
        return (
            relative.parts[:2] == (".opencode", "commands")
            and relative.name.startswith("speckit.")
            and relative.name.endswith(".md")
        )

    def _read_capped(self, path: Path) -> bytes | None:
        """Read no more than the configured content limit plus one boundary byte."""
        with path.open("rb") as file:
            content = file.read(MAX_BYTES + 1)
        return None if len(content) > MAX_BYTES else content

    def _source_kind(self, path: Path) -> str:
        """Classify evidence source without inspecting or executing its contents."""
        if "tests" in path.parts:
            return "test"
        if "docs" in path.parts or path.suffix.casefold() in (".md", ".rst"):
            return "spec"
        return "source"
