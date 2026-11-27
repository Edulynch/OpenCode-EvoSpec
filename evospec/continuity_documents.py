"""Human-authored Markdown codecs for composed Feature documents."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from .domain import (
    ContinuityCheck,
    ContinuityError,
    ContinuityCriterionDocument,
    ContinuityDecisionDocument,
    ContinuityFeatureDocument,
    ContinuityInputScope,
    ContinuityIncrementDocument,
    ContinuityPlanDocument,
    ContinuityStateData,
    ContinuityTaskDocument,
)
from .lifecycle import (
    ContinuityFeatureView,
    ContinuityStateView,
    ContinuityTaskView,
    continuity_task_status,
)

_ID = re.compile(r"^[FSTCD]-[0-9a-f]{32}$")
_ALIAS = re.compile(r"^[CSTD]-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_DIRECTORY = re.compile(r"^[0-9]{3,}-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATA_START = "<!-- evospec-data:start -->"
_DATA_END = "<!-- evospec-data:end -->"
_INDEX_START = "<!-- evospec-index:start -->"
_INDEX_END = "<!-- evospec-index:end -->"
_GENERATED_START = "<!-- evospec-generated:start -->"
_GENERATED_END = "<!-- evospec-generated:end -->"
_NOTES_START = "<!-- evospec-notes:start -->"
_NOTES_END = "<!-- evospec-notes:end -->"
_FEATURE = re.compile(r"^<!-- evospec-feature: (F-[0-9a-f]{32}) -->\r?$", re.M)
_DOCUMENT_NAMES = {"spec": "spec", "plan": "plan", "tasks": "tasks", "decisions": "decisions"}


class _ContinuityCheckView(TypedDict):
    criterion_id: str
    criterion_digest: str
    exit_code: int
    check_kind: str
    check_target: str
    output_sha256: str
    task_id: str | None
    reported_by_session_id: str
    recorded_revision: int
    observed_input_scope: NotRequired[dict[str, str]]


def _continuity_check_view(check: ContinuityCheck) -> _ContinuityCheckView:
    view: _ContinuityCheckView = {
        "criterion_id": check["criterion_id"],
        "criterion_digest": check["criterion_digest"],
        "exit_code": check["exit_code"],
        "check_kind": check["check_kind"],
        "check_target": check["check_target"],
        "output_sha256": check["output_sha256"],
        "task_id": check["task_id"],
        "reported_by_session_id": check["reported_by_session_id"],
        "recorded_revision": check["recorded_revision"],
    }
    observed = check.get("observed_input_scope")
    if observed is not None:
        view["observed_input_scope"] = {
            "policy_id": observed["policy_id"],
            "policy_digest": observed["policy_digest"],
            "fingerprint": observed["fingerprint"],
        }
    return view


def initial_documents(
    feature: dict[str, object], tasks: list[dict[str, object]], plan: str,
) -> dict[str, dict[str, object]]:
    documents = {
        "spec": feature,
        "tasks": {"feature_id": feature["id"], "tasks": tasks},
    }
    if plan.strip():
        documents["plan"] = {"feature_id": feature["id"], "plan": plan}
    return documents


def feature_document_writes(
    root: Path, state: ContinuityStateData, feature_id: str,
    documents: dict[str, dict[str, object]], *, initial: bool = False,
) -> dict[str, tuple[bytes, str | None]]:
    feature = documents.get("spec")
    if feature is None:
        feature = cast(dict[str, object], cast(object, read_feature(root, state, feature_id)))
    writes: dict[str, tuple[bytes, str | None]] = {}
    for name, value in documents.items():
        relative = f"specs/{state['features'][feature_id]['directory']}/{name}.md"
        before = None if initial else state["documents"].get(relative)
        rendered = render_document(name, value, state, feature_id, feature)
        original = _read_optional(root, relative)
        if original is not None:
            rendered = _preserve_notes(original, rendered)
        writes[relative] = (rendered.encode("utf-8"), before)
    return writes


def spec_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/spec.md"
    rendered = render_document("spec", feature, state, feature_id, feature)
    original = _read_optional(root, relative)
    if original is not None:
        rendered = _preserve_notes(original, rendered)
        rendered = _preserve_unchanged_sections(original, rendered, "spec")
    return {relative: (rendered.encode("utf-8"), state["documents"].get(relative))}


def plan_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    feature: dict[str, object], plan: str,
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/plan.md"
    value: dict[str, object] = {"feature_id": feature_id, "plan": plan}
    rendered = render_document("plan", value, state, feature_id, feature)
    original = _read_optional(root, relative)
    if original is not None:
        rendered = _preserve_notes(original, rendered)
    return {relative: (rendered.encode("utf-8"), state["documents"].get(relative))}


def tasks_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    tasks: list[ContinuityTaskDocument], feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/tasks.md"
    value: dict[str, object] = {"feature_id": feature_id, "tasks": tasks}
    original = _read_optional(root, relative)
    if original is None:
        rendered = render_document("tasks", value, state, feature_id, feature)
    else:
        try:
            current = read_tasks(root, state, feature_id)
        except ContinuityError:
            current = []
        new_ids = [item["id"] for item in current]
        requested_ids = [item["id"] for item in tasks]
        if requested_ids[:len(new_ids)] == new_ids:
            additions = tasks[len(new_ids):]
            rendered = _append_task_definitions(original, additions)
        elif current == tasks:
            rendered = original
        else:
            rendered = render_document("tasks", value, state, feature_id, feature)
            rendered = _preserve_notes(original, rendered)
        generated = _generated_checklist(state, feature_id, feature, tasks)
        rendered = _replace_generated(rendered, generated)
    return {relative: (rendered.encode("utf-8"), state["documents"].get(relative))}


def decisions_write(
    root: Path, state: ContinuityStateData, feature_id: str,
    decisions: list[dict[str, object]], feature: dict[str, object],
) -> dict[str, tuple[bytes, str | None]]:
    relative = f"specs/{state['features'][feature_id]['directory']}/decisions.md"
    value: dict[str, object] = {"feature_id": feature_id, "decisions": decisions}
    original = _read_optional(root, relative)
    if original is None:
        rendered = render_document("decisions", value, state, feature_id, feature)
    else:
        try:
            current = read_decisions(root, state, feature_id)
        except ContinuityError:
            current = []
        current_ids = [item["id"] for item in current]
        requested = cast(list[dict[str, object]], cast(object, decisions))
        if [item["id"] for item in requested[:len(current_ids)]] == current_ids:
            rendered = _append_decisions(original, requested[len(current_ids):])
        elif current == cast(list[ContinuityDecisionDocument], cast(object, decisions)):
            rendered = original
        else:
            rendered = _preserve_notes(
                original, render_document("decisions", value, state, feature_id, feature),
            )
    return {relative: (rendered.encode("utf-8"), state["documents"].get(relative))}


def render_document(
    name: str, value: dict[str, object], state: ContinuityStateData,
    feature_id: str, feature: dict[str, object],
) -> str:
    identity = f"<!-- evospec-feature: {feature_id} -->"
    notes = f"{_NOTES_START}\n{_NOTES_END}\n"
    if name == "spec":
        criteria = "\n".join(
            f"- `{item['id']}` — {item['description']}\n"
            f"  - Check: `{item['check_kind']}`\n"
            f"  - Target: `{item['check_target']}`"
            for item in cast(list[ContinuityCriterionDocument], value["criteria"])
        )
        increments = "\n".join(
            f"- `{item['id']}` — **{item['title']}**\n"
            f"  Outcome: {item['outcome']}"
            for item in cast(list[ContinuityIncrementDocument], value["increments"])
        ) or "_No increments; Tasks belong directly to this Feature._"
        return (
            f"# {value['title']}\n\n{identity}\n\n"
            f"## Kind\n\n- Kind: `{value['kind']}`\n\n"
            f"## Outcome\n\n{value['outcome']}\n\n"
            f"## Acceptance criteria\n\n{criteria}\n\n"
            f"## Increments (optional Slices)\n\n{increments}\n\n{notes}"
        )
    if name == "plan":
        text = cast(str, value["plan"])
        return f"# Plan for `{feature_id}`\n\n{identity}\n\n## Plan\n\n{text}\n\n{notes}"
    if name == "tasks":
        tasks = cast(list[ContinuityTaskDocument], cast(object, value["tasks"]))
        rows = [f"# Tasks for `{feature_id}`", "", identity, "", "## Tasks", ""]
        rows.extend(_task_definition(task) for task in tasks)
        rows.extend(("", _generated_checklist(state, feature_id, feature, tasks), "", notes))
        return "\n".join(rows)
    if name == "decisions":
        rows = [f"# Decisions for `{feature_id}`", "", identity, "", "## Decisions", ""]
        for decision in cast(list[ContinuityDecisionDocument], cast(object, value["decisions"])):
            rows.extend((f"### `{decision['id']}` — {decision['question']}", "", decision["decision"], ""))
        rows.append(notes)
        return "\n".join(rows) + "\n"
    raise ContinuityError("INVALID_DOCUMENT", f"unsupported canonical document {name}")


def read_document(
    root: Path, state: ContinuityStateData, feature_id: str, name: str,
) -> dict[str, object]:
    relative = _relative(state, feature_id, name)
    safe_document_path(root, relative)
    content = _read_optional(root, relative)
    if content is None:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} is missing")
    return _parse_markdown_or_legacy(content, relative, feature_id, name)


def _parse_markdown_or_legacy(
    content: str, relative: str, feature_id: str, name: str,
) -> dict[str, object]:
    if _DATA_START in content or _DATA_END in content:
        return _read_legacy_document(content, relative, feature_id)
    return _parse_markdown_document(content, relative, feature_id, name)


def read_feature(
    root: Path, state: ContinuityStateData, feature_id: str,
) -> ContinuityFeatureDocument:
    value = read_document(root, state, feature_id, "spec")
    if set(value) != {"id", "kind", "title", "outcome", "criteria", "increments"} or value["id"] != feature_id:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature specification is malformed")
    _validate_feature(value)
    return cast(ContinuityFeatureDocument, cast(object, value))


def read_plan(
    root: Path, state: ContinuityStateData, feature_id: str,
) -> ContinuityPlanDocument:
    relative = _relative(state, feature_id, "plan")
    if _read_optional(root, relative) is None:
        return {"feature_id": feature_id, "plan": ""}
    value = read_document(root, state, feature_id, "plan")
    if set(value) != {"feature_id", "plan"} or type(value["plan"]) is not str:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature plan is malformed")
    return cast(ContinuityPlanDocument, cast(object, value))


def read_tasks(
    root: Path, state: ContinuityStateData, feature_id: str,
) -> list[ContinuityTaskDocument]:
    value = read_document(root, state, feature_id, "tasks")
    if set(value) != {"feature_id", "tasks"} or type(value["tasks"]) is not list:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature Task list is malformed")
    tasks = cast(list[object], value["tasks"])
    try:
        _validate_tasks(tasks, read_feature(root, state, feature_id))
    except (KeyError, TypeError, ValueError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature Task definitions or references are malformed") from error
    return cast(list[ContinuityTaskDocument], cast(object, tasks))


def read_decisions(
    root: Path, state: ContinuityStateData, feature_id: str,
) -> list[ContinuityDecisionDocument]:
    relative = _relative(state, feature_id, "decisions")
    if _read_optional(root, relative) is None:
        return []
    value = read_document(root, state, feature_id, "decisions")
    if set(value) != {"feature_id", "decisions"} or type(value["decisions"]) is not list:
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature decisions are malformed")
    decisions = cast(list[object], value["decisions"])
    _validate_decision_list(decisions, _relative(state, feature_id, "decisions"))
    records = cast(list[ContinuityDecisionDocument], cast(object, decisions))
    return records


def _validate_decision_list(decisions: list[object], relative: str) -> None:
    ids = [
        cast(dict[str, object], item).get("id")
        for item in decisions if type(item) is dict
    ]
    if len(ids) != len(decisions) or len(ids) != len(set(ids)) or any(
        not _valid_identity(item, "D") for item in ids
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has malformed or duplicate decision IDs")
    if any(
        set(cast(dict[str, object], item)) != {"id", "question", "decision"}
        or not isinstance(cast(dict[str, object], item).get("question"), str)
        or not cast(str, cast(dict[str, object], item).get("question")).strip()
        or not isinstance(cast(dict[str, object], item).get("decision"), str)
        or not cast(str, cast(dict[str, object], item).get("decision")).strip()
        for item in decisions
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} decision content is malformed")


def parse_all(
    root: Path, state: ContinuityStateData, feature_id: str,
    input_snapshot: ContinuityInputScope | None = None,
) -> dict[str, object]:
    """Read semantic Markdown, its digest and generated-region discrepancies."""
    relative_paths = {
        name: _relative(state, feature_id, name)
        for name in ("spec", "plan", "tasks", "decisions")
    }
    texts = {
        name: _read_optional(root, relative)
        for name, relative in relative_paths.items()
    }
    if texts["spec"] is None or texts["tasks"] is None:
        missing = "spec" if texts["spec"] is None else "tasks"
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative_paths[missing]} is missing")
    parsed: dict[str, dict[str, object] | None] = {}
    for name, text in texts.items():
        if text is None:
            parsed[name] = None
        else:
            parsed[name] = _parse_markdown_or_legacy(
                text, relative_paths[name], feature_id, name,
            )
    feature = cast(dict[str, object], parsed["spec"])
    if set(feature) != {"id", "kind", "title", "outcome", "criteria", "increments"}:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative_paths['spec']} has unsupported fields")
    _validate_feature(feature)
    tasks_value = cast(dict[str, object], parsed["tasks"])
    if set(tasks_value) != {"feature_id", "tasks"} or tasks_value["feature_id"] != feature_id:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative_paths['tasks']} has unsupported fields")
    tasks = cast(list[object], tasks_value.get("tasks", []))
    try:
        _validate_tasks(tasks, cast(ContinuityFeatureDocument, cast(object, feature)))
    except (KeyError, TypeError, ValueError) as error:
        raise ContinuityError(
            "DOCUMENT_DIVERGED",
            f"{relative_paths['tasks']} has dangling, wrong-kind, duplicate, or cyclic references",
        ) from error
    task_documents = cast(list[ContinuityTaskDocument], cast(object, tasks))
    plan_value = parsed["plan"] or {"feature_id": feature_id, "plan": ""}
    decisions_value = parsed["decisions"] or {"feature_id": feature_id, "decisions": []}
    if (
        set(plan_value) != {"feature_id", "plan"}
        or plan_value["feature_id"] != feature_id or not isinstance(plan_value["plan"], str)
        or set(decisions_value) != {"feature_id", "decisions"}
        or decisions_value["feature_id"] != feature_id
        or type(decisions_value["decisions"]) is not list
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "plan or decisions document shape is invalid")
    _validate_decision_list(cast(list[object], decisions_value.get("decisions", [])), relative_paths["decisions"])
    values: dict[str, object] = {
        "spec": feature,
        "tasks": cast(list[dict[str, object]], cast(object, task_documents)),
        "plan": plan_value,
        "decisions": cast(list[dict[str, object]], decisions_value["decisions"]),
    }
    semantic_values = {
        "spec": feature,
        "plan": plan_value,
        "tasks": {"feature_id": feature_id, "tasks": values["tasks"]},
        "decisions": {
            "feature_id": feature_id, "decisions": values["decisions"],
        },
    }
    bytes_digests: dict[str, str | None] = {}
    semantic_digests: dict[str, str | None] = {}
    generated: dict[str, bool] = {}
    for name, relative in relative_paths.items():
        text = texts[name]
        if text is None:
            bytes_digests[relative] = None
            semantic_digests[relative] = None
            continue
        encoded = text.encode("utf-8")
        bytes_digests[relative] = sha256(encoded).hexdigest()
        semantic_digests[relative] = semantic_digest(
            name, cast(dict[str, object], semantic_values[name]),
        )
        if name == "tasks" and _DATA_START not in text and _DATA_END not in text:
            checklist_state = cast(
                ContinuityStateData,
                cast(object, {**state, "input_snapshot": input_snapshot}),
            )
            expected = _generated_checklist(
                checklist_state, feature_id, cast(dict[str, object], values["spec"]),
                task_documents,
            )
            generated[relative] = _generated_content(text) == expected
    values["bytes_digests"] = bytes_digests
    values["semantic_digests"] = semantic_digests
    values["generated_current"] = generated
    return values


def semantic_digest(name: str, value: dict[str, object]) -> str:
    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256((name + "\0" + normalized).encode("utf-8")).hexdigest()


def semantic_digest_for_content(content: bytes, relative: str, name: str, feature_id: str) -> str:
    return semantic_digest(
        name, parse_document_content(content, relative, name, feature_id),
    )


def parse_document_content(content: bytes, relative: str, name: str, feature_id: str) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContinuityError("INVALID_DOCUMENT", f"{relative} is not UTF-8") from error
    return (
        _read_legacy_document(text, relative, feature_id)
        if _DATA_START in text or _DATA_END in text
        else _parse_markdown_document(text, relative, feature_id, name)
    )


def replace_aliases(content: bytes, aliases: dict[str, str]) -> bytes:
    if not aliases:
        return content
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContinuityError("INVALID_DOCUMENT", "edited Markdown is not UTF-8") from error
    for alias, identity in aliases.items():
        text = text.replace(f"`{alias}`", f"`{identity}`")
    return text.encode("utf-8")


def refresh_generated_checklist(
    content: bytes, state: ContinuityStateData, feature_id: str,
    feature: dict[str, object], tasks: list[ContinuityTaskDocument],
) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContinuityError("INVALID_DOCUMENT", "tasks.md is not UTF-8") from error
    updated = _replace_generated(
        text, _generated_checklist(state, feature_id, feature, tasks),
    )
    return updated.encode("utf-8")


def index_write(
    root: Path, state: ContinuityStateData,
    *, new_feature: tuple[str, str, str] | None = None,
    allow_external: bool = False,
) -> dict[str, tuple[bytes, str | None]]:
    relative = "specs/README.md"
    safe_document_path(root, relative)
    path = root / Path(relative)
    before = file_hash(path)
    if (
        not allow_external
        and relative in state["documents"] and before != state["documents"][relative]
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md changed outside Core")
    try:
        original = "" if before is None else path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md cannot be read") from error
    if (_INDEX_START in original) != (_INDEX_END in original):
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has an incomplete EvoSpec index")
    if original.count(_INDEX_START) > 1 or original.count(_INDEX_END) > 1:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has duplicate EvoSpec index markers")
    entries: list[str] = []
    for feature_id, item in sorted(state["features"].items(), key=lambda pair: pair[1]["directory"]):
        folder = item["directory"]
        title = (
            new_feature[2] if new_feature is not None and feature_id == new_feature[0]
            else read_feature(root, state, feature_id)["title"]
        )
        default = " — worktree default" if feature_id == state["default_feature_id"] else ""
        entries.append(
            f"- [{title}]({folder}/spec.md) — `{feature_id}` — {item['status']}{default}"
        )
    newline = "\r\n" if "\r\n" in original else "\n"
    block = newline.join((
        _INDEX_START, "## EvoSpec Feature specifications", "",
        *entries, _INDEX_END,
    ))
    if _INDEX_START in original:
        start, end = original.index(_INDEX_START), original.index(_INDEX_END)
        original = original[:start] + block + original[end + len(_INDEX_END):]
    else:
        if original and not original.endswith(("\n", "\r")):
            original += newline
        original += (newline if original else "") + block + newline
    return {relative: (original.encode("utf-8"), before)}


def index_write_selected(
    root: Path, state: ContinuityStateData, feature_id: str, title: str,
) -> dict[str, tuple[bytes, str | None]]:
    """Refresh one selected Feature row without reading or rewriting other rows."""
    relative = "specs/README.md"
    safe_document_path(root, relative)
    path = root / Path(relative)
    before = file_hash(path)
    if before is None:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has no delimited Feature index")
    try:
        original = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md cannot be read") from error
    if (
        original.count(_INDEX_START) != 1
        or original.count(_INDEX_END) != 1
        or original.index(_INDEX_START) > original.index(_INDEX_END)
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "specs/README.md has an incomplete or ambiguous EvoSpec index")
    feature = state["features"].get(feature_id)
    if feature is None:
        raise ContinuityError("FEATURE_REQUIRED", "index refresh requires the selected Feature")
    folder = feature["directory"]
    default = " — worktree default" if feature_id == state["default_feature_id"] else ""
    replacement = (
        f"- [{title}]({folder}/spec.md) — `{feature_id}` — "
        f"{feature['status']}{default}"
    )
    start = original.index(_INDEX_START) + len(_INDEX_START)
    end = original.index(_INDEX_END)
    block = original[start:end]
    lines = block.splitlines(keepends=True)
    matches = [
        index for index, line in enumerate(lines)
        if f"`{feature_id}`" in line.rstrip("\r\n")
    ]
    if len(matches) != 1:
        raise ContinuityError(
            "DOCUMENT_DIVERGED",
            "specs/README.md must contain exactly one delimited row for the selected Feature",
        )
    row_index = matches[0]
    old_row = lines[row_index].rstrip("\r\n")
    expected_marker = f"]({folder}/spec.md) — `{feature_id}` — "
    if not old_row.startswith("- [") or expected_marker not in old_row:
        raise ContinuityError(
            "DOCUMENT_DIVERGED",
            "the selected Feature index row is malformed or points to another document",
        )
    ending = lines[row_index][len(old_row):]
    lines[row_index] = replacement + ending
    updated = original[:start] + "".join(lines) + original[end:]
    return {relative: (updated.encode("utf-8"), before)}


def pending_document_preservation_issue(
    relative: str,
    feature_id: str,
    current: bytes,
    intended: bytes,
    *,
    current_feature: dict[str, object] | None = None,
    intended_feature: dict[str, object] | None = None,
) -> str | None:
    """Require a preserved third version to retain queued identities and references."""
    name = relative.rsplit("/", 1)[-1][:-3]
    if name not in _DOCUMENT_NAMES or _DATA_START.encode() in current or _DATA_END.encode() in current:
        return "merge the queued canonical document into ordinary Markdown before preserving this path"
    try:
        actual = parse_document_content(current, relative, name, feature_id)
        queued = parse_document_content(intended, relative, name, feature_id)
        if name == "spec":
            _validate_feature(actual)
            _validate_feature(queued)
            actual_criteria = cast(list[dict[str, object]], cast(object, actual["criteria"]))
            actual_increments = cast(list[dict[str, object]], cast(object, actual["increments"]))
            queued_criteria = cast(list[dict[str, object]], cast(object, queued["criteria"]))
            queued_increments = cast(list[dict[str, object]], cast(object, queued["increments"]))
            actual_ids = {
                cast(str, item["id"])
                for item in (*actual_criteria, *actual_increments)
            }
            queued_ids = {
                cast(str, item["id"])
                for item in (*queued_criteria, *queued_increments)
            }
            if not queued_ids <= actual_ids:
                return "merge every queued criterion and increment ID into spec.md, then refresh status"
        elif name == "tasks":
            if current_feature is None or intended_feature is None:
                return "restore valid Feature documents before preserving tasks.md"
            if actual.get("feature_id") != feature_id or queued.get("feature_id") != feature_id:
                return "repair the Feature reference in tasks.md, then refresh status"
            actual_tasks = cast(list[object], actual["tasks"])
            queued_tasks = cast(list[object], queued["tasks"])
            _validate_tasks(actual_tasks, cast(ContinuityFeatureDocument, cast(object, current_feature)))
            _validate_tasks(queued_tasks, cast(ContinuityFeatureDocument, cast(object, intended_feature)))
            actual_by_id = {
                cast(str, cast(dict[str, object], task)["id"]): cast(dict[str, object], task)
                for task in actual_tasks
            }
            for item in queued_tasks:
                queued_task = cast(dict[str, object], item)
                preserved = actual_by_id.get(cast(str, queued_task["id"]))
                if preserved is None or any(
                    preserved[key] != queued_task[key]
                    for key in ("increment_id", "criteria_ids", "depends_on")
                ):
                    return "merge every queued Task ID and its links into tasks.md, then refresh status"
        elif name == "decisions":
            queued_records = cast(list[object], queued["decisions"])
            actual_records = cast(list[object], actual["decisions"])
            _validate_decision_list(queued_records, relative)
            _validate_decision_list(actual_records, relative)
            actual_by_id = {
                cast(str, cast(dict[str, object], item)["id"]): cast(dict[str, object], item)
                for item in actual_records
            }
            if any(
                actual_by_id.get(cast(str, cast(dict[str, object], item)["id"])) != item
                for item in queued_records
            ):
                return "preserve every queued immutable decision ID and its exact content in decisions.md"
        elif (
            actual.get("feature_id") != feature_id
            or queued.get("feature_id") != feature_id
        ):
            return "repair the Feature reference in this document, then refresh status"
    except (ContinuityError, KeyError, TypeError, ValueError) as error:
        return f"repair the current and queued Markdown definitions before preserving this path: {error}"
    return None


def pending_index_preservation_issue(
    content: bytes, state: ContinuityStateData,
) -> str | None:
    """Require every next-state Feature to remain uniquely linked in the delimited index."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "repair specs/README.md as UTF-8 and retain every queued Feature row"
    if (
        text.count(_INDEX_START) != 1
        or text.count(_INDEX_END) != 1
        or text.index(_INDEX_START) > text.index(_INDEX_END)
    ):
        return "restore one delimited EvoSpec index in specs/README.md before preserving it"
    block = text[
        text.index(_INDEX_START) + len(_INDEX_START):text.index(_INDEX_END)
    ]
    lines = block.splitlines()
    for feature_id, feature in state["features"].items():
        matches = [line for line in lines if f"`{feature_id}`" in line]
        expected = f"]({feature['directory']}/spec.md) — `{feature_id}` — "
        if len(matches) != 1 or not matches[0].startswith("- [") or expected not in matches[0]:
            return (
                f"merge the unique queued row for Feature {feature_id} into the delimited "
                "specs/README.md index, then refresh status"
            )
    return None


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


