from __future__ import annotations

from dataclasses import dataclass
import re
from unicodedata import normalize


DEFAULT_MAX_CHARS = 320
MIN_MAX_CHARS = 80

_BRACKET_CONTROL = re.compile(r"[\[\]]")
_TAG_CONTROL = re.compile(r"[<>]")
_MARKDOWN_CONTROL = re.compile(
    r"(?:[*_`~|#]|^\s*(?:[-+]\s|(?:-\s*){3,}$))",
    re.MULTILINE,
)
_DIGITS = re.compile(r"\d")
_URL_OR_EMAIL = re.compile(
    r"(?:https?://|www\.|[^\s@]+@[^\s@]+|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
    r"(?:[/?#][^\s]*)?)",
    re.IGNORECASE,
)
_COMMON_ABBREVIATION = re.compile(
    r"\b(?:aprox|art|av|cap|cel|dra|dr|ed|etc|ex|fig|num|nº|pag|pág|"
    r"profa|prof|sra|sr|tel|vol|pp|p)\.",
    re.IGNORECASE,
)
_UPPERCASE_TOKEN = re.compile(r"\b[A-ZÀ-ÖØ-Þ]{2,}\b")


class NarratorTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarratorSegment:
    line_number: int
    text: str
    warnings: tuple[str, ...]


def _error(line_number: int, message: str) -> NarratorTextError:
    return NarratorTextError(f"Locutor line {line_number} {message}")


def _warnings(text: str) -> tuple[str, ...]:
    warnings: list[str] = []
    if any(token in text for token in ("...", "…", ":", ";", "—", "–")):
        warnings.append("uses punctuation normalized by Chatterbox")
    if text[0].islower():
        warnings.append("starts with lowercase text that Chatterbox capitalizes")
    if _UPPERCASE_TOKEN.search(text):
        warnings.append("contains uppercase token; verify its spoken form")
    return tuple(warnings)


def prepare_chatterbox_segments(text: str, max_chars: int) -> list[NarratorSegment]:
    if not MIN_MAX_CHARS <= max_chars <= DEFAULT_MAX_CHARS:
        raise NarratorTextError(
            f"Chatterbox max chars must be between {MIN_MAX_CHARS} and {DEFAULT_MAX_CHARS}."
        )

    normalized = normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    segments: list[NarratorSegment] = []
    for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
        segment = " ".join(raw_line.split())
        if not segment:
            continue
        if len(segment) > max_chars:
            raise _error(
                line_number,
                f"has {len(segment)} characters; split it into complete spoken lines of at most "
                f"{max_chars} characters.",
            )
        if _BRACKET_CONTROL.search(segment):
            raise _error(line_number, "contains bracketed markup; write the intended speech instead.")
        if _TAG_CONTROL.search(segment):
            raise _error(line_number, "contains SSML, HTML, or comparison markup; write the intended speech instead.")
        if _MARKDOWN_CONTROL.search(segment):
            raise _error(line_number, "contains Markdown controls; write the intended speech instead.")
        if _DIGITS.search(segment):
            raise _error(line_number, "contains digits; write the value in full PT-BR words.")
        if _URL_OR_EMAIL.search(segment):
            raise _error(line_number, "contains a URL or email; use an approved spoken form.")
        if _COMMON_ABBREVIATION.search(segment):
            raise _error(line_number, "contains an abbreviation; expand it into PT-BR speech.")
        segments.append(
            NarratorSegment(
                line_number=line_number,
                text=segment,
                warnings=_warnings(segment),
            )
        )
    return segments
