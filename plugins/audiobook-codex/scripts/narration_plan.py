from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from unicodedata import normalize

from chatterbox_text import (
    NarratorSegment,
    NarratorTextError,
    prepare_chatterbox_segments,
)
from path_safety import resolve_under


SCHEMA_VERSION = "1.0"
POLICY_NAME = "paragraph-pauses-v1"
PAUSE_SECONDS = {
    "continuation": 0.06,
    "sentence": 0.17,
    "paragraph": 0.42,
    "heading": 1.0,
    "end": 0.0,
}
_WORD = re.compile(r"\w+(?:['’]\w+)?", re.UNICODE)
_HEADING_LABEL = re.compile(
    r"^(?:cap[ií]tulo|parte|livro|volume|tomo|se[cç][aã]o|nota)\b",
    re.IGNORECASE,
)
_DIALOGUE_START = re.compile(r"^\s*[-—]\s*[A-ZÀ-ÖØ-Þ“\"']")
_NOTE_START = re.compile(r"^\s*(?:\d+|[*†‡])\s+[A-ZÀ-ÖØ-Þ]")
_TERMINAL_PUNCTUATION = re.compile(r"[.!?…][\"”’)]*$")
_BIBLIOGRAPHIC_NOTE_END = re.compile(r"(?:\d|p{1,2}\.?\s*\d+)\s*$", re.IGNORECASE)
_ATTRIBUTION_AFTER_PUNCTUATION = re.compile(
    r"(?<=[,!?])\s+(?="
    r"(?:afirmou|avisou|considerou|continuou|disse|exclamou|objetou|"
    r"observou|pediu|perguntou(?:-me)?|protestou|replicou|respondeu)\b"
    r")",
    re.IGNORECASE,
)
_ATTRIBUTION_AFTER_VOCATIVE = re.compile(
    r"(?<=\bcaboclo)\s+(?=avisou\b)",
    re.IGNORECASE,
)
SEMANTIC_ROLES = {
    "paragraph",
    "dialogue",
    "attribution",
    "note",
    "verse",
    "heading",
}
_PAUSE_KINDS = frozenset(PAUSE_SECONDS)
_OPENING_DELIMITERS = "“\"'([{—-"
_CLOSING_DELIMITERS = "”\"')]}."
_COMMON_ABBREVIATIONS = {
    "aprox",
    "art",
    "av",
    "cap",
    "cel",
    "dra",
    "dr",
    "ed",
    "etc",
    "ex",
    "fig",
    "num",
    "p",
    "pag",
    "pág",
    "pp",
    "prof",
    "profa",
    "sr",
    "sra",
    "tel",
    "vol",
}
_UNIT_BOUNDARY_END = re.compile(r"[.!?…;,:)\]”\"’]$")
_NOTE_CLOSURE = re.compile(
    r"^(?P<closure>Fim da nota (?:\d+|um|dois|três|quatro|cinco|seis|sete|"
    r"oito|nove|dez)\.)(?:\s+|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceUnit:
    text: str
    role: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(normalize("NFC", value).split())


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _body_units(lines: list[str]) -> list[SourceUnit]:
    if not lines:
        return []
    joined = " ".join(lines)
    letter_count = sum(character.isalpha() for character in joined)
    uppercase = letter_count > 2 and joined.upper() == joined
    if (
        len(lines) >= 3
        and max(len(line) for line in lines) <= 64
        and sum(len(line) for line in lines) / len(lines) <= 46
        and not uppercase
        and not any(line.startswith(("“", '"', "'")) for line in lines)
    ):
        return [SourceUnit(line, "verse") for line in lines]
    if joined.startswith(("“", '"', "'")):
        role = "dialogue"
    else:
        role = "heading" if uppercase or _HEADING_LABEL.match(joined) else "paragraph"
    return [SourceUnit(joined, role)]


def _dialogue_units(line: str) -> list[SourceUnit]:
    boundary = None
    for pattern in (_ATTRIBUTION_AFTER_PUNCTUATION, _ATTRIBUTION_AFTER_VOCATIVE):
        match = pattern.search(line)
        if match is not None and (boundary is None or match.end() < boundary):
            boundary = match.end()
    if boundary is None:
        return [SourceUnit(line, "dialogue")]
    dialogue = line[:boundary].strip()
    remainder = line[boundary:].strip()
    terminal = re.search(r"[.!?…][\"”’)]?(?:\s+|$)", remainder)
    if terminal is None:
        attribution = remainder
        trailing_dialogue = ""
    else:
        attribution = remainder[: terminal.end()].strip()
        trailing_dialogue = remainder[terminal.end() :].strip()
    if not dialogue or not attribution:
        return [SourceUnit(line, "dialogue")]
    result = [
        SourceUnit(dialogue, "dialogue"),
        SourceUnit(attribution, "attribution"),
    ]
    if trailing_dialogue:
        result.append(SourceUnit(trailing_dialogue, "dialogue"))
    return result


def _note_wraps(first_line: str) -> bool:
    if _TERMINAL_PUNCTUATION.search(first_line) or _BIBLIOGRAPHIC_NOTE_END.search(first_line):
        return False
    return len(first_line) >= 85


def _source_units(text: str) -> list[SourceUnit]:
    groups: list[list[str]] = []
    current: list[str] = []
    for raw_line in normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if line:
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    units: list[SourceUnit] = []
    for group in groups:
        body: list[str] = []
        note: list[str] = []
        for line_index, line in enumerate(group):
            if note:
                note.append(line)
                if _TERMINAL_PUNCTUATION.search(line):
                    units.append(SourceUnit(" ".join(note), "note"))
                    note = []
                continue
            if _DIALOGUE_START.match(line):
                units.extend(_body_units(body))
                body = []
                units.extend(_dialogue_units(line))
            elif (
                not body
                and units
                and units[-1].role in {"dialogue", "attribution"}
                and re.match(r"^[a-zà-öø-ÿ]", line)
            ):
                previous = units[-1]
                units[-1] = SourceUnit(f"{previous.text} {line}", previous.role)
            elif _NOTE_START.match(line):
                units.extend(_body_units(body))
                body = []
                if _note_wraps(line) and line_index + 1 < len(group):
                    note = [line]
                else:
                    units.append(SourceUnit(line, "note"))
            else:
                body.append(line)
        if note:
            units.append(SourceUnit(" ".join(note), "note"))
        units.extend(_body_units(body))
    return units


def _word_matches(text: str) -> list[re.Match[str]]:
    return list(_WORD.finditer(text))


def _mapped_boundaries(source: str, locutor: str, units: list[SourceUnit]) -> list[int]:
    source_words = [match.group(0).casefold() for match in _word_matches(source)]
    locutor_words = [match.group(0).casefold() for match in _word_matches(locutor)]
    matcher = SequenceMatcher(None, source_words, locutor_words, autojunk=False)
    boundaries: list[int] = [0]
    source_offset = 0
    for unit in units:
        source_offset += len(_word_matches(unit.text))
        boundaries.append(source_offset)

    mapped: list[int] = []
    opcodes = matcher.get_opcodes()
    for boundary in boundaries:
        position = len(locutor_words)
        for tag, source_start, source_end, locutor_start, locutor_end in opcodes:
            if source_start <= boundary <= source_end:
                if tag == "equal":
                    position = locutor_start + (boundary - source_start)
                else:
                    # A source replacement such as "1925" -> "mil novecentos e
                    # vinte e cinco" is indivisible for speech. Mapping a source
                    # boundary proportionally can bisect the spoken replacement.
                    position = locutor_end
                break
        mapped.append(position)

    mapped[0] = 0
    mapped[-1] = len(locutor_words)
    for index in range(1, len(mapped)):
        mapped[index] = max(mapped[index], mapped[index - 1])
    return mapped


def _locutor_units(source: str, locutor: str) -> list[tuple[SourceUnit, str]]:
    source_units = _source_units(source)
    if not source_units:
        raise RuntimeError("Source chapter has no semantic units.")
    normalized_locutor = normalized_text(locutor)
    source_text = " ".join(unit.text for unit in source_units)
    locutor_matches = _word_matches(normalized_locutor)
    boundaries = _mapped_boundaries(source_text, normalized_locutor, source_units)
    if not locutor_matches:
        raise RuntimeError("Locutor chapter has no spoken words.")

    starts: list[int] = []
    for word_index in boundaries[:-1]:
        if word_index >= len(locutor_matches):
            raise RuntimeError(
                "Cannot align source units; the approved locutor text ended before "
                "the source semantic boundary."
            )
        start = locutor_matches[word_index].start()
        while start > 0 and normalized_locutor[start - 1].isspace():
            start -= 1
        while start > 0:
            previous = normalized_locutor[start - 1]
            if previous in _OPENING_DELIMITERS:
                start -= 1
            else:
                break
        starts.append(start)

    result: list[tuple[SourceUnit, str]] = []
    for index, unit in enumerate(source_units):
        start_word = boundaries[index]
        end_word = boundaries[index + 1]
        if end_word <= start_word:
            raise RuntimeError(
                f"Cannot align source unit {index + 1}; the approved locutor text diverged."
            )
        start = starts[index]
        end = (
            starts[index + 1]
            if index + 1 < len(starts)
            else len(normalized_locutor)
        )
        spoken = normalized_locutor[start:end].strip()
        if not spoken:
            raise RuntimeError(f"Aligned locutor unit {index + 1} is empty.")
        result.append((unit, spoken))

    for index in range(1, len(result)):
        previous_unit, previous_spoken = result[index - 1]
        current_unit, current_spoken = result[index]
        if previous_unit.role != "note":
            continue
        closure = _NOTE_CLOSURE.match(current_spoken)
        if closure is None:
            continue
        remainder = current_spoken[closure.end() :].strip()
        if not remainder:
            raise RuntimeError("A spoken note closure cannot consume the following source unit.")
        result[index - 1] = (
            previous_unit,
            f"{previous_spoken} {closure.group('closure')}",
        )
        result[index] = (current_unit, remainder)

    reconstructed = normalized_text(" ".join(spoken for _, spoken in result))
    if reconstructed != normalized_locutor:
        raise RuntimeError("Source-to-locutor alignment changed approved spoken text.")
    return result


def _inside_protected_span(text: str, index: int) -> bool:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for character in text[:index]:
        if character in pairs:
            stack.append(pairs[character])
        elif stack and character == stack[-1]:
            stack.pop()
    return bool(stack)


def _looks_like_abbreviation(text: str, period_index: int) -> bool:
    before = text[:period_index]
    match = re.search(r"([A-Za-zÀ-ÖØ-öø-ÿ]+)$", before)
    token = match.group(1) if match is not None else ""
    if token.casefold() in _COMMON_ABBREVIATIONS:
        return True
    if len(token) == 1 and token.isalpha():
        token_start = match.start(1) if match is not None else 0
        if token_start == 0 or before[token_start - 1] not in "-–—'’":
            return True
    if period_index > 0 and period_index + 1 < len(text):
        if text[period_index - 1].isdigit() and text[period_index + 1].isdigit():
            return True
    return False


def _semantic_parts(text: str, punctuation: str) -> list[str]:
    boundaries: list[int] = []
    for index, character in enumerate(text):
        if character not in punctuation or _inside_protected_span(text, index):
            continue
        if character == "." and _looks_like_abbreviation(text, index):
            continue
        next_index = index + 1
        while next_index < len(text) and text[next_index] in _CLOSING_DELIMITERS:
            next_index += 1
        whitespace_start = next_index
        while next_index < len(text) and text[next_index].isspace():
            next_index += 1
        if next_index == whitespace_start or next_index >= len(text):
            continue
        while next_index < len(text) and text[next_index] in _OPENING_DELIMITERS:
            next_index += 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
        if next_index < len(text) and text[next_index].isupper():
            boundaries.append(whitespace_start)
    if not boundaries:
        return [text]
    result: list[str] = []
    start = 0
    for boundary in boundaries:
        result.append(text[start:boundary].strip())
        start = boundary
    result.append(text[start:].strip())
    return [part for part in result if part]


def _ensure_safe_chunk_starts(chunks: list[tuple[str, str]]) -> None:
    for text, _ in chunks[1:]:
        first_letter = next((character for character in text if character.isalpha()), "")
        if first_letter and first_letter.islower():
            raise RuntimeError(
                "Narration segmentation would create a mechanical lowercase start. "
                "Reflow the approved locutor text at a semantic boundary."
            )


def _validate_renderable_segment(text: str, max_chars: int) -> tuple[str, ...]:
    try:
        prepared = prepare_chatterbox_segments(text, max_chars)
    except NarratorTextError as error:
        raise RuntimeError(f"Unsafe narration segment: {error}") from error
    if len(prepared) != 1 or prepared[0].text != text:
        raise RuntimeError("Narration segment safety validation changed approved text.")
    return prepared[0].warnings


def _validate_unit_boundaries(entries: list[dict]) -> None:
    seen_ids: set[str] = set()
    for entry in entries:
        semantic_id = entry.get("id")
        if not isinstance(semantic_id, str) or not semantic_id.strip():
            raise RuntimeError("Narration plan segment id must be non-empty.")
        if semantic_id in seen_ids:
            raise RuntimeError(f"Narration plan segment id is duplicated: {semantic_id!r}.")
        seen_ids.add(semantic_id)
    for previous, current in zip(entries, entries[1:]):
        previous_source = previous.get("source")
        current_source = current.get("source")
        if not isinstance(previous_source, dict) or not isinstance(current_source, dict):
            continue
        if previous_source.get("base_output_id") != current_source.get("base_output_id"):
            continue
        previous_role = previous.get("role")
        current_role = current.get("role")
        if previous_role in {"heading", "verse"}:
            continue
        if (previous_role, current_role) in {
            ("dialogue", "attribution"),
            ("attribution", "dialogue"),
        }:
            continue
        previous_text = str(previous.get("text") or "").rstrip()
        if previous_text and not _UNIT_BOUNDARY_END.search(previous_text):
            raise RuntimeError(
                "Narration plan contains an unsafe lexical boundary between "
                f"segments {previous.get('index')} and {current.get('index')}: "
                f"{previous_text[-48:]!r} / {str(current.get('text') or '')[:48]!r}."
            )


def _split_long_text(text: str, max_chars: int) -> list[tuple[str, str]]:
    if len(text) <= max_chars:
        return [(text, "paragraph")]

    sentences = _semantic_parts(text, ".!?…")
    chunks: list[tuple[str, str]] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append((current, "sentence"))
                current = ""
            chunks.extend(_split_clause(sentence, max_chars))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append((current, "sentence"))
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append((current, "paragraph"))
    if not chunks:
        raise RuntimeError("Cannot split an empty narration unit.")
    result = [
        (chunk, "paragraph" if index == len(chunks) - 1 else pause_kind)
        for index, (chunk, pause_kind) in enumerate(chunks)
    ]
    _ensure_safe_chunk_starts(result)
    return result


def _split_clause(text: str, max_chars: int) -> list[tuple[str, str]]:
    clauses = _semantic_parts(text, ";,:")
    chunks: list[tuple[str, str]] = []
    current = ""
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        candidate = f"{current} {clause}".strip()
        if current and len(candidate) > max_chars:
            if not re.match(r"^(?:[.“\"'—-]\s*)?[A-ZÀ-ÖØ-Þ]", clause):
                raise RuntimeError(
                    "Narration text exceeds the segment limit without a safe semantic boundary. "
                    "Split the approved locutor text before rendering."
                )
            chunks.append((current, "continuation"))
            current = ""
        if len(clause) > max_chars:
            if current:
                chunks.append((current, "continuation"))
                current = ""
            while len(clause) > max_chars:
                candidates = _semantic_parts(clause[: max_chars + 1], ";,:")
                split_at = len(" ".join(candidates[:-1])) if len(candidates) > 1 else 0
                if split_at <= 0:
                    raise RuntimeError(
                        "Narration text exceeds the segment limit without a safe semantic boundary."
                    )
                chunks.append((clause[:split_at].strip(), "continuation"))
                clause = clause[split_at:].strip()
            current = clause
        else:
            current = f"{current} {clause}".strip()
    if current:
        chunks.append((current, "paragraph"))
    _ensure_safe_chunk_starts(chunks)
    return chunks


def _segments_for_unit(unit: SourceUnit, spoken: str, max_chars: int) -> list[tuple[str, str]]:
    if unit.role == "heading":
        chunks = _split_long_text(spoken, max_chars)
        return [(text, "heading" if index == len(chunks) - 1 else pause) for index, (text, pause) in enumerate(chunks)]
    if unit.role == "verse":
        return _split_clause(spoken, max_chars)
    return _split_long_text(spoken, max_chars)


def _chapter_records(book_root: Path, input_file: Path) -> list[dict]:
    text_root = book_root / "text"
    changes = read_json(book_root / "metadata" / "narrator-changes.json", "narrator changes")
    outputs = changes.get("outputs")
    if not isinstance(outputs, list):
        raise RuntimeError("Narrator changes outputs must be an array.")
    selected = next(
        (
            output
            for output in outputs
            if isinstance(output, dict)
            and resolve_under(text_root, output.get("locutor_file"), (Path("locutor"),))
            == input_file.resolve()
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise RuntimeError("Narrator input is not declared by narrator changes.")
    base_outputs = selected.get("base_outputs")
    if not isinstance(base_outputs, list) or not base_outputs:
        raise RuntimeError("Narrator changes output has no base outputs.")

    base_edition = changes.get("base_edition")
    if base_edition == "source":
        ledger_path = book_root / "metadata" / "text-ledger.json"
        ledger_label = "text ledger"
        text_key = "source_file"
        allowed_root = Path("source") / "chapters"
    elif base_edition == "translated-pt-br":
        ledger_path = book_root / "metadata" / "translation-ledger.json"
        ledger_label = "translation ledger"
        text_key = "translation_file"
        allowed_root = Path("translation") / "pt-BR" / "chapters"
    else:
        raise RuntimeError("Narrator changes base_edition must be source or translated-pt-br.")
    ledger = read_json(ledger_path, ledger_label)
    records = {
        record.get("id"): record
        for record in ledger.get("chapter_outputs", [])
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    result: list[dict] = []
    for base in base_outputs:
        if not isinstance(base, dict) or not isinstance(base.get("id"), str):
            raise RuntimeError("Narrator changes base output is invalid.")
        source_record = records.get(base["id"])
        if not isinstance(source_record, dict):
            raise RuntimeError(f"Missing source chapter record {base['id']!r}.")
        source_path = resolve_under(
            text_root,
            source_record.get(text_key),
            (allowed_root,),
        )
        if source_path is None or not source_path.is_file():
            raise RuntimeError(f"Base chapter is unavailable for {base['id']!r}.")
        locutor_path = text_root / "locutor" / "chapters" / source_path.name
        if not locutor_path.is_file():
            raise RuntimeError(f"Locutor chapter is unavailable: {locutor_path}")
        pages = sorted(
            {
                page.get("logical_page")
                for page in source_record.get("source_pages", [])
                if isinstance(page, dict) and isinstance(page.get("logical_page"), int)
            }
        )
        if not pages:
            raise RuntimeError(f"Source chapter {base['id']!r} has no logical pages.")
        result.append(
            {
                "base_output_id": base["id"],
                "source_path": source_path,
                "locutor_path": locutor_path,
                "logical_pages": pages,
                "base_ledger_path": ledger_path,
            }
        )
    return result


def build_narration_plan(book_root: Path, input_file: Path, max_chars: int) -> tuple[str, dict]:
    if not 80 <= max_chars <= 320:
        raise RuntimeError("Narration plan max chars must be between 80 and 320.")
    chapters = _chapter_records(book_root, input_file)
    segments: list[dict] = []
    output_lines: list[str] = []
    for chapter in chapters:
        aligned = _locutor_units(
            chapter["source_path"].read_text(encoding="utf-8"),
            chapter["locutor_path"].read_text(encoding="utf-8"),
        )
        for paragraph_index, (unit, spoken) in enumerate(aligned, start=1):
            for text, pause_kind in _segments_for_unit(unit, spoken, max_chars):
                if len(text) > max_chars:
                    raise RuntimeError("Narration segment exceeds the configured Chatterbox limit.")
                _validate_renderable_segment(text, max_chars)
                line_number = len(output_lines) + 1
                semantic_id = f"{chapter['base_output_id']}-{paragraph_index:04d}-{line_number:04d}"
                output_lines.append(text)
                segments.append(
                    {
                        "id": semantic_id,
                        "index": line_number,
                        "locutor_line": line_number,
                        "text": text,
                        "text_sha256": sha256_text(text),
                        "role": unit.role,
                        "source": {
                            "base_output_id": chapter["base_output_id"],
                            "locutor_chapter": chapter["locutor_path"]
                            .relative_to(book_root / "text")
                            .as_posix(),
                            "paragraph_index": paragraph_index,
                            "logical_pages": chapter["logical_pages"],
                        },
                        "pause_after": {
                            "kind": pause_kind,
                            "seconds": PAUSE_SECONDS[pause_kind],
                        },
                    }
                )
    if not output_lines:
        raise RuntimeError("No narration segments were produced.")
    segments[-1]["pause_after"] = {"kind": "end", "seconds": PAUSE_SECONDS["end"]}
    _validate_unit_boundaries(segments)
    book_text = "\n".join(output_lines) + "\n"
    book_map = book_root / "metadata" / "book-map.json"
    ledger = book_root / "metadata" / "text-ledger.json"
    base_ledger = chapters[0]["base_ledger_path"]
    changes = book_root / "metadata" / "narrator-changes.json"
    review = book_root / "metadata" / "narrator-review.json"
    return book_text, {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "name": POLICY_NAME,
            "max_chars": max_chars,
            "pauses_seconds": PAUSE_SECONDS,
        },
        "input_file": input_file.relative_to(book_root).as_posix(),
        "input_sha256": sha256_text(book_text),
        "book_map_sha256": sha256_file(book_map),
        "text_ledger_sha256": sha256_file(ledger),
        "base_ledger_sha256": sha256_file(base_ledger),
        "narrator_changes_sha256": sha256_file(changes),
        "narrator_review_sha256": sha256_file(review) if review.is_file() else "",
        "segments": segments,
    }


def load_plan_segments(book_root: Path, input_file: Path, plan: dict) -> list[NarratorSegment]:
    expected_input = input_file.relative_to(book_root).as_posix()
    if plan.get("input_file") != expected_input:
        raise RuntimeError("Narration plan input_file does not match the selected narrator input.")
    if plan.get("input_sha256") != sha256_file(input_file):
        raise RuntimeError("Narration plan input_sha256 does not match the selected narrator input.")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Narration plan must contain non-empty segments.")
    _validate_unit_boundaries(segments)
    result: list[NarratorSegment] = []
    input_lines = input_file.read_text(encoding="utf-8").splitlines()
    max_chars = int((plan.get("policy") or {}).get("max_chars") or 0)
    for index, entry in enumerate(segments, start=1):
        if not isinstance(entry, dict):
            raise RuntimeError("Narration plan segment must be an object.")
        if entry.get("index") != index or entry.get("locutor_line") != index:
            raise RuntimeError("Narration plan segments must use sequential indexes and lines.")
        text = entry.get("text")
        if not isinstance(text, str) or index > len(input_lines) or text != input_lines[index - 1]:
            raise RuntimeError("Narration plan segment text does not match the narrator input.")
        pause = entry.get("pause_after")
        source = entry.get("source")
        if (
            not isinstance(pause, dict)
            or not isinstance(pause.get("seconds"), (int, float))
            or not isinstance(source, dict)
            or not isinstance(entry.get("id"), str)
        ):
            raise RuntimeError("Narration plan segment metadata is invalid.")
        pages = source.get("logical_pages")
        if not isinstance(pages, list) or any(not isinstance(page, int) for page in pages):
            raise RuntimeError("Narration plan source logical_pages is invalid.")
        role = entry.get("role")
        if role not in SEMANTIC_ROLES:
            raise RuntimeError(f"Narration plan segment role is invalid: {role!r}.")
        pause_kind = pause.get("kind")
        if pause_kind not in _PAUSE_KINDS:
            raise RuntimeError(f"Narration plan pause kind is invalid: {pause_kind!r}.")
        expected_pause = PAUSE_SECONDS[pause_kind]
        if float(pause["seconds"]) != expected_pause:
            raise RuntimeError(
                f"Narration plan pause {pause_kind!r} must be {expected_pause} seconds."
            )
        warnings = _validate_renderable_segment(text, max_chars)
        result.append(
            NarratorSegment(
                line_number=index,
                text=text,
                warnings=warnings,
                semantic_id=entry["id"],
                chapter_id=str(source["base_output_id"]),
                role=str(role),
                logical_pages=tuple(pages),
                pause_after_seconds=float(pause["seconds"]),
                pause_after_kind=str(pause_kind),
            )
        )
    if len(input_lines) != len(result):
        raise RuntimeError("Narration plan does not cover every narrator input line.")
    return result


def _refresh_narrator_changes(book_root: Path, input_file: Path) -> Path:
    changes_path = book_root / "metadata" / "narrator-changes.json"
    changes = read_json(changes_path, "narrator changes")
    outputs = changes.get("outputs")
    if not isinstance(outputs, list):
        raise RuntimeError("Narrator changes outputs must be an array.")
    expected_file = input_file.relative_to(book_root / "text").as_posix()
    selected = next(
        (
            output
            for output in outputs
            if isinstance(output, dict) and output.get("locutor_file") == expected_file
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise RuntimeError("Narrator changes do not declare the selected narrator input.")
    selected["locutor_sha256"] = sha256_file(input_file)
    write_json(changes_path, changes)
    return changes_path


def _refresh_narrator_review(book_root: Path, input_file: Path, changes_path: Path) -> Path:
    from narrator_quality import (
        audit_text,
        draft_review,
        narration_plan_continuation_lines,
    )

    review_path = book_root / "metadata" / "narrator-review.json"
    previous = read_json(review_path, "narrator review")
    previous_findings = {
        (entry.get("kind"), entry.get("locutor_span")): entry
        for entry in previous.get("findings", [])
        if isinstance(entry, dict)
    }
    findings = audit_text(
        input_file.read_text(encoding="utf-8"),
        narration_plan_continuation_lines(book_root, input_file),
    )
    refreshed = draft_review(book_root, input_file, findings, changes_path)
    refreshed["status"] = "approved"
    refreshed["reviewed_by"] = previous.get("reviewed_by")
    if not isinstance(refreshed["reviewed_by"], str) or not refreshed["reviewed_by"].strip():
        raise RuntimeError("Existing narrator review has no approved reviewer identity.")
    for entry in refreshed["findings"]:
        key = (entry.get("kind"), entry.get("locutor_span"))
        prior = previous_findings.get(key)
        if not isinstance(prior, dict):
            raise RuntimeError(
                "Narrator reflow introduced an unreviewed quality finding: "
                f"{entry.get('kind')} {entry.get('locutor_span')!r}."
            )
        for field in ("category", "status", "reason", "reviewed_by"):
            entry[field] = prior.get(field)
        if entry.get("status") not in {"resolved", "preserved"}:
            raise RuntimeError(f"Prior quality finding {key!r} was not resolved or preserved.")
    pronunciation = previous.get("pronunciation_review")
    if not isinstance(pronunciation, dict) or pronunciation.get("status") != "approved":
        raise RuntimeError("Existing narrator pronunciation review is not approved.")
    refreshed["pronunciation_review"] = pronunciation
    write_json(review_path, refreshed)
    return review_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a semantic Chatterbox narration plan from approved narrator chapter text."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-chars", type=int, default=320)
    parser.add_argument(
        "--refresh-approved-metadata",
        action="store_true",
        help="Refresh narrator lineage and quality metadata after a text-preserving semantic reflow.",
    )
    args = parser.parse_args()

    try:
        book_root = args.book_root.expanduser().resolve()
        input_file = (
            args.input_file.expanduser().resolve()
            if args.input_file
            else book_root / "text" / "locutor" / "book.txt"
        )
        output = (
            args.output.expanduser().resolve()
            if args.output
            else book_root / "metadata" / "narration-plan.json"
        )
        try:
            input_file.relative_to((book_root / "text" / "locutor").resolve())
        except ValueError:
            raise RuntimeError("Narrator input must remain under text/locutor.")
        book_text, plan = build_narration_plan(book_root, input_file, args.max_chars)
        input_file.write_text(book_text, encoding="utf-8")
        plan["input_sha256"] = sha256_file(input_file)
        if args.refresh_approved_metadata:
            changes_path = _refresh_narrator_changes(book_root, input_file)
            plan["narrator_changes_sha256"] = sha256_file(changes_path)
            write_json(output, plan)
            review_path = _refresh_narrator_review(book_root, input_file, changes_path)
            plan["narrator_review_sha256"] = sha256_file(review_path)
        else:
            plan["narrator_changes_sha256"] = sha256_file(
                book_root / "metadata" / "narrator-changes.json"
            )
        write_json(output, plan)
    except RuntimeError as error:
        raise SystemExit(f"Cannot build narration plan: {error}") from error

    print(f"Created {output}")
    print(f"Created {input_file}")


if __name__ == "__main__":
    main()