def _parse_markdown_document(
    text: str, relative: str, feature_id: str, name: str,
) -> dict[str, object]:
    if len(text.encode("utf-8")) > 512 * 1024:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} exceeds the document size bound")
    marker_ids = _FEATURE.findall(text)
    if marker_ids != [feature_id]:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has a missing, duplicate or wrong Feature ID")
    text = _without_block(text, _GENERATED_START, _GENERATED_END, relative)
    text = _without_block(text, _NOTES_START, _NOTES_END, relative)
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# ") or not lines[0][2:].strip():
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} needs one nonblank H1 title")
    title = lines[0][2:].strip()
    if name == "spec":
        sections = _sections(lines, relative)
        if set(sections) != {
            "Kind", "Outcome", "Acceptance criteria", "Increments (optional Slices)",
        }:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has unsupported or missing sections")
        kind = _single_value(sections["Kind"], "Kind", relative).strip("`")
        outcome = _multiline(sections["Outcome"])
        criteria = _parse_criteria(sections["Acceptance criteria"], relative)
        increments = _parse_increments(sections["Increments (optional Slices)"], relative)
        return {
            "id": feature_id, "kind": kind, "title": title, "outcome": outcome,
            "criteria": criteria, "increments": increments,
        }
    if name == "plan":
        if title != f"Plan for `{feature_id}`":
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} title does not match its Feature")
        sections = _sections(lines, relative)
        if set(sections) != {"Plan"}:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has unsupported or missing sections")
        return {"feature_id": feature_id, "plan": _multiline(sections["Plan"], allow_empty=True)}
    if name == "tasks":
        if title != f"Tasks for `{feature_id}`":
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} title does not match its Feature")
        sections = _sections(lines, relative)
        if set(sections) != {"Tasks"}:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has unsupported or missing sections")
        return {"feature_id": feature_id, "tasks": _parse_tasks(sections["Tasks"], relative)}
    if name == "decisions":
        if title != f"Decisions for `{feature_id}`":
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} title does not match its Feature")
        sections = _sections(lines, relative)
        if set(sections) != {"Decisions"}:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has unsupported or missing sections")
        return {"feature_id": feature_id, "decisions": _parse_decisions(sections["Decisions"], relative)}
    raise ContinuityError("INVALID_DOCUMENT", f"unsupported canonical document {name}")


