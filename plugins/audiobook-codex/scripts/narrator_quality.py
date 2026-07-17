from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from unicodedata import normalize

from book_layout import resolve_book_paths
from path_safety import resolve_under


QUALITY_PROFILE = "faithful-natural-v1"
REVIEW_SCHEMA_VERSION = "1.0"

FINDING_KINDS = {
    "roman_heading",
    "labelled_roman_numeral",
    "punctuation_cluster",
    "space_before_punctuation",
    "abbreviation",
    "date_or_time",
    "raw_number",
    "spoken_symbol",
    "line_boundary",
    "lowercase_locution",
    "uppercase_token",
    "introduced_punctuation",
    "corrupted_phrase_split",
    "mechanical_lowercase_start",
    "pronunciation_sensitive_term",
}
EVIDENCE_BACKED_FINDING_KINDS = {
    "introduced_punctuation",
    "corrupted_phrase_split",
    "mechanical_lowercase_start",
    "pronunciation_sensitive_term",
}

FINDING_STATUSES = {"resolved", "preserved"}
LOCUTION_CATEGORIES = {
    "heading",
    "prose",
    "dialogue",
    "quotation",
    "verse",
    "note",
    "list",
    "excluded",
}
PRONUNCIATION_KINDS = {
    "acronym",
    "abbreviation",
    "foreign_term",
    "proper_name",
    "religious_term",
    "technical_term",
}
PRONUNCIATION_DECISIONS = {"spoken_form", "preserved"}

_ROMAN_HEADING = re.compile(r"^\s*(?P<value>[MDCLXVI]+)\.?\s*$", re.IGNORECASE)
_LABELLED_ROMAN = re.compile(
    r"\b(?:cap[i\u00ed]tulo|parte|livro|volume|tomo|se[c\u00e7][a\u00e3]o|nota)\s+"
    r"(?P<value>[MDCLXVI]+)\b",
    re.IGNORECASE,
)
_DOUBLE_FULL_STOP = re.compile(r"(?<!\.)\.\.(?!\.)")
_ADJACENT_PUNCTUATION = re.compile(r"(?:[!?]\.|[,;:][.,;:]|,,)")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+[,.!?;:]")
_COMMON_ABBREVIATION = re.compile(
    r"\b(?:[A-Za-zÀ-ÖØ-öø-ÿ]\.){2,}|\b(?:Sr|Sra|Srta|Dr|Dra|Prof|Profa|"
    r"etc|aprox|p|pp|vol|cap)\.",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(
    r"\b\d{1,2}(?:[/-]\d{1,2}){1,2}\b|\b\d{1,2}:\d{2}\b"
)
_RAW_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?!\w)")
_SPOKEN_SYMBOL = re.compile(r"R\$|US\$|€|£|%|º|ª|§|&|@")
_UPPERCASE_TOKEN = re.compile(r"\b[A-Z\u00c0-\u00d6\u00d8-\u00de]{2,}\b")
_CORRUPTED_SENTENCE_SPLIT = re.compile(
    r"(?=\b(?P<first>[A-Za-zÀ-ÖØ-öø-ÿ]{1,12})\.\s+"
    r"(?P<next>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ]+))"
)
_CORRUPTED_SPLIT_LEADS = {
    "aí",
    "assim",
    "e",
    "então",
    "logo",
    "mas",
    "ora",
    "pois",
    "porém",
}
_SPEECH_PUNCTUATION = set(".,;:!?…")
_PAGE_FILE = re.compile(r"^page-(?P<logical_page>\d+)\.txt$")
_HEADING_LABEL = re.compile(
    r"^(?:cap[i\u00ed]tulo|parte|livro|volume|tomo|se[c\u00e7][a\u00e3]o)\b",
    re.IGNORECASE,
)
_NOTE_LABEL = re.compile(r"^(?:nota(?:\s+do\s+(?:autor|editor))?|obs(?:erva[cç][aã]o)?)\b", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^(?:[•*+]|(?:\d+|[A-Za-z])[\).])\s+")
_QUOTATION_MARKER = re.compile(r'^(?:>|\u201c|["\'])')

_ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}
_ROMAN_TABLE = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)
_PT_BR_UNITS = {
    0: "zero",
    1: "um",
    2: "dois",
    3: "três",
    4: "quatro",
    5: "cinco",
    6: "seis",
    7: "sete",
    8: "oito",
    9: "nove",
    10: "dez",
    11: "onze",
    12: "doze",
    13: "treze",
    14: "quatorze",
    15: "quinze",
    16: "dezesseis",
    17: "dezessete",
    18: "dezoito",
    19: "dezenove",
}
_PT_BR_TENS = {
    20: "vinte",
    30: "trinta",
    40: "quarenta",
    50: "cinquenta",
    60: "sessenta",
    70: "setenta",
    80: "oitenta",
    90: "noventa",
}
_PT_BR_HUNDREDS = {
    100: "cento",
    200: "duzentos",
    300: "trezentos",
    400: "quatrocentos",
    500: "quinhentos",
    600: "seiscentos",
    700: "setecentos",
    800: "oitocentos",
    900: "novecentos",
}


@dataclass(frozen=True)
class QualityFinding:
    id: str
    kind: str
    severity: str
    line_number: int
    column: int
    locutor_span: str
    context: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(normalize("NFC", value).split())


def roman_to_int(value: str) -> int | None:
    token = value.upper()
    if not token or any(letter not in _ROMAN_VALUES for letter in token):
        return None
    total = 0
    previous = 0
    for letter in reversed(token):
        current = _ROMAN_VALUES[letter]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    if total <= 0 or int_to_roman(total) != token:
        return None
    return total


def int_to_roman(value: int) -> str:
    if not 0 < value < 4000:
        raise ValueError("Roman numeral values must be between 1 and 3999.")
    remaining = value
    parts: list[str] = []
    for amount, symbol in _ROMAN_TABLE:
        repetitions, remaining = divmod(remaining, amount)
        parts.append(symbol * repetitions)
    return "".join(parts)


