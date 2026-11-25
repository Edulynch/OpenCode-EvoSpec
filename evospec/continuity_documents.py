"""Canonical Markdown codecs for composed Feature documents."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from .lifecycle import continuity_task_status
from .domain import ContinuityError

_ID = re.compile(r"^[FSTCD]-[0-9a-f]{32}$")
_DIRECTORY = re.compile(r"^[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATA_START = "<!-- evospec-data:start -->"
_DATA_END = "<!-- evospec-data:end -->"
_INDEX_START = "<!-- evospec-index:start -->"
_INDEX_END = "<!-- evospec-index:end -->"
_DOCUMENT_NAMES = {
    "spec": "spec",
    "plan": "plan",
    "tasks": "tasks",
    "decisions": "decisions",
}


def initial_documents(
    feature: dict[str, object], tasks: list[dict[str, object]], plan: str,
) -> dict[str, dict[str, object]]:
    return {
        "spec": feature,
        "plan": {"feature_id": feature["id"], "plan": plan},
        "tasks": {"feature_id": feature["id"], "tasks": tasks},
        "decisions": {"feature_id": feature["id"], "decisions": []},
    }


def feature_document_writes(
    root: Path, state: dict[str, object], feature_id: str,
    documents: dict[str, dict[str, object]], *, initial: bool = False,
) -> dict[str, tuple[bytes, str | None]]:
    folder = state["features"][feature_id]["directory"]
    feature = documents.get("spec")
    if feature is None:
        feature = read_feature(root, state, feature_id)
    writes: dict[str, tuple[bytes, str | None]] = {}
    for name, value in documents.items():
        relative = f"specs/{folder}/{name}.md"
        before = None if initial else state["documents"].get(relative)
        rendered = render_document(name, value, state, feature_id, feature)
        writes[relative] = (rendered.encode("utf-8"), before)
    return writes


def spec_write(
    root: Path, state: dict[str, object], feature_id: str,
    feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/spec.md"
    return {
        relative: (
            render_document("spec", feature, state, feature_id, feature).encode("utf-8"),
            state["documents"].get(relative),
        ),
    }


def plan_write(
    root: Path, state: dict[str, object], feature_id: str,
    feature: dict[str, object], plan: str,
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/plan.md"
    value = {"feature_id": feature_id, "plan": plan}
    return {
        relative: (
            render_document("plan", value, state, feature_id, feature).encode("utf-8"),
            state["documents"].get(relative),
        ),
    }


def tasks_write(
    root: Path, state: dict[str, object], feature_id: str,
    tasks: list[dict[str, object]], feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/tasks.md"
    value = {"feature_id": feature_id, "tasks": tasks}
    return {
        relative: (
            render_document("tasks", value, state, feature_id, feature).encode("utf-8"),
            state["documents"].get(relative),
        ),
    }


def decisions_write(
    root: Path, state: dict[str, object], feature_id: str,
    decisions: list[dict[str, object]], feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/decisions.md"
    value = {"feature_id": feature_id, "decisions": decisions}
    return {
        relative: (
            render_document("decisions", value, state, feature_id, feature).encode("utf-8"),
            state["documents"].get(relative),
        ),
    }


def render_document(
    name: str, value: dict[str, object], state: dict[str, object],
    feature_id: str, feature: dict[str, object],
) -> str:
    if name == "spec":
        entry = state["features"][feature_id]
        criteria = "\n".join(
            f"- `{item['id']}` — {item['description']} "
            f"(`{item['check_kind']}: {item['check_target']}`)"
            for item in value["criteria"]
        )
        increments = "\n".join(
            f"- `{item['id']}` — **{item['title']}**: {item['outcome']}"
            for item in value["increments"]
        ) or "- No Slice grouping; Tasks belong directly to this Feature."
        return (
            f"# {value['title']}\n\n"
            f"- Feature ID: `{feature_id}`\n- Kind: `{value['kind']}`\n"
            f"- Cycle: `{entry['cycle']}`\n- Lifecycle: `{entry['status']}`\n\n"
            f"## Outcome\n\n{value['outcome']}\n\n"
            f"## Acceptance criteria\n\n{criteria}\n\n"
            f"## Increments (optional Slices)\n\n{increments}\n\n"
            f"{_data_block(value)}"
        )
    if name == "plan":
        text = value["plan"] or "Plan is not recorded yet."
        return f"# Plan for `{feature_id}`\n\n{text}\n\n{_data_block(value)}"
    if name == "tasks":
        rows = [f"# Tasks for `{feature_id}`", ""]
        for task in value["tasks"]:
            status = continuity_task_status(state, feature, task)
            checkbox = "x" if status == "COMPLETED" else " "
            rows.extend((
                f"## - [{checkbox}] `{task['id']}` {task['title']}",
                "",
                f"- Projection: `{status}` (editing this checkbox does not record progress)",
                f"- Increment: `{task['increment_id'] or 'none'}`",
                f"- Criteria: {', '.join(f'`{item}`' for item in task['criteria_ids'])}",
                f"- Depends on: {', '.join(f'`{item}`' for item in task['depends_on']) or 'none'}",
                "",
                task["description"] or "No additional implementation notes.",
                "",
            ))
        rows.append(_data_block(value))
        return "\n".join(rows) + "\n"
    if name == "decisions":
        rows = [f"# Decisions for `{feature_id}`", ""]
        for decision in value["decisions"]:
            rows.extend((f"## `{decision['id']}` — {decision['question']}", "", decision["decision"], ""))
        rows.append(_data_block(value))
        return "\n".join(rows) + "\n"
    raise ContinuityError("INVALID_DOCUMENT", f"unsupported canonical document {name}")


def _data_block(value: dict[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{_DATA_START}\n```json\n{payload}\n```\n{_DATA_END}"


def read_document(
    root: Path, state: dict[str, object], feature_id: str, name: str,
) -> dict[str, object]:
    relative = f"specs/{state['features'][feature_id]['directory']}/{_DOCUMENT_NAMES[name]}.md"
    safe_document_path(root, relative)
    try:
        content = (root / Path(relative)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} cannot be read") from error
    start = content.rfind(_DATA_START)
    end = content.rfind(_DATA_END) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has no canonical data block")
    block = content[start + len(_DATA_START):end].strip()
    if not block.startswith("```json\n") or not block.endswith("\n```"):
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has a malformed canonical data block")
    try:
        value = json.loads(block[len("```json\n"):-len("\n```")])
    except (ValueError, RecursionError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} data cannot be parsed") from error
    if type(value) is not dict or value.get("feature_id", feature_id) != feature_id:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} data belongs to another Feature")
    return value


def read_feature(root: Path, state: dict[str, object], feature_id: str) -> dict[str, object]:
    value = read_document(root, state, feature_id, "spec")
    if set(value) != {"id", "kind", "title", "outcome", "criteria", "increments"} or value["id"] != feature_id:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature specification is malformed")
    _validate_feature(value)
    return value


def read_plan(root: Path, state: dict[str, object], feature_id: str) -> dict[str, object]:
    value = read_document(root, state, feature_id, "plan")
    if set(value) != {"feature_id", "plan"} or type(value["plan"]) is not str:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature plan is malformed")
    return value


def read_tasks(root: Path, state: dict[str, object], feature_id: str) -> list[dict[str, object]]:
    value = read_document(root, state, feature_id, "tasks")
    if set(value) != {"feature_id", "tasks"} or type(value["tasks"]) is not list:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature Task list is malformed")
    tasks = value["tasks"]
    ids = [item.get("id") for item in tasks if type(item) is dict]
    if len(ids) != len(tasks) or len(ids) != len(set(ids)):
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature Task IDs are not unique")
    try:
        _validate_tasks(tasks, read_feature(root, state, feature_id))
    except (KeyError, TypeError, ValueError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature Task definitions are malformed") from error
    return tasks


def read_decisions(root: Path, state: dict[str, object], feature_id: str) -> list[dict[str, object]]:
    value = read_document(root, state, feature_id, "decisions")
    if set(value) != {"feature_id", "decisions"} or type(value["decisions"]) is not list:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature decisions are malformed")
    decisions = value["decisions"]
    ids = [item.get("id") for item in decisions if type(item) is dict]
    if len(ids) != len(decisions) or len(ids) != len(set(ids)) or any(
        not isinstance(item, str) or _ID.fullmatch(item) is None or item[0] != "D"
        for item in ids
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature decision identities are malformed")
    if any(
        set(item) != {"id", "question", "decision"}
        or not isinstance(item["question"], str) or not item["question"]
        or not isinstance(item["decision"], str) or not item["decision"]
        for item in decisions
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature decision content is malformed")
    return decisions


def index_write(
    root: Path, state: dict[str, object],
    *, new_feature: tuple[str, str, str] | None = None,
) -> dict[str, tuple[bytes, str | None]]:
    relative = "specs/README.md"
    safe_document_path(root, relative)
    path = root / Path(relative)
    before = file_hash(path)
    if relative in state["documents"] and before != state["documents"][relative]:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md changed outside Core")
    try:
        original = "" if before is None else path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md cannot be read") from error
    if (_INDEX_START in original) != (_INDEX_END in original):
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has an incomplete EvoSpec index")
    if original.count(_INDEX_START) > 1 or original.count(_INDEX_END) > 1:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has duplicate EvoSpec index markers")
    entries: list[str] = []
    for feature_id, item in sorted(state["features"].items(), key=lambda pair: pair[1]["directory"]):
        folder = item["directory"]
        if new_feature is not None and feature_id == new_feature[0]:
            title = new_feature[2]
        else:
            title = read_feature(root, state, feature_id)["title"]
        entries.append(f"- [{title}]({folder}/spec.md) — `{feature_id}`")
    block = _INDEX_START + "\n## EvoSpec Feature specifications\n\n" + "\n".join(entries) + "\n" + _INDEX_END
    if _INDEX_START in original:
        start, end = original.index(_INDEX_START), original.index(_INDEX_END)
        original = original[:start] + block + original[end + len(_INDEX_END):]
    else:
        if original and not original.endswith("\n"):
            original += "\n"
        original += ("\n" if original else "") + block + "\n"
    return {relative: (original.encode("utf-8"), before)}


def new_directory(root: Path, title: str) -> tuple[str, int]:
    specs = root / "specs"
    ordinal = 1
    if specs.exists():
        safe_document_path(root, "specs")
        if not specs.is_dir():
            raise ContinuityError("UNSAFE_PATH", "specs must be a directory")
        for path in specs.iterdir():
            match = re.match(r"^([0-9]{3,})-", path.name)
            if match:
                ordinal = max(ordinal, int(match.group(1)) + 1)
    words = re.findall(r"[a-z0-9]+", title.casefold())
    slug = "-".join(words[:8])[:56].rstrip("-") or "feature"
    return f"{ordinal:03d}-{slug}", ordinal


def safe_document_path(root: Path, relative: str) -> None:
    if (
        not isinstance(relative, str) or not relative
        or Path(relative).is_absolute() or ".." in Path(relative).parts
        or "\\" in relative
    ):
        raise ContinuityError("UNSAFE_PATH", "continuity path is not repository-relative")
    current = root
    try:
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ContinuityError("UNSAFE_PATH", "continuity refuses symbolic-link targets")
        if not (root / Path(relative)).resolve().is_relative_to(root):
            raise ContinuityError("UNSAFE_PATH", "continuity path escapes the repository")
    except OSError as error:
        raise ContinuityError("UNSAFE_PATH", "continuity path cannot be resolved") from error


def file_hash(path: Path) -> str | None:
    if not path.exists():
        if path.is_symlink():
            raise ContinuityError("UNSAFE_PATH", f"{path.name} is a symbolic link")
        return None
    try:
        if not path.is_file() or path.is_symlink():
            raise ContinuityError("UNSAFE_PATH", f"{path.name} is not a regular file")
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ContinuityError("DOCUMENT_UNREADABLE", f"{path.name} cannot be read") from error


def _validate_feature(value: dict[str, object]) -> None:
    if (
        not isinstance(value["title"], str) or not value["title"].strip()
        or not isinstance(value["outcome"], str) or not value["outcome"].strip()
        or value["kind"] not in ("FEATURE", "CORRECTION")
        or type(value["criteria"]) is not list or not value["criteria"]
        or type(value["increments"]) is not list
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature content is malformed")
    for item in value["criteria"]:
        if (
            type(item) is not dict or set(item) != {"id", "description", "check_kind", "check_target"}
            or not isinstance(item["id"], str) or _ID.fullmatch(item["id"]) is None or item["id"][0] != "C"
            or not isinstance(item["description"], str) or not item["description"].strip()
            or item["check_kind"] not in ("python-unittest", "node-test")
            or not isinstance(item["check_target"], str)
        ):
            raise ContinuityError("DOCUMENT_DIVERGED", "Feature criterion is malformed")
    for item in value["increments"]:
        if (
            type(item) is not dict or set(item) != {"id", "title", "outcome"}
            or not isinstance(item["id"], str) or _ID.fullmatch(item["id"]) is None or item["id"][0] != "S"
            or not isinstance(item["title"], str) or not item["title"].strip()
            or not isinstance(item["outcome"], str) or not item["outcome"].strip()
        ):
            raise ContinuityError("DOCUMENT_DIVERGED", "Feature increment is malformed")


def _validate_tasks(tasks: list[object], feature: dict[str, object]) -> None:
    criteria = {item["id"] for item in feature["criteria"]}
    increments = {item["id"] for item in feature["increments"]}
    ids: set[str] = set()
    for task in tasks:
        if (
            type(task) is not dict
            or set(task) != {"id", "title", "description", "increment_id", "criteria_ids", "depends_on"}
            or not isinstance(task["id"], str) or _ID.fullmatch(task["id"]) is None or task["id"][0] != "T"
            or task["id"] in ids
            or not isinstance(task["title"], str) or not task["title"].strip()
            or not isinstance(task["description"], str)
            or task["increment_id"] is not None and task["increment_id"] not in increments
            or type(task["criteria_ids"]) is not list or not task["criteria_ids"]
            or any(item not in criteria for item in task["criteria_ids"])
            or type(task["depends_on"]) is not list
            or any(item not in ids for item in task["depends_on"])
        ):
            raise ValueError("invalid Task record")
        ids.add(task["id"])