def _sections(lines: list[str], relative: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[1:]:
        match = re.fullmatch(r"## ([^\r\n]+?)\s*", line)
        if match:
            section = match.group(1)
            if section is None:
                raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has an invalid section")
            current = section
            if section in sections:
                raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has duplicate {current} sections")
            sections[section] = []
        elif current is not None:
            sections[current].append(line)
        elif line.strip() and not line.startswith("<!-- evospec-feature:"):
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has content outside a supported section")
    return sections


def _parse_criteria(lines: list[str], relative: str) -> list[dict[str, str]]:
    groups = _bullet_groups(lines, relative, "criterion")
    result: list[dict[str, str]] = []
    for identity, heading, body in groups:
        if not _valid_identity(identity, "C"):
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has an invalid criterion ID")
        description: list[str] = [heading]
        kind: str | None = None
        target: str | None = None
        for line in body:
            stripped = line.strip()
            if stripped.startswith("- Check:"):
                if kind is not None:
                    raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} duplicates a criterion check field")
                kind = stripped.removeprefix("- Check:").strip().strip("`")
            elif stripped.startswith("- Target:"):
                if target is not None:
                    raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} duplicates a criterion target")
                target = stripped.removeprefix("- Target:").strip().strip("`")
            elif stripped and not stripped.startswith("- Check:") and not stripped.startswith("- Target:"):
                description.append(stripped)
        if kind is None or target is None:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} criterion needs Check and Target fields")
        result.append({
            "id": identity,
            "description": "\n".join(description).strip(),
            "check_kind": kind,
            "check_target": target,
        })
    return result