def integer_to_pt_br(value: int) -> str:
    if not 0 <= value < 4000:
        raise ValueError("Portuguese number values must be between 0 and 3999.")
    if value < 20:
        return _PT_BR_UNITS[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        prefix = _PT_BR_TENS[tens * 10]
        return prefix if not remainder else f"{prefix} e {_PT_BR_UNITS[remainder]}"
    if value < 1000:
        if value == 100:
            return "cem"
        hundreds, remainder = divmod(value, 100)
        prefix = _PT_BR_HUNDREDS[hundreds * 100]
        return prefix if not remainder else f"{prefix} e {integer_to_pt_br(remainder)}"
    thousands, remainder = divmod(value, 1000)
    prefix = "mil" if thousands == 1 else f"{integer_to_pt_br(thousands)} mil"
    return prefix if not remainder else f"{prefix} e {integer_to_pt_br(remainder)}"


def roman_to_pt_br(value: str) -> str | None:
    integer = roman_to_int(value)
    return integer_to_pt_br(integer) if integer is not None else None


def _finding(
    kind: str,
    severity: str,
    line_number: int,
    column: int,
    locutor_span: str,
    context: str,
) -> QualityFinding:
    return QualityFinding(
        id=f"{kind}:L{line_number}:C{column}",
        kind=kind,
        severity=severity,
        line_number=line_number,
        column=column,
        locutor_span=locutor_span,
        context=context,
    )


def _matches(
    pattern: re.Pattern[str],
    kind: str,
    severity: str,
    line_number: int,
    text: str,
    excluded_spans: tuple[tuple[int, int], ...] = (),
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    for match in pattern.finditer(text):
        if any(
            match.start() < excluded_end and excluded_start < match.end()
            for excluded_start, excluded_end in excluded_spans
        ):
            continue
        findings.append(
            _finding(kind, severity, line_number, match.start() + 1, match.group(0), text)
        )
    return findings


def _ends_spoken_locution(line: str) -> bool:
    return line.rstrip().endswith((".", "!", "?", "…", ":", ";", "”", "»"))


def audit_text(
    text: str,
    intentional_continuation_lines: set[int] | None = None,
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    intentional_continuation_lines = intentional_continuation_lines or set()
    normalized = normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    previous_line = ""
    for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
        line = " ".join(raw_line.split())
        if not line:
            continue

        heading = _ROMAN_HEADING.fullmatch(line)
        if heading is not None and roman_to_int(heading.group("value")) is not None:
            findings.append(
                _finding("roman_heading", "blocking", line_number, 1, line, line)
            )

        for match in _LABELLED_ROMAN.finditer(line):
            if roman_to_int(match.group("value")) is not None:
                findings.append(
                    _finding(
                        "labelled_roman_numeral",
                        "blocking",
                        line_number,
                        match.start() + 1,
                        match.group(0),
                        line,
                    )
                )

        findings.extend(
            _matches(
                _DOUBLE_FULL_STOP,
                "punctuation_cluster",
                "review",
                line_number,
                line,
            )
        )
        findings.extend(
            _matches(
                _ADJACENT_PUNCTUATION,
                "punctuation_cluster",
                "review",
                line_number,
                line,
            )
        )
        findings.extend(
            _matches(
                _SPACE_BEFORE_PUNCTUATION,
                "space_before_punctuation",
                "review",
                line_number,
                line,
            )
        )
        date_matches = tuple((match.start(), match.end()) for match in _DATE_OR_TIME.finditer(line))
        findings.extend(
            _matches(
                _DATE_OR_TIME,
                "date_or_time",
                "review",
                line_number,
                line,
            )
        )
        findings.extend(
            _matches(
                _RAW_NUMBER,
                "raw_number",
                "review",
                line_number,
                line,
                date_matches,
            )
        )
        findings.extend(
            _matches(
                _SPOKEN_SYMBOL,
                "spoken_symbol",
                "review",
                line_number,
                line,
            )
        )
        findings.extend(
            _matches(
                _COMMON_ABBREVIATION,
                "abbreviation",
                "review",
                line_number,
                line,
            )
        )
        if (
            previous_line
            and line_number not in intentional_continuation_lines
            and not _ends_spoken_locution(previous_line)
            and line[0].islower()
        ):
            findings.append(
                _finding("line_boundary", "review", line_number, 1, line, line)
            )
        if line_number not in intentional_continuation_lines and line[0].islower():
            findings.append(
                _finding("lowercase_locution", "review", line_number, 1, line[0], line)
            )
        for match in _UPPERCASE_TOKEN.finditer(line):
            findings.append(
                _finding(
                    "uppercase_token",
                    "review",
                    line_number,
                    match.start() + 1,
                    match.group(0),
                    line,
                )
            )
        previous_line = line

    return findings


def _load_json_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _selected_output(
    book_root: Path,
    input_file: Path,
    narrator_changes: dict,
) -> dict | None:
    outputs = narrator_changes.get("outputs")
    if not isinstance(outputs, list):
        return None
    text_root = book_root / "text"
    for output in outputs:
        if not isinstance(output, dict):
            continue
        locutor_path = resolve_under(
            text_root,
            output.get("locutor_file"),
            (Path("locutor"),),
        )
        if locutor_path == input_file.resolve():
            return output
    return None


def _ledger_records(book_root: Path, narrator_changes: dict) -> dict[str, dict]:
    ledger_name = (
        "translation-ledger.json"
        if narrator_changes.get("base_edition") == "translated-pt-br"
        else "text-ledger.json"
    )
    ledger = _load_json_object(book_root / "metadata" / ledger_name)
    outputs = ledger.get("chapter_outputs") if isinstance(ledger, dict) else None
    if not isinstance(outputs, list):
        return {}
    return {
        entry.get("id"): entry
        for entry in outputs
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _base_texts_for_output(
    book_root: Path,
    narrator_changes: dict,
    selected_output: dict,
) -> dict[str, str]:
    records = _ledger_records(book_root, narrator_changes)
    text_root = book_root / "text"
    base_texts: dict[str, str] = {}
    base_outputs = selected_output.get("base_outputs")
    if not isinstance(base_outputs, list):
        return base_texts
    for base_output in base_outputs:
        if not isinstance(base_output, dict) or not isinstance(base_output.get("id"), str):
            continue
        base_id = base_output["id"]
        record = records.get(base_id)
        if not isinstance(record, dict):
            continue
        base_file = (
            record.get("translation_file")
            if "translation_file" in record
            else record.get("source_file")
        )
        base_path = resolve_under(
            text_root,
            base_file,
            (Path("source"), Path("translation") / "pt-BR"),
        )
        if base_path is not None and base_path.is_file():
            base_texts[base_id] = base_path.read_text(encoding="utf-8")
    return base_texts


def _selected_changes(narrator_changes: dict, selected_output: dict) -> list[dict]:
    output_id = selected_output.get("id")
    base_ids = {
        entry.get("id")
        for entry in selected_output.get("base_outputs", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    changes = narrator_changes.get("changes")
    if not isinstance(changes, list) or not isinstance(output_id, str):
        return []
    return [
        change
        for change in changes
        if isinstance(change, dict)
        and change.get("output_id") == output_id
        and change.get("base_output_id") in base_ids
    ]


def _normalized_document(
    lines: list[str],
) -> tuple[str, list[tuple[int, int]], list[str]]:
    normalized_lines = [" ".join(raw_line.split()) for raw_line in lines]
    characters: list[str] = []
    positions: list[tuple[int, int]] = []
    needs_space = False
    for line_number, line in enumerate(normalized_lines, start=1):
        for column, character in enumerate(line, start=1):
            if character.isspace():
                needs_space = bool(characters)
                continue
            if needs_space:
                characters.append(" ")
                positions.append((line_number, column))
                needs_space = False
            characters.append(character)
            positions.append((line_number, column))
        if line:
            needs_space = True
    return "".join(characters), positions, normalized_lines


def _line_position_for_span(
    lines: list[str],
    span: str,
    offset: int = 0,
) -> tuple[int, int, str] | None:
    normalized_span = normalized_text(span)
    if not normalized_span:
        return None
    document, positions, normalized_lines = _normalized_document(lines)
    start = document.find(normalized_span)
    if start < 0:
        return None
    target = min(start + max(offset, 0), start + len(normalized_span) - 1)
    if target >= len(positions):
        return None
    line_number, column = positions[target]
    return line_number, column, normalized_lines[line_number - 1]


def _change_context(base_span: str, locutor_context: str) -> str:
    return f"base: {base_span} | locutor: {locutor_context}"


def _punctuation_delta(base_span: str, locutor_span: str) -> tuple[str, int] | None:
    for tag, _base_start, _base_end, locutor_start, locutor_end in SequenceMatcher(
        None, base_span, locutor_span
    ).get_opcodes():
        if tag == "equal":
            continue
        for offset, character in enumerate(locutor_span[locutor_start:locutor_end]):
            if character in _SPEECH_PUNCTUATION:
                return character, locutor_start + offset
    return None


def _audit_change_findings(
    lines: list[str],
    changes: list[dict],
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    seen: set[str] = set()
    for change in changes:
        base_span = normalized_text(str(change.get("base_span") or ""))
        locutor_span = normalized_text(str(change.get("locutor_span") or ""))
        if not base_span or not locutor_span:
            continue
        position = _line_position_for_span(lines, locutor_span)
        if position is None:
            continue
        line_number, column, line = position

        punctuation = _punctuation_delta(base_span, locutor_span)
        if punctuation is not None:
            punctuation_mark, punctuation_offset = punctuation
            punctuation_position = _line_position_for_span(
                lines,
                locutor_span,
                punctuation_offset,
            )
            if punctuation_position is None:
                punctuation_position = (line_number, column, line)
            finding = _finding(
                "introduced_punctuation",
                "review",
                punctuation_position[0],
                punctuation_position[1],
                punctuation_mark,
                _change_context(base_span, punctuation_position[2]),
            )
            if finding.id not in seen:
                findings.append(finding)
                seen.add(finding.id)

        for match in _CORRUPTED_SENTENCE_SPLIT.finditer(locutor_span):
            if match.group("first").casefold() not in _CORRUPTED_SPLIT_LEADS:
                continue
            base_word_pair = re.compile(
                rf"\b{re.escape(match.group('first'))}(?P<separator>\W+)"
                rf"{re.escape(match.group('next'))}\b",
                re.IGNORECASE,
            )
            base_match = base_word_pair.search(base_span)
            if base_match is None or any(
                mark in base_match.group("separator") for mark in ".!?…"
            ):
                continue
            split_span = f"{match.group('first')}. {match.group('next')}"
            split_position = _line_position_for_span(
                lines,
                locutor_span,
                match.start(),
            )
            if split_position is None:
                split_position = (line_number, column, line)
            finding = _finding(
                "corrupted_phrase_split",
                "blocking",
                split_position[0],
                split_position[1],
                split_span,
                _change_context(base_span, split_position[2]),
            )
            if finding.id not in seen:
                findings.append(finding)
                seen.add(finding.id)

        if change.get("kind") == "pronunciation":
            finding = _finding(
                "pronunciation_sensitive_term",
                "review",
                line_number,
                column,
                locutor_span,
                _change_context(base_span, line),
            )
            if finding.id not in seen:
                findings.append(finding)
                seen.add(finding.id)
    return findings


def _base_has_same_line_boundary(base_texts: dict[str, str], previous: str, line: str) -> bool:
    for base_text in base_texts.values():
        base_lines = [normalized_text(value) for value in base_text.splitlines()]
        for index in range(1, len(base_lines)):
            if base_lines[index - 1] == previous and base_lines[index] == line:
                return True
    return False


def _audit_mechanical_lowercase_starts(
    lines: list[str],
    base_texts: dict[str, str],
    intentional_continuation_lines: set[int],
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    base_text = normalized_text(" ".join(base_texts.values()))
    if not base_text:
        return findings
    normalized_lines = [normalized_text(line) for line in lines]
    for index, line in enumerate(normalized_lines):
        line_number = index + 1
        if (
            not line
            or line_number in intentional_continuation_lines
            or not line[0].islower()
            or index == 0
        ):
            continue
        previous = normalized_lines[index - 1]
        if not previous:
            continue
        combined = normalized_text(f"{previous} {line}")
        if combined in base_text and not _base_has_same_line_boundary(
            base_texts, previous, line
        ):
            findings.append(
                _finding(
                    "mechanical_lowercase_start",
                    "blocking",
                    line_number,
                    1,
                    line[0],
                    line,
                )
            )
    return findings


def _merge_findings(findings: list[QualityFinding], extra: list[QualityFinding]) -> list[QualityFinding]:
    merged: list[QualityFinding] = []
    seen: set[str] = set()
    for finding in [*findings, *extra]:
        if finding.id in seen:
            continue
        merged.append(finding)
        seen.add(finding.id)
    return merged


def audit_narrator_quality(
    book_root: Path,
    input_file: Path,
    narrator_changes_path: Path | None = None,
    intentional_continuation_lines: set[int] | None = None,
) -> list[QualityFinding]:
    """Audit narrator text plus evidence-backed risks from narrator lineage metadata."""
    text = input_file.read_text(encoding="utf-8")
    continuation_lines = intentional_continuation_lines or set()
    findings = audit_text(text, continuation_lines)
    narrator_changes = _load_json_object(
        narrator_changes_path or book_root / "metadata" / "narrator-changes.json"
    )
    if narrator_changes is None:
        return findings
    selected_output = _selected_output(book_root, input_file, narrator_changes)
    if selected_output is None:
        return findings
    base_texts = _base_texts_for_output(book_root, narrator_changes, selected_output)
    lines = normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
    findings.extend(
        _audit_change_findings(lines, _selected_changes(narrator_changes, selected_output))
    )
    findings.extend(
        _audit_mechanical_lowercase_starts(lines, base_texts, continuation_lines)
    )
    return findings


def _is_verse_candidate(line: str) -> bool:
    words = line.split()
    return (
        bool(line)
        and len(line) <= 80
        and 2 <= len(words) <= 14
        and not line.startswith(("—", "-", ">", '"', "'", "\u201c"))
        and not _LIST_MARKER.match(line)
    )


def _is_verse_line(lines: list[str], index: int) -> bool:
    line = lines[index].strip()
    if not _is_verse_candidate(line):
        return False
    adjacent = [
        lines[neighbor].strip()
        for neighbor in (index - 1, index + 1)
        if 0 <= neighbor < len(lines)
    ]
    return any(_is_verse_candidate(candidate) for candidate in adjacent)


def classify_locution_line(lines: list[str], index: int) -> str:
    """Return a conservative speech category for a narrator line."""
    line = lines[index].strip()
    if not line:
        return "excluded"
    if _ROMAN_HEADING.fullmatch(line) or _HEADING_LABEL.match(line):
        return "heading"
    if line.startswith(("—", "- ")):
        return "dialogue"
    if _QUOTATION_MARKER.match(line):
        return "quotation"
    if _NOTE_LABEL.match(line):
        return "note"
    if _LIST_MARKER.match(line):
        return "list"
    if _is_verse_line(lines, index):
        return "verse"
    return "prose"


def classify_finding(finding: QualityFinding, lines: list[str]) -> str:
    index = finding.line_number - 1
    if not 0 <= index < len(lines):
        return "prose"
    return classify_locution_line(lines, index)


def finding_dict(finding: QualityFinding) -> dict:
    return asdict(finding)


def relative_to_text(path: Path, book_root: Path) -> str:
    try:
        return path.resolve().relative_to((book_root / "text").resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def locutor_page_texts(book_root: Path) -> dict[int, str]:
    pages_root = book_root / "text" / "locutor" / "pages"
    page_texts: dict[int, str] = {}
    if not pages_root.is_dir():
        return page_texts
    for page_path in sorted(pages_root.glob("page-*.txt")):
        match = _PAGE_FILE.fullmatch(page_path.name)
        if match is None:
            continue
        page_texts[int(match.group("logical_page"))] = normalized_text(
            page_path.read_text(encoding="utf-8")
        )
    return page_texts


def logical_pages_for_finding(
    input_file: Path,
    finding: QualityFinding,
    page_texts: dict[int, str],
    narration_pages_by_line: dict[int, list[int]] | None = None,
) -> list[int]:
    if narration_pages_by_line is not None and finding.line_number in narration_pages_by_line:
        return narration_pages_by_line[finding.line_number]
    raw_lines = input_file.read_text(encoding="utf-8").splitlines()
    if finding.line_number > len(raw_lines):
        return []
    line = normalized_text(raw_lines[finding.line_number - 1])
    if not line:
        return []
    return [
        logical_page
        for logical_page, page_text in page_texts.items()
        if line in page_text
    ]


def narration_plan_pages(book_root: Path, input_file: Path) -> dict[int, list[int]]:
    plan_path = book_root / "metadata" / "narration-plan.json"
    if not plan_path.is_file():
        return {}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(plan, dict):
        return {}
    try:
        expected_input = input_file.resolve().relative_to(book_root.resolve()).as_posix()
    except ValueError:
        return {}
    if plan.get("input_file") != expected_input:
        return {}
    segments = plan.get("segments")
    if not isinstance(segments, list):
        return {}
    pages_by_line: dict[int, list[int]] = {}
    for entry in segments:
        if not isinstance(entry, dict) or not isinstance(entry.get("locutor_line"), int):
            continue
        source = entry.get("source")
        pages = source.get("logical_pages") if isinstance(source, dict) else None
        if isinstance(pages, list) and all(isinstance(page, int) and page > 0 for page in pages):
            pages_by_line[entry["locutor_line"]] = pages
    return pages_by_line


def narration_plan_continuation_lines(book_root: Path, input_file: Path) -> set[int]:
    plan_path = book_root / "metadata" / "narration-plan.json"
    if not plan_path.is_file():
        return set()
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(plan, dict):
        return set()
    try:
        expected_input = input_file.resolve().relative_to(book_root.resolve()).as_posix()
    except ValueError:
        return set()
    if plan.get("input_file") != expected_input:
        return set()
    segments = plan.get("segments")
    if not isinstance(segments, list):
        return set()
    continuation_lines: set[int] = set()
    for current in segments:
        if not isinstance(current, dict):
            continue
        line_number = current.get("locutor_line")
        if isinstance(line_number, int) and current.get("role") == "attribution":
            continuation_lines.add(line_number)
    return continuation_lines


def narrator_output_pages(
    book_root: Path,
    input_file: Path,
    narrator_changes_path: Path,
) -> tuple[list[str], set[int]]:
    try:
        narrator_changes = json.loads(narrator_changes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"Cannot read narrator changes {narrator_changes_path}: {error}"], set()
    if not isinstance(narrator_changes, dict):
        return ["narrator changes must be an object"], set()
    text_root = book_root / "text"
    outputs = narrator_changes.get("outputs")
    if not isinstance(outputs, list):
        return ["narrator changes outputs must be an array"], set()
    selected: dict | None = None
    for output in outputs:
        if not isinstance(output, dict):
            continue
        locutor_path = resolve_under(
            text_root,
            output.get("locutor_file"),
            (Path("locutor"),),
        )
        if locutor_path == input_file.resolve():
            selected = output
            break
    if selected is None:
        return ["narrator input is not declared by narrator changes outputs"], set()
    base_outputs = selected.get("base_outputs")
    if not isinstance(base_outputs, list) or not base_outputs:
        return ["selected narrator output must declare base_outputs"], set()
    base_ids = {
        entry.get("id")
        for entry in base_outputs
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if not base_ids:
        return ["selected narrator output base_outputs must contain ids"], set()
    base_edition = narrator_changes.get("base_edition")
    ledger_name = (
        "translation-ledger.json"
        if base_edition == "translated-pt-br"
        else "text-ledger.json"
    )
    ledger_path = book_root / "metadata" / ledger_name
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"Cannot read {ledger_name}: {error}"], set()
    if not isinstance(ledger, dict) or not isinstance(ledger.get("chapter_outputs"), list):
        return [f"{ledger_name} must declare chapter_outputs"], set()
    records = {
        entry.get("id"): entry
        for entry in ledger["chapter_outputs"]
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    errors: list[str] = []
    logical_pages: set[int] = set()
    for base_id in base_ids:
        record = records.get(base_id)
        if not isinstance(record, dict):
            errors.append(
                f"selected narrator output base_output {base_id!r} is absent from {ledger_name}"
            )
            continue
        source_pages = record.get("source_pages")
        if not isinstance(source_pages, list):
            errors.append(f"{ledger_name} output {base_id!r} must declare source_pages")
            continue
        for page in source_pages:
            logical_page = page.get("logical_page") if isinstance(page, dict) else None
            if not isinstance(logical_page, int) or logical_page <= 0:
                errors.append(
                    f"{ledger_name} output {base_id!r} has an invalid logical page"
                )
                continue
            logical_pages.add(logical_page)
    if not logical_pages:
        errors.append("selected narrator output has no logical pages")
    return errors, logical_pages


def draft_review(
    book_root: Path,
    input_file: Path,
    findings: list[QualityFinding],
    narrator_changes_path: Path | None = None,
) -> dict:
    narrator_changes = narrator_changes_path or (
        book_root / "metadata" / "narrator-changes.json"
    )
    scope_errors, output_pages = narrator_output_pages(
        book_root,
        input_file,
        narrator_changes,
    )
    if scope_errors:
        raise RuntimeError("; ".join(scope_errors))
    findings = _merge_findings(
        findings,
        [
            finding
            for finding in audit_narrator_quality(
                book_root,
                input_file,
                narrator_changes,
                narration_plan_continuation_lines(book_root, input_file),
            )
            if finding.kind in EVIDENCE_BACKED_FINDING_KINDS
        ],
    )
    page_texts = locutor_page_texts(book_root)
    narration_pages_by_line = narration_plan_pages(book_root, input_file)
    lines = input_file.read_text(encoding="utf-8").splitlines()
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "profile": QUALITY_PROFILE,
        "status": "needs-review",
        "reviewed_by": "",
        "output_file": relative_to_text(input_file, book_root),
        "output_sha256": sha256_file(input_file),
        "narrator_changes_sha256": (
            sha256_file(narrator_changes) if narrator_changes.is_file() else ""
        ),
        "review_scope": {
            "categories": [
                "heading",
                "prose",
                "dialogue",
                "quotation",
                "verse",
                "note",
                "list",
            ],
            "logical_pages": sorted(output_pages),
        },
        "findings": [
            {
                **finding_dict(finding),
                "category": "",
                "suggested_category": classify_finding(finding, lines),
                "status": "unresolved",
                "logical_pages": sorted(
                    set(
                        logical_pages_for_finding(input_file, finding, page_texts)
                        if not narration_pages_by_line
                        else logical_pages_for_finding(
                            input_file,
                            finding,
                            page_texts,
                            narration_pages_by_line,
                        )
                    ).intersection(output_pages)
                ),
                "reason": "",
                "reviewed_by": "",
            }
            for finding in findings
        ],
        "pronunciation_review": {
            "status": "needs-review",
            "reviewed_by": "",
            "entries": [],
        },
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a deterministic narrator-quality review draft for a locutor TXT."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--input-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--narrator-changes", type=Path)
    args = parser.parse_args()

    book_root = resolve_book_paths(args.book_root).assembly_root
    input_file = args.input_file.expanduser().resolve()
    output = args.output.expanduser().resolve()
    narrator_changes = (
        args.narrator_changes.expanduser().resolve()
        if args.narrator_changes
        else book_root / "metadata" / "narrator-changes.json"
    )
    if not input_file.is_file():
        raise SystemExit(f"Narrator input is missing: {input_file}")
    try:
        input_file.relative_to((book_root / "text" / "locutor").resolve())
    except ValueError:
        raise SystemExit("Narrator input must be under book-root/text/locutor.")
    try:
        output.relative_to((book_root / "metadata").resolve())
    except ValueError:
        raise SystemExit("Narrator review output must be under book-root/metadata.")

    findings = audit_narrator_quality(book_root, input_file, narrator_changes)
    try:
        review = draft_review(book_root, input_file, findings, narrator_changes)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    write_json(output, review)
    print(f"Created narrator-quality draft with {len(findings)} finding(s): {output}")


if __name__ == "__main__":
    main()
