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


__all__ = (
    "REQUEST_SCOPED_OBJECTIVE",
    "OBJECTIVE_TEST_KIND",
    "OBJECTIVE_TEST_TARGET",
    "StaticHeadingObjective",
    "derive_static_heading",
    "verify_static_heading",
)