def _parse_increments(lines: list[str], relative: str) -> list[dict[str, str]]:
    if [
        line.strip() for line in lines if line.strip()
    ] == ["_No increments; Tasks belong directly to this Feature._"]:
        return []
    groups = _bullet_groups(lines, relative, "increment")
    result = []
    for identity, heading, body in groups:
        if not _valid_identity(identity, "S"):
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has an invalid increment ID")
        if not heading.startswith("**") or not heading.endswith("**"):
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} increment title must be bold")
        outcome_lines = [
            line.strip().removeprefix("Outcome:").strip()
            for line in body if line.strip()
        ]
        first_body_line = next((line.strip() for line in body if line.strip()), "")
        if not outcome_lines or not first_body_line.startswith("Outcome:"):
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} increment needs an Outcome field")
        result.append({
            "id": identity, "title": heading[2:-2].strip(),
            "outcome": "\n".join(outcome_lines).strip(),
        })
    return result


def _bullet_groups(
    lines: list[str], relative: str, kind: str,
) -> list[tuple[str, str, list[str]]]:
    groups: list[tuple[str, str, list[str]]] = []
    for line in lines:
        if not line.strip():
            if groups:
                groups[-1][2].append("")
            continue
        match = re.fullmatch(r"\s*-\s+`([^`]+)`\s+—\s+(.+?)\s*", line)
        if match:
            groups.append((match.group(1), match.group(2), []))
        elif groups:
            groups[-1][2].append(line)
        else:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has unsupported {kind} structure")
    if not groups:
        return []
    return groups


