from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from path_safety import resolve_under


LAYOUT_SCHEMA_VERSION = "1.0"
LAYOUT_KINDS = {"paragraph", "quotation", "dialogue", "verse", "heading", "note"}
FLUID_TEXT_EDITION = "fluid-pt-br"
TRANSLATED_TEXT_EDITION = "translated-pt-br"
_SAFE_NOTE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_SAFE_NOTE_MARKER = re.compile(r"^(?:\d+|[*†‡])$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read EPUB layout {path}: {error}") from error


def relative_to_book(book_root: Path, path: Path) -> str:
    return path.resolve().relative_to(book_root.resolve()).as_posix()


def layout_descriptor(book_root: Path, layout_path: Path) -> dict:
    return {
        "mode": "semantic",
        "path": relative_to_book(book_root, layout_path),
        "sha256": sha256_file(layout_path),
    }


def layout_document_index(layout: dict) -> dict[str, dict]:
    documents = layout.get("documents")
    if not isinstance(documents, list):
        return {}
    return {
        document["id"]: document
        for document in documents
        if isinstance(document, dict) and isinstance(document.get("id"), str) and document["id"].strip()
    }


def split_text_blocks(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return [
        block.strip()
        for block in re.split(r"\n\s*\n+", normalized)
        if block.strip()
    ]


def _expected_page_lines(book_root: Path, ledger: dict) -> tuple[list[tuple[str, int]], dict[str, dict], list[str]]:
    errors: list[str] = []
    expected_lines: list[tuple[str, int]] = []
    records_by_file: dict[str, dict] = {}
    text_root = book_root / "text"
    pages = ledger.get("pages")
    if not isinstance(pages, list):
        return expected_lines, records_by_file, ["text ledger must include pages"]

    ordered_pages: list[dict] = []
    for entry in pages:
        if not isinstance(entry, dict) or entry.get("status") != "verified":
            continue
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or logical_page <= 0:
            errors.append("verified ledger pages need a positive logical_page")
            continue
        ordered_pages.append(entry)

    for entry in sorted(ordered_pages, key=lambda value: value["logical_page"]):
        relative_path = entry.get("source_file")
        page_path = resolve_under(
            text_root,
            relative_path,
            (Path("source") / "pages",),
        )
        if page_path is None or not page_path.is_file():
            errors.append(f"verified layout source page is missing: {relative_path}")
            continue
        book_relative = relative_to_book(book_root, page_path)
        expected_hash = entry.get("source_sha256")
        if not isinstance(expected_hash, str) or expected_hash != sha256_file(page_path):
            errors.append(f"verified layout source page hash is invalid: {relative_path}")
            continue
        lines = page_path.read_text(encoding="utf-8").splitlines()
        records_by_file[book_relative] = {
            "logical_page": entry["logical_page"],
            "source_sha256": expected_hash,
            "lines": lines,
        }
        expected_lines.extend(
            (book_relative, line_number)
            for line_number, line in enumerate(lines, start=1)
            if line.strip()
        )
    return expected_lines, records_by_file, errors


def _expected_fluid_blocks(
    book_root: Path,
    edition_outputs: dict[str, dict],
    expected_document_ids: list[str],
) -> tuple[list[tuple[str, int]], dict[str, dict], list[str]]:
    errors: list[str] = []
    expected_blocks: list[tuple[str, int]] = []
    records_by_file: dict[str, dict] = {}
    text_root = book_root / "text"
    for document_id in expected_document_ids:
        output = edition_outputs.get(document_id)
        if not isinstance(output, dict):
            errors.append(f"fluid EPUB layout has no verified output for {document_id}")
            continue
        fluid_path = resolve_under(
            text_root,
            output.get("fluid_file"),
            (Path("fluid") / "pt-BR" / "chapters",),
        )
        if fluid_path is None or not fluid_path.is_file():
            errors.append(
                f"verified fluid layout chapter is missing: {output.get('fluid_file')}"
            )
            continue
        expected_hash = output.get("fluid_sha256")
        if not isinstance(expected_hash, str) or expected_hash != sha256_file(fluid_path):
            errors.append(
                f"verified fluid layout chapter hash is invalid: {output.get('fluid_file')}"
            )
            continue
        book_relative = relative_to_book(book_root, fluid_path)
        blocks = split_text_blocks(fluid_path.read_text(encoding="utf-8"))
        if not blocks:
            errors.append(f"verified fluid layout chapter is empty: {book_relative}")
            continue
        records_by_file[book_relative] = {
            "document_id": document_id,
            "fluid_sha256": expected_hash,
            "blocks": blocks,
        }
        expected_blocks.extend(
            (book_relative, block_index)
            for block_index in range(1, len(blocks) + 1)
        )
    return expected_blocks, records_by_file, errors


def _expected_translation_blocks(
    book_root: Path,
    edition_outputs: dict[str, dict],
    expected_document_ids: list[str],
) -> tuple[list[tuple[str, int]], dict[str, dict], list[str]]:
    errors: list[str] = []
    expected_blocks: list[tuple[str, int]] = []
    records_by_file: dict[str, dict] = {}
    text_root = book_root / "text"
    for document_id in expected_document_ids:
        output = edition_outputs.get(document_id)
        if not isinstance(output, dict):
            errors.append(f"translated EPUB layout has no verified output for {document_id}")
            continue
        translation_path = resolve_under(
            text_root,
            output.get("translation_file"),
            (Path("translation") / "pt-BR" / "chapters",),
        )
        if translation_path is None or not translation_path.is_file():
            errors.append(
                f"verified translated layout chapter is missing: {output.get('translation_file')}"
            )
            continue
        expected_hash = output.get("translation_sha256")
        if not isinstance(expected_hash, str) or expected_hash != sha256_file(translation_path):
            errors.append(
                f"verified translated layout chapter hash is invalid: {output.get('translation_file')}"
            )
            continue
        book_relative = relative_to_book(book_root, translation_path)
        blocks = split_text_blocks(translation_path.read_text(encoding="utf-8"))
        if not blocks:
            errors.append(f"verified translated layout chapter is empty: {book_relative}")
            continue
        records_by_file[book_relative] = {
            "document_id": document_id,
            "translation_sha256": expected_hash,
            "blocks": blocks,
        }
        expected_blocks.extend(
            (book_relative, block_index)
            for block_index in range(1, len(blocks) + 1)
        )
    return expected_blocks, records_by_file, errors


def validate_layout(
    layout: object,
    book_root: Path,
    book_map_sha256: str,
    text_ledger_sha256: str,
    ledger: dict,
    expected_document_ids: list[str],
    *,
    text_edition: str = "original",
    edition_ledger_sha256: str | None = None,
    edition_outputs: dict[str, dict] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(layout, dict):
        return ["EPUB layout must be a JSON object"]
    if layout.get("schema_version") != LAYOUT_SCHEMA_VERSION:
        errors.append(f"EPUB layout schema_version must be '{LAYOUT_SCHEMA_VERSION}'")
    if layout.get("text_edition") != text_edition:
        errors.append(f"EPUB layout text_edition must be {text_edition}")
    if layout.get("book_map_sha256") != book_map_sha256:
        errors.append("EPUB layout book_map_sha256 does not match current book-map.json")
    if layout.get("text_ledger_sha256") != text_ledger_sha256:
        errors.append("EPUB layout text_ledger_sha256 does not match current text-ledger.json")

    fluid_layout = text_edition == FLUID_TEXT_EDITION
    translated_layout = text_edition == TRANSLATED_TEXT_EDITION
    if fluid_layout:
        if layout.get("fluid_edition_ledger_sha256") != edition_ledger_sha256:
            errors.append(
                "EPUB layout fluid_edition_ledger_sha256 does not match current "
                "fluid-edition-ledger.json"
            )
        expected_units, records_by_file, source_errors = _expected_fluid_blocks(
            book_root,
            edition_outputs or {},
            expected_document_ids,
        )
    elif translated_layout:
        if layout.get("translation_ledger_sha256") != edition_ledger_sha256:
            errors.append(
                "EPUB layout translation_ledger_sha256 does not match current "
                "translation-ledger.json"
            )
        expected_units, records_by_file, source_errors = _expected_translation_blocks(
            book_root,
            edition_outputs or {},
            expected_document_ids,
        )
    else:
        expected_units, records_by_file, source_errors = _expected_page_lines(
            book_root,
            ledger,
        )
    errors += source_errors
    documents = layout.get("documents")
    if not isinstance(documents, list) or not documents:
        return errors + ["EPUB layout documents must be a non-empty array"]

    seen_document_ids: set[str] = set()
    document_ids: list[str] = []
    covered_units: list[tuple[str, int]] = []
    note_ids: set[str] = set()
    note_markers: set[str] = set()
    for document_index, document in enumerate(documents):
        label = f"layout.documents[{document_index}]"
        if not isinstance(document, dict):
            errors.append(f"{label} must be an object")
            continue
        document_id = document.get("id")
        if not isinstance(document_id, str) or not document_id.strip():
            errors.append(f"{label}.id must be non-empty")
            continue
        if document_id in seen_document_ids:
            errors.append(f"{label}.id is duplicated: {document_id}")
            continue
        seen_document_ids.add(document_id)
        document_ids.append(document_id)
        blocks = document.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            errors.append(f"{label}.blocks must be a non-empty array")
            continue
        for block_index, block in enumerate(blocks):
            block_label = f"{label}.blocks[{block_index}]"
            if not isinstance(block, dict):
                errors.append(f"{block_label} must be an object")
                continue
            kind = block.get("kind")
            if kind not in LAYOUT_KINDS:
                errors.append(f"{block_label}.kind must be one of {sorted(LAYOUT_KINDS)}")
                continue
            if kind == "heading":
                level = block.get("level")
                if not isinstance(level, int) or not 1 <= level <= 6:
                    errors.append(f"{block_label}.level must be an integer from 1 to 6")
            elif "level" in block:
                errors.append(f"{block_label}.level is only allowed for heading blocks")
            if kind == "note":
                note_id = block.get("id")
                marker = block.get("marker")
                if not isinstance(note_id, str) or _SAFE_NOTE_ID.fullmatch(note_id) is None:
                    errors.append(f"{block_label}.id must be a safe non-empty note identifier")
                elif note_id in note_ids:
                    errors.append(f"{block_label}.id is duplicated: {note_id}")
                else:
                    note_ids.add(note_id)
                if not isinstance(marker, str) or _SAFE_NOTE_MARKER.fullmatch(marker) is None:
                    errors.append(f"{block_label}.marker must be a footnote marker")
                elif marker in note_markers:
                    errors.append(f"{block_label}.marker is duplicated: {marker}")
                else:
                    note_markers.add(marker)
            elif "marker" in block or "id" in block:
                errors.append(f"{block_label}.id and .marker are only allowed for note blocks")
            if "join_with_previous" in block:
                if not fluid_layout:
                    errors.append(
                        f"{block_label}.join_with_previous is only allowed for fluid layouts"
                    )
                elif block.get("join_with_previous") is not True:
                    errors.append(
                        f"{block_label}.join_with_previous must be true when present"
                    )
                elif kind not in {"paragraph", "quotation"}:
                    errors.append(
                        f"{block_label}.join_with_previous is only allowed for paragraph "
                        "or quotation blocks"
                    )
                else:
                    previous_index = block_index - 1
                    while (
                        previous_index >= 0
                        and isinstance(blocks[previous_index], dict)
                        and blocks[previous_index].get("kind") == "note"
                    ):
                        previous_index -= 1
                    if (
                        previous_index < 0
                        or not isinstance(blocks[previous_index], dict)
                        or blocks[previous_index].get("kind") != kind
                    ):
                        errors.append(
                            f"{block_label}.join_with_previous requires a preceding "
                            f"{kind} block separated only by note blocks"
                        )
            if fluid_layout or translated_layout:
                path_prefix = (
                    "text/fluid/pt-BR/chapters/"
                    if fluid_layout
                    else "text/translation/pt-BR/chapters/"
                )
                sha_key = "fluid_sha256" if fluid_layout else "translation_sha256"
                label_name = "fluid" if fluid_layout else "translated"
                text_file = block.get("text_file")
                if (
                    not isinstance(text_file, str)
                    or not text_file.startswith(path_prefix)
                ):
                    errors.append(
                        f"{block_label}.text_file must resolve under {path_prefix}"
                    )
                    continue
                normalized_file = text_file.replace("\\", "/")
                record = records_by_file.get(normalized_file)
                if record is None:
                    errors.append(
                        f"{block_label}.text_file is not a verified {label_name} chapter: "
                        f"{text_file}"
                    )
                    continue
                if record["document_id"] != document_id:
                    errors.append(
                        f"{block_label}.text_file must match the verified {label_name} "
                        f"chapter for document {document_id}"
                    )
                if block.get("text_sha256") != record[sha_key]:
                    errors.append(
                        f"{block_label}.text_sha256 does not match its verified "
                        f"{label_name} chapter"
                    )
                block_number = block.get("block_index")
                if (
                    not isinstance(block_number, int)
                    or isinstance(block_number, bool)
                    or block_number <= 0
                    or block_number > len(record["blocks"])
                ):
                    errors.append(f"{block_label}.block_index is invalid")
                    continue
                covered_units.append((normalized_file, block_number))
            else:
                spans = block.get("spans")
                if not isinstance(spans, list) or not spans:
                    errors.append(f"{block_label}.spans must be a non-empty array")
                    continue
                block_has_text = False
                for span_index, span in enumerate(spans):
                    span_label = f"{block_label}.spans[{span_index}]"
                    if not isinstance(span, dict):
                        errors.append(f"{span_label} must be an object")
                        continue
                    source_file = span.get("source_file")
                    if (
                        not isinstance(source_file, str)
                        or not source_file.startswith("text/source/pages/")
                    ):
                        errors.append(
                            f"{span_label}.source_file must resolve under text/source/pages/"
                        )
                        continue
                    normalized_file = source_file.replace("\\", "/")
                    record = records_by_file.get(normalized_file)
                    if record is None:
                        errors.append(
                            f"{span_label}.source_file is not a verified source page: "
                            f"{source_file}"
                        )
                        continue
                    if span.get("source_sha256") != record["source_sha256"]:
                        errors.append(
                            f"{span_label}.source_sha256 does not match its verified "
                            "source page"
                        )
                    start_line = span.get("start_line")
                    end_line = span.get("end_line")
                    if (
                        not isinstance(start_line, int)
                        or not isinstance(end_line, int)
                        or start_line <= 0
                        or end_line < start_line
                        or end_line > len(record["lines"])
                    ):
                        errors.append(f"{span_label} has an invalid inclusive line range")
                        continue
                    for line_number in range(start_line, end_line + 1):
                        if record["lines"][line_number - 1].strip():
                            block_has_text = True
                            covered_units.append((normalized_file, line_number))
                if not block_has_text:
                    errors.append(
                        f"{block_label} must cover at least one non-empty source line"
                    )

    if seen_document_ids != set(expected_document_ids):
        missing = sorted(set(expected_document_ids) - seen_document_ids)
        extra = sorted(seen_document_ids - set(expected_document_ids))
        if missing:
            errors.append(f"EPUB layout is missing documents: {missing}")
        if extra:
            errors.append(f"EPUB layout contains unknown documents: {extra}")
    elif document_ids != expected_document_ids:
        errors.append("EPUB layout document order must match the validated text ledger")
    if covered_units != expected_units:
        expected_set = set(expected_units)
        covered_set = set(covered_units)
        missing = len(expected_set - covered_set)
        duplicated = len(covered_units) - len(covered_set)
        extra = len(covered_set - expected_set)
        order_matches = (
            len(covered_units) == len(expected_units)
            and covered_units == expected_units
        )
        details = []
        if missing:
            details.append(f"{missing} missing")
        if duplicated:
            details.append(f"{duplicated} duplicated")
        if extra:
            details.append(f"{extra} unexpected")
        if not order_matches:
            details.append("out of source order")
        unit_label = (
            "fluid chapter block"
            if fluid_layout
            else "translated chapter block"
            if translated_layout
            else "non-empty verified source line"
        )
        errors.append(
            f"EPUB layout must cover each {unit_label} exactly once"
            + f" ({', '.join(details)})"
        )
    return errors


def lines_for_block(block: dict, book_root: Path) -> list[str]:
    if "text_file" in block:
        text_file = block["text_file"]
        normalized_file = str(text_file).replace("\\", "/")
        if normalized_file.startswith("text/fluid/pt-BR/chapters/"):
            allowed_subtree = Path("text") / "fluid" / "pt-BR" / "chapters"
            label = "fluid"
        elif normalized_file.startswith("text/translation/pt-BR/chapters/"):
            allowed_subtree = Path("text") / "translation" / "pt-BR" / "chapters"
            label = "translated"
        else:
            raise RuntimeError(
                f"EPUB layout text path is invalid: {block['text_file']}"
            )
        text_path = resolve_under(
            book_root,
            text_file,
            (allowed_subtree,),
        )
        if text_path is None or not text_path.is_file():
            raise RuntimeError(
                f"EPUB layout {label} text path is invalid: {block['text_file']}"
            )
        blocks = split_text_blocks(text_path.read_text(encoding="utf-8"))
        block_index = block.get("block_index")
        if (
            not isinstance(block_index, int)
            or block_index <= 0
            or block_index > len(blocks)
        ):
            raise RuntimeError(f"EPUB layout {label} block_index is invalid")
        return [
            line.strip()
            for line in blocks[block_index - 1].splitlines()
            if line.strip()
        ]

    lines: list[str] = []
    for span in block["spans"]:
        source_path = resolve_under(
            book_root,
            span["source_file"],
            (Path("text") / "source" / "pages",),
        )
        if source_path is None:
            raise RuntimeError(f"EPUB layout source path is invalid: {span['source_file']}")
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        lines.extend(
            line.strip()
            for line in source_lines[span["start_line"] - 1 : span["end_line"]]
            if line.strip()
        )
    return lines
