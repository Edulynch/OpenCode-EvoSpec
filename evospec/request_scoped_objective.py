"""Pure derivation and verification for one bounded static-page objective.

Verification accepts only a lowercase doctype and html/head/body skeleton, one
attribute-free H1, and optional plain-text title, UTF-8 charset meta, and lang.
All other elements, attributes, resources, and executable or styling content
are outside this deliberately small subset.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import re
import unicodedata

REQUEST_SCOPED_OBJECTIVE = "REQUEST_SCOPED_OBJECTIVE"
OBJECTIVE_TEST_KIND = "core-static-heading-v1"
OBJECTIVE_TEST_TARGET = "index.html"
TASK_APP_TEST_KIND = "core-personal-tasks-browser-v1"
TASK_APP_TEST_TARGET = "index.html"

_MAX_CONTENT_BYTES = 16_384
_ENGLISH_REQUEST = re.compile(
    r'Create a static HTML page with the heading "([^"\r\n]+)"\.'
)
_SPANISH_REQUEST = re.compile(
    r"Crea una página HTML estática con el encabezado «([^«»\r\n]+)»\."
)
_ZERO_WIDTH_CODEPOINTS = {
    0x034F,  # combining grapheme joiner
    0x115F, 0x1160,  # Hangul fillers
    0x17B4, 0x17B5,  # Khmer inherent vowels
    0x180E,  # Mongolian vowel separator
    0x2800,  # blank Braille pattern
    0x3164, 0xFFA0,  # Hangul fillers
}
_LANGUAGE_TAG = r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*"
_WHITESPACE = r"[ \t\r\n]*"
_SAFE_TITLE_TEXT = r"(?:[^<&\t\r\n]|&(?:amp|lt|gt);)*"
_META = '<meta charset="utf-8">'
_TITLE = rf"<title>{_SAFE_TITLE_TEXT}</title>"
_HEAD_CONTENT = (
    rf"(?:(?:{_META}(?:{_WHITESPACE}{_TITLE})?|"
    rf"{_TITLE}(?:{_WHITESPACE}{_META})?){_WHITESPACE})?"
)


@dataclass(frozen=True, slots=True)
class StaticHeadingObjective:
    heading: str


@dataclass(frozen=True, slots=True)
class PersonalTasksObjective:
    explicit_operations: tuple[str, ...] = ()

    @property
    def operations(self) -> tuple[str, ...]:
        base = ("create", "consult")
        return base + (("complete",) if "complete" in self.explicit_operations else ())


def _is_unsafe_invisible_or_control(character: str) -> bool:
    codepoint = ord(character)
    category = unicodedata.category(character)
    return (
        category.startswith("C")
        or category in {"Zl", "Zp"}
        or (category == "Zs" and character != " ")
        or codepoint in _ZERO_WIDTH_CODEPOINTS
        or 0x180B <= codepoint <= 0x180F
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _valid_heading(heading: object) -> bool:
    return (
        type(heading) is str
        and bool(heading)
        and heading == heading.strip()
        and "  " not in heading
        and not any(_is_unsafe_invisible_or_control(char) for char in heading)
    )


def derive_static_heading(statement: str) -> StaticHeadingObjective | None:
    """Recognize only the two exact, inert static-heading request forms."""
    if type(statement) is not str or len(statement) > 2048:
        return None

    candidate = statement.strip(" \t\r\n")
    match = _ENGLISH_REQUEST.fullmatch(candidate)
    if match is None:
        match = _SPANISH_REQUEST.fullmatch(candidate)
    if match is None:
        return None

    heading = match.group(1)
    if not _valid_heading(heading):
        return None
    return StaticHeadingObjective(heading=heading)


def verify_static_heading(
    objective: StaticHeadingObjective, content: bytes
) -> str | None:
    """Hash exact source bytes only when the canonical subset proves the H1.

    Non-ASCII documents require the accepted UTF-8 charset meta so browser
    decoding cannot silently change the heading text.
    """
    if type(objective) is not StaticHeadingObjective or not _valid_heading(
        objective.heading
    ):
        return None
    if type(content) is not bytes or len(content) > _MAX_CONTENT_BYTES:
        return None

    try:
        document = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if any(
        _is_unsafe_invisible_or_control(char)
        for char in document
        if char not in "\t\r\n"
    ):
        return None
    if not document.isascii() and _META not in document:
        return None

    escaped_heading = re.escape(html.escape(objective.heading, quote=False))
    page_pattern = re.compile(
        rf'<!doctype html>{_WHITESPACE}'
        rf'<html(?: lang="{_LANGUAGE_TAG}")?>{_WHITESPACE}'
        rf"<head>{_WHITESPACE}{_HEAD_CONTENT}</head>{_WHITESPACE}"
        rf"<body>{_WHITESPACE}<h1>{escaped_heading}</h1>"
        rf"{_WHITESPACE}</body>{_WHITESPACE}</html>{_WHITESPACE}"
    )
    if page_pattern.fullmatch(document) is None:
        return None
    return hashlib.sha256(content).hexdigest()


_TASK_WORD_OR_PUNCTUATION = re.compile(r"[a-z]+(?:-[a-z]+)*|[.,;:!?¿¡]")
_TASK_ALLOWED_SPANISH = frozenset("áéíóúüñÁÉÍÓÚÜÑ")
_TASK_ALLOWED_PUNCTUATION = frozenset(".,;:!?¿¡-")
_TASK_WHITESPACE = frozenset(" \t\r\n")
# Full accepted shapes: request + web-app + purpose + task target, or
# purpose + comma + request + web-app. Any remainder must be an explicit
# operation phrase; unparsed words and clauses fail closed.
_POLITE_PREFIXES = (
    ("please",),
    ("could", "you"),
    ("could", "you", "please"),
    ("por", "favor"),
    ("podrias",),
    ("puedes",),
)
_DESIRE_PREFIXES = (
    ("i", "want"),
    ("i", "need"),
    ("i", "would", "like"),
    ("quiero",),
    ("necesito",),
    ("me", "gustaria"),
)
_REQUEST_VERBS = frozenset(
    {
        "build", "create", "make", "hacer", "crear",
    }
)
_APP_PHRASES = (
    ("web", "app"),
    ("web", "application"),
    ("website",),
    ("web", "site"),
    ("aplicacion", "web"),
    ("app", "web"),
    ("sitio", "web"),
    ("pagina", "web"),
)
_APP_ARTICLES = frozenset({"a", "an", "the", "una", "un", "el", "la", "my", "mi"})
_APP_MODIFIERS = frozenset(
    {
        "simple", "sencilla", "sencillo", "small", "pequena", "pequeno", "local",
    }
)
_TASK_TARGETS = (
    ("my", "personal", "tasks"),
    ("my", "tasks"),
    ("personal", "tasks"),
    ("tasks",),
    ("my", "task", "list"),
    ("task", "list"),
    ("my", "to-do", "list"),
    ("to-do", "list"),
    ("my", "todo", "list"),
    ("todo", "list"),
    ("my", "to", "do", "list"),
    ("to", "do", "list"),
    ("mis", "tareas", "personales"),
    ("mis", "tareas"),
    ("tareas", "personales"),
    ("tareas",),
    ("mi", "lista", "de", "tareas"),
    ("lista", "de", "tareas"),
)
_MANAGEMENT_VERBS = frozenset(
    {
        "manage", "managing", "organize", "organizing", "track",
        "gestionar", "administrar", "organizar",
    }
)
_PURPOSE_PREFIXES = (
    (("to",), False),
    (("for",), True),
    (("para",), True),
    (("para", "poder"), False),
    (("where", "i", "can"), False),
    (("that", "lets", "me"), False),
    (("que", "me", "permita"), False),
)
_TASK_FIRST_PREFIXES = (("to",), ("para",))
_TAIL_CONNECTORS = frozenset(
    {"and", "also", "then", "plus", "y", "tambien", "luego", "ademas"}
)
_TAIL_WRAPPERS = (
    ("where", "i", "can"),
    ("that", "lets", "me"),
    ("let", "me"),
    ("i", "can"),
    ("que", "me", "permita"),
    ("para", "poder"),
    ("poder",),
    ("puedo",),
)
_OPERATION_CONNECTORS = frozenset(
    {"and", "then", "also", "y", "luego", "tambien", "ademas"}
)
_CREATE_VERBS = (
    "add", "adding", "create", "creating", "agregar", "anadir", "crear",
)
_CREATE_OBJECTS = (
    ("tasks",), ("new", "tasks"), ("my", "tasks"), ("a", "task"),
    ("tareas",), ("nuevas", "tareas"), ("mis", "tareas"),
    ("una", "tarea"),
)
_CONSULT_VERBS = (
    "view", "see", "show", "list", "consult", "ver", "mostrar", "listar",
    "consultar",
)
_CONSULT_OBJECTS = (
    ("tasks",), ("my", "tasks"), ("personal", "tasks"),
    ("my", "personal", "tasks"), ("task", "list"), ("my", "task", "list"),
    ("to-do", "list"), ("my", "to-do", "list"),
    ("tareas",), ("mis", "tareas"), ("mis", "tareas", "personales"),
    ("lista", "de", "tareas"), ("mi", "lista", "de", "tareas"),
)
_EN_COMPLETE_TARGETS = (
    ("tasks",), ("my", "tasks"), ("them",), ("it",),
)
_EN_COMPLETE_STATUSES = (("complete",), ("completed",), ("done",))
_ES_COMPLETE_TARGETS = (
    ("tareas",), ("las", "tareas"), ("las",),
)
_ES_COMPLETE_STATUSES = (
    ("completada",), ("completadas",),
)


def _starts_with(tokens: tuple[str, ...], start: int, phrase: tuple[str, ...]) -> bool:
    return tokens[start : start + len(phrase)] == phrase


def _task_tokens(statement: str) -> tuple[str, ...] | None:
    if type(statement) is not str or len(statement) > 2048:
        return None

    source = unicodedata.normalize("NFC", statement)
    for character in source:
        if character in _TASK_WHITESPACE or character in _TASK_ALLOWED_PUNCTUATION:
            continue
        if character.isascii() and character.isalpha():
            continue
        if character in _TASK_ALLOWED_SPANISH:
            continue
        return None

    folded = unicodedata.normalize("NFD", source.casefold())
    normalized = "".join(
        character
        for character in folded
        if unicodedata.category(character) != "Mn"
    )
    tokens: list[str] = []
    cursor = 0
    for match in _TASK_WORD_OR_PUNCTUATION.finditer(normalized):
        if any(not character.isspace() for character in normalized[cursor : match.start()]):
            return None
        tokens.append(match.group())
        cursor = match.end()
    if any(not character.isspace() for character in normalized[cursor:]):
        return None
    return tuple(tokens)


def _request_prefix_ends(tokens: tuple[str, ...]) -> tuple[int, ...]:
    start = 1 if tokens[:1] == ("¿",) else 0
    offsets = [(start, False)]
    for prefix in _POLITE_PREFIXES:
        if _starts_with(tokens, start, prefix):
            offsets.append((start + len(prefix), True))

    ends: set[int] = set()
    for offset, polite in offsets:
        for prefix in _DESIRE_PREFIXES:
            if not polite and _starts_with(tokens, offset, prefix):
                end = offset + len(prefix)
                ends.add(end)
                action_start = end + (1 if tokens[end : end + 1] == ("to",) else 0)
                if action_start < len(tokens) and tokens[action_start] in _REQUEST_VERBS:
                    action_end = action_start + 1
                    ends.add(action_end + (1 if tokens[action_end : action_end + 1] == ("me",) else 0))
        if offset < len(tokens) and tokens[offset] in _REQUEST_VERBS:
            action_end = offset + 1
            ends.add(action_end + (1 if tokens[action_end : action_end + 1] == ("me",) else 0))
        if polite and _starts_with(tokens, offset, ("necesito",)):
            ends.add(offset + 1)

    return tuple(sorted(end for end in ends if end <= len(tokens)))


def _app_phrase_ends(tokens: tuple[str, ...], start: int) -> tuple[int, ...]:
    index = start
    if index < len(tokens) and tokens[index] in _APP_ARTICLES:
        index += 1
    modifiers = 0
    while index < len(tokens) and tokens[index] in _APP_MODIFIERS and modifiers < 2:
        index += 1
        modifiers += 1

    core = next(
        (phrase for phrase in _APP_PHRASES if _starts_with(tokens, index, phrase)),
        None,
    )
    if core is None:
        return ()
    index += len(core)
    ends = [index]
    for _ in range(2):
        if index < len(tokens) and tokens[index] in _APP_MODIFIERS:
            index += 1
            ends.append(index)
        else:
            break
    return tuple(ends)


def _is_task_target(tokens: tuple[str, ...]) -> bool:
    return tokens in _TASK_TARGETS


def _is_management_request(tokens: tuple[str, ...]) -> bool:
    return (
        len(tokens) > 1
        and tokens[0] in _MANAGEMENT_VERBS
        and _is_task_target(tokens[1:])
    )


def _is_task_first_phrase(tokens: tuple[str, ...]) -> bool:
    return any(
        _starts_with(tokens, 0, prefix)
        and _is_management_request(tokens[len(prefix) :])
        for prefix in _TASK_FIRST_PREFIXES
    )


def _is_app_purpose(tokens: tuple[str, ...]) -> bool:
    for prefix, allows_direct_target in _PURPOSE_PREFIXES:
        if not _starts_with(tokens, 0, prefix):
            continue
        remainder = tokens[len(prefix) :]
        if allows_direct_target and _is_task_target(remainder):
            return True
        if _is_management_request(remainder):
            return True
    return False


def _without_question_opener(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    if tokens[:1] == ("¿",):
        tokens = tokens[1:]
    if any(token in {"¿", "¡"} for token in tokens):
        return None
    return tokens


def _parse_main_core(tokens: tuple[str, ...]) -> bool:
    tokens = _without_question_opener(tokens)
    if tokens is None or not tokens:
        return False

    for prefix_end in _request_prefix_ends(tokens):
        for app_end in _app_phrase_ends(tokens, prefix_end):
            if _is_app_purpose(tokens[app_end:]):
                return True

    for comma_index, token in enumerate(tokens):
        if token != "," or not _is_task_first_phrase(tokens[:comma_index]):
            continue
        remainder = tokens[comma_index + 1 :]
        for prefix_end in _request_prefix_ends(remainder):
            if any(
                app_end == len(remainder)
                for app_end in _app_phrase_ends(remainder, prefix_end)
            ):
                return True
    return False


def _strip_final_punctuation(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if tokens and tokens[-1] in {".", "!", "?"}:
        return tokens[:-1]
    return tokens


def _operation_phrases() -> tuple[tuple[tuple[str, ...], str], ...]:
    phrases: list[tuple[tuple[str, ...], str]] = []
    for verb in _CREATE_VERBS:
        for target in _CREATE_OBJECTS:
            phrases.append(((verb,) + target, "create"))
    for verb in _CONSULT_VERBS:
        for target in _CONSULT_OBJECTS:
            phrases.append(((verb,) + target, "consult"))

    for verb in ("mark", "marking"):
        for target in _EN_COMPLETE_TARGETS:
            for linker in ((), ("as",)):
                for status in _EN_COMPLETE_STATUSES:
                    phrases.append(((verb,) + target + linker + status, "complete"))
    for verb in ("marcar", "marcando", "marcarlas", "marcarlos"):
        targets = ((),) if verb in {"marcarlas", "marcarlos"} else _ES_COMPLETE_TARGETS
        for target in targets:
            for linker in ((), ("como",)):
                for status in _ES_COMPLETE_STATUSES:
                    phrases.append(((verb,) + target + linker + status, "complete"))
    return tuple(sorted(phrases, key=lambda entry: len(entry[0]), reverse=True))


_OPERATION_PHRASES = _operation_phrases()


def _match_operation(tokens: tuple[str, ...], start: int) -> tuple[str, int] | None:
    for phrase, operation in _OPERATION_PHRASES:
        if _starts_with(tokens, start, phrase):
            return operation, start + len(phrase)
    return None


def _parse_operation_sequence(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    index = 0
    operations: set[str] = set()
    while index < len(tokens):
        match = _match_operation(tokens, index)
        if match is None:
            return None
        operation, index = match
        operations.add(operation)
        if index == len(tokens):
            break

        if tokens[index : index + 1] == (",",):
            index += 1
        if index < len(tokens) and tokens[index] in _OPERATION_CONNECTORS:
            index += 1
            if index < len(tokens) and tokens[index] in {"then", "also", "luego", "tambien"}:
                index += 1
        else:
            return None
        if index == len(tokens):
            return None

    return tuple(operation for operation in ("create", "consult", "complete") if operation in operations)


def _parse_operation_tail(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    tokens = _strip_final_punctuation(tokens)
    if not tokens:
        return None
    index = 0
    while index < len(tokens) and tokens[index] in _TAIL_CONNECTORS:
        index += 1
    for wrapper in _TAIL_WRAPPERS:
        if _starts_with(tokens, index, wrapper):
            index += len(wrapper)
            break
    if tokens[index : index + 1] in {("also",), ("tambien",)}:
        index += 1
    return _parse_operation_sequence(tokens[index:])


def derive_personal_tasks(statement: str) -> PersonalTasksObjective | None:
    """Derive only a bounded personal-task web-app request, never user approval."""
    tokens = _task_tokens(statement)
    if tokens is None:
        return None

    main = _strip_final_punctuation(tokens)
    if _parse_main_core(main):
        return PersonalTasksObjective()

    for index, token in enumerate(tokens):
        if token not in {",", ";", ".", "and", "also", "then", "y", "tambien", "luego", "ademas", "where", "that", "que"}:
            continue
        if not _parse_main_core(tokens[:index]):
            continue
        operations = _parse_operation_tail(tokens[index + 1 :])
        if operations is not None:
            return PersonalTasksObjective(explicit_operations=operations)
    return None


def derive_request_objective(
    statement: str,
) -> StaticHeadingObjective | PersonalTasksObjective | None:
    """Try the unchanged legacy recognizer before the bounded task-app grammar."""
    objective = derive_static_heading(statement)
    return objective if objective is not None else derive_personal_tasks(statement)


__all__ = (
    "REQUEST_SCOPED_OBJECTIVE",
    "OBJECTIVE_TEST_KIND",
    "OBJECTIVE_TEST_TARGET",
    "TASK_APP_TEST_KIND",
    "TASK_APP_TEST_TARGET",
    "StaticHeadingObjective",
    "PersonalTasksObjective",
    "derive_static_heading",
    "verify_static_heading",
    "derive_personal_tasks",
    "derive_request_objective",
)