def _parse_tasks(lines: list[str], relative: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    section: list[str] = []
    def finish() -> None:
        nonlocal current, section
        if current is None:
            return
        fields: dict[str, str] = {}
        description_lines: list[str] = []
        in_description = False
        for line in section:
            stripped = line.strip()
            if stripped == "- Description:":
                if in_description:
                    raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} duplicates Task description")
                in_description = True
            elif not in_description and stripped.startswith("- "):
                match = re.fullmatch(r"- (Increment|Criteria|Depends on):\s*(.*)", stripped)
                if not match or match.group(1) in fields:
                    raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has an unsupported Task field")
                fields[match.group(1)] = match.group(2)
            elif in_description:
                description_lines.append(line[2:] if line.startswith("  ") else line)
            elif stripped:
                raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has Task prose outside Description")
        if set(fields) != {"Increment", "Criteria", "Depends on"} or not in_description:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} Task needs Increment, Criteria, Depends on and Description")
        increment = _unquote(fields["Increment"])
        criteria = _reference_list(fields["Criteria"])
        dependencies = _reference_list(fields["Depends on"])
        current.update({
            "increment_id": None if increment == "none" else _unquote(increment),
            "criteria_ids": criteria,
            "depends_on": dependencies,
            "description": "\n".join(description_lines).strip(),
        })
        records.append(current)
        current = None
        section = []
    for line in lines:
        match = re.fullmatch(r"### `([^`]+)` — (.+?)\s*", line)
        if match:
            finish()
            current = {"id": match.group(1), "title": match.group(2)}
            section = []
        elif current is not None:
            section.append(line)
        elif line.strip():
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has content outside a Task definition")
    finish()
    return records


def _parse_decisions(lines: list[str], relative: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current_id: str | None = None
    question: str | None = None
    body: list[str] = []
    def finish() -> None:
        nonlocal current_id, question, body
        if current_id is None:
            return
        decision = "\n".join(body).strip()
        if not _valid_identity(current_id, "D") or not question or not decision:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} decision is incomplete")
        records.append({"id": current_id, "question": question, "decision": decision})
        current_id, question, body = None, None, []
    for line in lines:
        match = re.fullmatch(r"### `([^`]+)` — (.+?)\s*", line)
        if match:
            finish()
            current_id, question = match.groups()
        elif current_id is not None:
            body.append(line)
        elif line.strip():
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has content outside a decision")
    finish()
    return records


def _validate_feature(value: dict[str, object]) -> None:
    if (
        not isinstance(value["title"], str) or not value["title"].strip()
        or not isinstance(value["outcome"], str) or not value["outcome"].strip()
        or value["kind"] not in ("FEATURE", "CORRECTION")
        or type(value["criteria"]) is not list or not value["criteria"]
        or len(value["criteria"]) > 200
        or type(value["increments"]) is not list or len(value["increments"]) > 100
    ):
        raise ContinuityError("DOCUMENT_DIVERGED", "Feature content is malformed")
    all_ids: set[str] = set()
    for item in value["criteria"]:
        if (
            type(item) is not dict or set(item) != {"id", "description", "check_kind", "check_target"}
            or not _valid_identity(item["id"], "C") or item["id"] in all_ids
            or not isinstance(item["description"], str) or not item["description"].strip()
            or item["check_kind"] not in ("python-unittest", "node-test")
            or not isinstance(item["check_target"], str)
        ):
            raise ContinuityError("DOCUMENT_DIVERGED", "Feature criterion is malformed")
        all_ids.add(cast(str, item["id"]))
    for item in value["increments"]:
        if (
            type(item) is not dict or set(item) != {"id", "title", "outcome"}
            or not _valid_identity(item["id"], "S") or item["id"] in all_ids
            or not isinstance(item["title"], str) or not item["title"].strip()
            or not isinstance(item["outcome"], str) or not item["outcome"].strip()
        ):
            raise ContinuityError("DOCUMENT_DIVERGED", "Feature increment is malformed")
        all_ids.add(cast(str, item["id"]))


def _validate_tasks(tasks: list[object], feature: ContinuityFeatureDocument) -> None:
    criteria = {item["id"] for item in feature["criteria"]}
    increments = {item["id"] for item in feature["increments"]}
    ids: set[str] = set()
    for task in tasks:
        if (
            type(task) is not dict
            or set(task) != {"id", "title", "description", "increment_id", "criteria_ids", "depends_on"}
            or not _valid_identity(task["id"], "T") or task["id"] in ids
            or not isinstance(task["title"], str) or not task["title"].strip()
            or not isinstance(task["description"], str)
            or task["increment_id"] is not None and task["increment_id"] not in increments
            or type(task["criteria_ids"]) is not list or not task["criteria_ids"]
            or any(item not in criteria for item in task["criteria_ids"])
            or len(task["criteria_ids"]) != len(set(task["criteria_ids"]))
            or type(task["depends_on"]) is not list
            or any(not isinstance(item, str) or not item.startswith("T-") for item in task["depends_on"])
            or len(task["depends_on"]) != len(set(task["depends_on"]))
        ):
            raise ValueError("invalid Task record")
        ids.add(cast(str, task["id"]))
    by_id = {cast(str, cast(dict[str, object], task)["id"]): cast(dict[str, object], task) for task in tasks}
    dependents: dict[str, list[str]] = {identity: [] for identity in by_id}
    remaining: dict[str, int] = {}
    for identity, task in by_id.items():
        dependencies = cast(list[str], task["depends_on"])
        if any(dependency not in by_id for dependency in dependencies):
            raise ValueError("dangling Task dependency")
        remaining[identity] = len(dependencies)
        for dependency in dependencies:
            dependents[dependency].append(identity)
    ready = [identity for identity, count in remaining.items() if count == 0]
    visited = 0
    while ready:
        identity = ready.pop()
        visited += 1
        for dependent in dependents[identity]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
    if visited != len(by_id):
        raise ValueError("cyclic Task dependency")
    if len(tasks) > 1000:
        raise ValueError("too many Tasks")


def _parse_legacy_document(content: str, relative: str, feature_id: str) -> dict[str, object]:
    return _read_legacy_document(content, relative, feature_id)


def _read_legacy_document(content: str, relative: str, feature_id: str) -> dict[str, object]:
    start = content.rfind(_DATA_START)
    end = content.rfind(_DATA_END) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has no complete legacy data block")
    block = content[start + len(_DATA_START):end].strip().replace("\r\n", "\n")
    if not block.startswith("```json\n") or not block.endswith("\n```"):
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has a malformed legacy data block")
    try:
        value: object = json.loads(block[len("```json\n"):-len("\n```")])
    except (ValueError, RecursionError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} legacy data cannot be parsed") from error
    if type(value) is not dict or value.get("feature_id", feature_id) != feature_id:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} data belongs to another Feature")
    return cast(dict[str, object], value)


def _generated_checklist(
    state: ContinuityStateData, feature_id: str, feature: dict[str, object],
    tasks: Sequence[ContinuityTaskDocument],
) -> str:
    input_snapshot = cast(ContinuityInputScope | None, state.get("input_snapshot"))
    view_state: ContinuityStateView = {
        "checks": tuple(_continuity_check_view(check) for check in state["checks"]),
        "attempts": tuple(state["attempts"]),
        "execution": None if state["execution"] is None else {"task_id": state["execution"]["task_id"]},
        "paused_features": tuple(state["paused_features"]),
        "task_events": tuple(state.get("task_events", ())),
        "input_snapshot": None if input_snapshot is None else {
            "policy_id": input_snapshot["policy_id"],
            "policy_digest": input_snapshot["policy_digest"],
            "fingerprint": input_snapshot["fingerprint"],
        },
    }
    criteria = cast(list[ContinuityCriterionDocument], feature["criteria"])
    view_feature: ContinuityFeatureView = {"criteria": tuple(criteria)}
    view_tasks: tuple[ContinuityTaskView, ...] = tuple(
        {
            "id": item["id"], "title": item["title"],
            "description": item["description"],
            "increment_id": item["increment_id"],
            "criteria_ids": tuple(item["criteria_ids"]),
            "depends_on": tuple(item["depends_on"]),
        }
        for item in tasks
    )
    lines = [
        _GENERATED_START,
        "## Checklist (derived; checkboxes do not record progress)",
        "",
    ]
    for item in tasks:
        view_task: ContinuityTaskView = {
            "id": item["id"], "title": item["title"], "description": item["description"],
            "increment_id": item["increment_id"], "criteria_ids": tuple(item["criteria_ids"]),
            "depends_on": tuple(item["depends_on"]),
        }
        status = continuity_task_status(view_state, view_feature, view_task, view_tasks)
        lines.append(f"- [{'x' if status == 'COMPLETED' else ' '}] `{item['id']}` — {status}")
    lines.extend((_GENERATED_END, ""))
    return "\n".join(lines)


def _task_definition(task: ContinuityTaskDocument) -> str:
    increment = task["increment_id"] or "none"
    criteria = ", ".join(f"`{item}`" for item in task["criteria_ids"])
    dependencies = ", ".join(f"`{item}`" for item in task["depends_on"]) or "none"
    description = task["description"]
    indented = "\n".join("  " + line if line else "" for line in description.splitlines())
    return (
        f"### `{task['id']}` — {task['title']}\n"
        f"- Increment: `{increment}`\n"
        f"- Criteria: {criteria}\n"
        f"- Depends on: {dependencies}\n"
        f"- Description:\n{indented}\n"
    )


def _append_task_definitions(original: str, tasks: list[ContinuityTaskDocument]) -> str:
    if not tasks:
        return original
    rendered = "\n".join(_task_definition(task) for task in tasks)
    marker = _GENERATED_START
    if marker in original:
        before, after = original.split(marker, 1)
        before = before.rstrip("\r\n") + "\n\n" + rendered.rstrip() + "\n\n"
        return before + marker + after
    notes = original.find(_NOTES_START)
    position = notes if notes >= 0 else len(original)
    return original[:position].rstrip("\r\n") + "\n\n" + rendered + "\n" + original[position:]


def _append_decisions(original: str, decisions: list[dict[str, object]]) -> str:
    if not decisions:
        return original
    rendered = "\n".join(
        f"### `{item['id']}` — {item['question']}\n\n{item['decision']}\n"
        for item in decisions
    )
    notes = original.find(_NOTES_START)
    position = notes if notes >= 0 else len(original)
    return original[:position].rstrip("\r\n") + "\n\n" + rendered + "\n" + original[position:]


def _replace_generated(text: str, generated: str) -> str:
    start_count, end_count = text.count(_GENERATED_START), text.count(_GENERATED_END)
    if start_count != end_count or start_count > 1:
        raise ContinuityError("DOCUMENT_DIVERGED", "tasks.md has invalid generated checklist markers")
    if start_count:
        start = text.index(_GENERATED_START)
        end = text.index(_GENERATED_END) + len(_GENERATED_END)
        newline = "\r\n" if "\r\n" in text else "\n"
        rendered = generated.rstrip("\n").replace("\n", newline)
        return text[:start] + rendered + text[end:]
    notes = text.find(_NOTES_START)
    position = notes if notes >= 0 else len(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    rendered = generated.replace("\n", newline)
    return (
        text[:position].rstrip("\r\n") + newline + newline
        + rendered + newline + text[position:]
    )


def _generated_content(text: str) -> str | None:
    if text.count(_GENERATED_START) != 1 or text.count(_GENERATED_END) != 1:
        return None
    start = text.index(_GENERATED_START)
    end = text.index(_GENERATED_END) + len(_GENERATED_END)
    return text[start:end].replace("\r\n", "\n").rstrip("\n") + "\n"


def _preserve_notes(original: str, rendered: str) -> str:
    if original.count(_NOTES_START) != 1 or original.count(_NOTES_END) != 1:
        return rendered
    start = original.index(_NOTES_START)
    end = original.index(_NOTES_END) + len(_NOTES_END)
    notes = original[start:end]
    if rendered.count(_NOTES_START) == 1 and rendered.count(_NOTES_END) == 1:
        new_start = rendered.index(_NOTES_START)
        new_end = rendered.index(_NOTES_END) + len(_NOTES_END)
        return rendered[:new_start] + notes + rendered[new_end:]
    return rendered


def _preserve_unchanged_sections(original: str, rendered: str, name: str) -> str:
    if name != "spec":
        return rendered
    try:
        old = _parse_markdown_document(
            original, "spec.md", _FEATURE.findall(original)[0], "spec",
        )
        new = _parse_markdown_document(
            rendered, "spec.md", _FEATURE.findall(rendered)[0], "spec",
        )
    except (ContinuityError, IndexError):
        return rendered
    if old == new:
        return original
    return rendered


def _relative(state: ContinuityStateData, feature_id: str, name: str) -> str:
    if name not in _DOCUMENT_NAMES or feature_id not in state["features"]:
        raise ContinuityError("INVALID_DOCUMENT", "unknown Feature document")
    return f"specs/{state['features'][feature_id]['directory']}/{name}.md"


def _read_optional(root: Path, relative: str) -> str | None:
    safe_document_path(root, relative)
    path = root / Path(relative)
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
        if len(data) > 512 * 1024:
            raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} exceeds the document size bound")
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} cannot be read as UTF-8") from error


def _without_block(text: str, start_marker: str, end_marker: str, relative: str) -> str:
    starts, ends = text.count(start_marker), text.count(end_marker)
    if starts != ends or starts > 1:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has invalid {start_marker} markers")
    if not starts:
        return text
    start = text.index(start_marker)
    close = text.find(end_marker, start + len(start_marker))
    if close < 0:
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} has reversed markers")
    end = close + len(end_marker)
    return text[:start] + text[end:]


def _single_value(lines: list[str], name: str, relative: str) -> str:
    nonempty = [line.strip() for line in lines if line.strip()]
    if len(nonempty) != 1 or not nonempty[0].startswith("- " + name + ":"):
        raise ContinuityError("DOCUMENT_DIVERGED", f"{relative} needs one {name} field")
    return nonempty[0].split(":", 1)[1].strip()


def _multiline(lines: list[str], *, allow_empty: bool = False) -> str:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    value = "\n".join(lines[start:end])
    return value if allow_empty else value.strip()


def _reference_list(value: str) -> list[str]:
    if _unquote(value) == "none":
        return []
    parts = [part.strip() for part in value.split(",")]
    result = [_unquote(part) for part in parts]
    if not result or any(not item for item in result):
        raise ValueError("invalid identity reference list")
    return result


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value.startswith("`") and value.endswith("`") else value


def _valid_identity(value: object, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix + "-") and (
        _ID.fullmatch(value) is not None or _ALIAS.fullmatch(value) is not None
    )


def _plain(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContinuityError("DOCUMENT_DIVERGED", "document shape is invalid")
    return dict(value)


def _plain_records(values: list[object]) -> list[dict[str, object]]:
    return [_plain(value) for value in values]
