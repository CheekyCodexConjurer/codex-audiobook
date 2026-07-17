from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from unicodedata import normalize

from book_layout import resolve_book_paths
from path_safety import resolve_under
from verify_text_ledger import chapter_output_records, verify as verify_text_ledger
from verify_translation_ledger import (
    TARGET_LANGUAGE,
    is_portuguese_language,
    load_json,
    sha256_file,
    translation_chapter_output_records,
    verify as verify_translation_ledger,
)


NARRATOR_MODES = {"faithful", "archaic-modernized", "translated-pt-br"}
CHANGE_KINDS = {
    "spoken_expansion",
    "punctuation",
    "mapped_exclusion",
    "figure_description",
    "orthographic_modernization",
    "archaic_lexical_modernization",
    "pronunciation",
    "editorial_correction",
    "note_relocation",
    "preserved_original",
}
ARCHAIC_CHANGE_KINDS = {"orthographic_modernization", "archaic_lexical_modernization"}


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_text(value: str) -> str:
    return " ".join(normalize("NFC", value).split())


def _book_source_hash(book_map: dict, book_root: Path) -> str | None:
    source = book_map.get("source")
    if not isinstance(source, dict):
        return None
    source_path = resolve_under(book_root, source.get("path"), (Path("source"),))
    return sha256_file(source_path) if source_path is not None and source_path.is_file() else None


def _load_base_context(
    book_root: Path,
    book_map: dict,
    book_map_sha256: str,
    narrator_changes: dict,
) -> tuple[list[str], dict[str, dict], str | None, dict | None]:
    errors: list[str] = []
    text_root = book_root / "text"
    source_ledger_path = book_root / "metadata" / "text-ledger.json"
    try:
        source_ledger = load_json(source_ledger_path)
    except RuntimeError as error:
        return [str(error)], {}, None, None
    if not isinstance(source_ledger, dict):
        return ["text-ledger.json must be an object"], {}, None, None

    base_edition = narrator_changes.get("base_edition")
    if base_edition == "source":
        errors += verify_text_ledger(
            book_map,
            book_map_sha256,
            source_ledger,
            text_root,
            False,
            True,
        )
        return errors, chapter_output_records(source_ledger), sha256_file(source_ledger_path), source_ledger
    if base_edition != "translated-pt-br":
        return ["narrator changes base_edition must be source or translated-pt-br"], {}, None, None

    translation_path = book_root / "metadata" / "translation-ledger.json"
    try:
        translation_ledger = load_json(translation_path)
    except RuntimeError as error:
        return [str(error)], {}, None, None
    if not isinstance(translation_ledger, dict):
        return ["translation-ledger.json must be an object"], {}, None, None
    errors += verify_translation_ledger(
        book_map,
        book_map_sha256,
        source_ledger,
        sha256_file(source_ledger_path),
        translation_ledger,
        text_root,
    )
    return (
        errors,
        translation_chapter_output_records(translation_ledger),
        sha256_file(translation_path),
        source_ledger,
    )


def _validate_archaic_assessment(
    narrator_changes: dict,
    source_ledger: dict | None,
    text_root: Path,
) -> tuple[list[str], set[tuple[int, str, str]]]:
    errors: list[str] = []
    evidence_keys: set[tuple[int, str, str]] = set()
    assessment = narrator_changes.get("archaic_assessment")
    if not isinstance(assessment, dict) or assessment.get("status") != "confirmed":
        return (
            ["archaic-modernized narrator mode requires archaic_assessment.status=confirmed"],
            evidence_keys,
        )
    if not require_text(assessment.get("reviewed_by")):
        errors.append("archaic assessment reviewed_by must be non-empty")
    evidence = assessment.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return errors + ["archaic assessment must include non-empty evidence"], evidence_keys
    for index, entry in enumerate(evidence):
        label = f"archaic assessment evidence[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = entry.get("logical_page")
        source_span = entry.get("source_span")
        source_sha256 = entry.get("source_sha256")
        if not isinstance(logical_page, int) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
        if not require_text(source_span):
            errors.append(f"{label}.source_span must be non-empty")
        if not require_text(entry.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        if not require_text(source_sha256):
            errors.append(f"{label}.source_sha256 must be non-empty")
        if (
            not isinstance(source_ledger, dict)
            or not isinstance(logical_page, int)
            or logical_page <= 0
            or not require_text(source_span)
            or not require_text(source_sha256)
        ):
            continue
        source_page = next(
            (
                page
                for page in source_ledger.get("pages", [])
                if isinstance(page, dict) and page.get("logical_page") == logical_page
            ),
            None,
        )
        if not isinstance(source_page, dict) or source_page.get("status") != "verified":
            errors.append(f"{label}.logical_page must reference a verified source page")
            continue
        if source_sha256 != source_page.get("source_sha256"):
            errors.append(f"{label}.source_sha256 does not match its verified source page")
            continue
        source_path = resolve_under(text_root, source_page.get("source_file"), (Path("source") / "pages",))
        if source_path is None or not source_path.is_file():
            errors.append(f"{label}.logical_page source file is unavailable")
        elif normalized_text(source_span) not in normalized_text(source_path.read_text(encoding="utf-8")):
            errors.append(f"{label}.source_span does not occur in its cited source page")
        else:
            evidence_keys.add((logical_page, source_sha256, normalized_text(source_span)))
    return errors, evidence_keys


def _validate_outputs(
    narrator_changes: dict,
    base_outputs: dict[str, dict],
    text_root: Path,
    input_file: Path | None,
) -> tuple[list[str], dict | None]:
    errors: list[str] = []
    outputs = narrator_changes.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return ["narrator changes outputs must be a non-empty array"], None

    by_file: dict[Path, dict] = {}
    ids: set[str] = set()
    selected: dict | None = None
    expected_base_ids = set(base_outputs)
    for index, entry in enumerate(outputs):
        label = f"narrator changes.outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not require_text(output_id):
            errors.append(f"{label}.id must be non-empty")
            continue
        output_id = output_id.strip()
        if output_id in ids:
            errors.append(f"{label}.id is duplicated: {output_id}")
            continue
        ids.add(output_id)
        if entry.get("kind") not in {"full-book", "chapter"}:
            errors.append(f"{label}.kind must be full-book or chapter")
        locutor_path = resolve_under(text_root, entry.get("locutor_file"), (Path("locutor"),))
        if locutor_path is None:
            errors.append(f"{label}.locutor_file must resolve under locutor/")
            continue
        if not locutor_path.is_file() or not locutor_path.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.locutor_file is missing or empty")
        elif entry.get("locutor_sha256") != sha256_file(locutor_path):
            errors.append(f"{label}.locutor_sha256 does not match locutor_file")
        if locutor_path in by_file:
            errors.append(f"{label}.locutor_file is duplicated")
        by_file[locutor_path] = entry
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

        base_entries = entry.get("base_outputs")
        if not isinstance(base_entries, list) or not base_entries:
            errors.append(f"{label}.base_outputs must be a non-empty array")
            continue
        seen_base_ids: set[str] = set()
        for base_index, base_entry in enumerate(base_entries):
            base_label = f"{label}.base_outputs[{base_index}]"
            if not isinstance(base_entry, dict):
                errors.append(f"{base_label} must be an object")
                continue
            base_id = base_entry.get("id")
            if not require_text(base_id):
                errors.append(f"{base_label}.id must be non-empty")
                continue
            base_id = base_id.strip()
            if base_id in seen_base_ids:
                errors.append(f"{base_label}.id is duplicated")
                continue
            seen_base_ids.add(base_id)
            expected = base_outputs.get(base_id)
            if not isinstance(expected, dict):
                errors.append(f"{base_label}.id has no validated base output")
                continue
            expected_file = (
                expected.get("translation_file")
                if "translation_file" in expected
                else expected.get("source_file")
            )
            expected_hash = (
                expected.get("translation_sha256")
                if "translation_file" in expected
                else expected.get("source_sha256")
            )
            if base_entry.get("base_file") != expected_file:
                errors.append(f"{base_label}.base_file does not match validated base output")
            if base_entry.get("base_sha256") != expected_hash:
                errors.append(f"{base_label}.base_sha256 does not match validated base output")
        if entry.get("kind") == "full-book" and seen_base_ids != expected_base_ids:
            errors.append(f"{label}.base_outputs must cover every validated base output")
        if entry.get("kind") == "chapter" and len(seen_base_ids) != 1:
            errors.append(f"{label}.chapter output must reference exactly one base output")
        if input_file is not None and locutor_path == input_file:
            selected = entry

    if input_file is not None and selected is None:
        errors.append("narrator input is not declared by narrator changes outputs")
    return errors, selected


def _validate_changes(
    narrator_changes: dict,
    outputs_by_id: dict[str, dict],
    base_outputs: dict[str, dict],
    text_root: Path,
    mode: str,
    archaic_evidence: set[tuple[int, str, str]],
) -> list[str]:
    errors: list[str] = []
    changes = narrator_changes.get("changes")
    if not isinstance(changes, list):
        return ["narrator changes changes must be an array"]
    archaic_change_seen = False
    changes_by_output: dict[str, list[dict]] = {}
    for index, entry in enumerate(changes):
        label = f"narrator changes.changes[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("output_id")
        output = outputs_by_id.get(output_id) if isinstance(output_id, str) else None
        if output is None:
            errors.append(f"{label}.output_id must reference an output")
            continue
        kind = entry.get("kind")
        if kind not in CHANGE_KINDS:
            errors.append(f"{label}.kind is invalid")
        if not require_text(entry.get("base_output_id")):
            errors.append(f"{label}.base_output_id must be non-empty")
            continue
        else:
            base_ids = {
                base.get("id")
                for base in output.get("base_outputs", [])
                if isinstance(base, dict)
            }
            if entry["base_output_id"] not in base_ids:
                errors.append(f"{label}.base_output_id is not declared by its output")
                continue
        base_record = base_outputs.get(entry["base_output_id"])
        if not isinstance(base_record, dict):
            errors.append(f"{label}.base_output_id has no validated base output")
            continue
        base_file = (
            base_record.get("translation_file")
            if "translation_file" in base_record
            else base_record.get("source_file")
        )
        base_path = resolve_under(
            text_root,
            base_file,
            (Path("source"), Path("translation") / TARGET_LANGUAGE),
        )
        locutor_path = resolve_under(text_root, output.get("locutor_file"), (Path("locutor"),))
        if base_path is None or not base_path.is_file():
            errors.append(f"{label}.base_output_id source file is unavailable")
            continue
        if locutor_path is None or not locutor_path.is_file():
            errors.append(f"{label}.output_id locutor file is unavailable")
            continue
        base_span = normalized_text(str(entry.get("base_span") or ""))
        locutor_span = normalized_text(str(entry.get("locutor_span") or ""))
        if not base_span:
            errors.append(f"{label}.base_span must be non-empty")
        elif base_span not in normalized_text(base_path.read_text(encoding="utf-8")):
            errors.append(f"{label}.base_span does not occur in its declared base output")
        if not locutor_span:
            errors.append(f"{label}.locutor_span must be non-empty")
        elif locutor_span not in normalized_text(locutor_path.read_text(encoding="utf-8")):
            errors.append(f"{label}.locutor_span does not occur in its declared locutor output")
        if not require_text(entry.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")
        pages = entry.get("logical_pages")
        page_set: set[int] = set()
        if not isinstance(pages, list) or not pages or any(
            not isinstance(page, int) or page <= 0 for page in pages
        ):
            errors.append(f"{label}.logical_pages must contain positive integers")
        else:
            page_set = set(pages)
            base_pages = {
                source_page.get("logical_page")
                for source_page in base_record.get("source_pages", [])
                if isinstance(source_page, dict) and isinstance(source_page.get("logical_page"), int)
            }
            if not page_set.issubset(base_pages):
                errors.append(f"{label}.logical_pages are outside its declared base output")
        if kind in ARCHAIC_CHANGE_KINDS:
            archaic_change_seen = True
            if mode != "archaic-modernized":
                errors.append(f"{label} archaic modernization requires archaic-modernized mode")
            source_sha256 = entry.get("source_sha256")
            if len(page_set) != 1:
                errors.append(
                    f"{label}.logical_pages must identify exactly one source page for archaic modernization"
                )
            if not require_text(source_sha256):
                errors.append(f"{label}.source_sha256 must be non-empty for archaic modernization")
            elif (
                base_span
                and len(page_set) == 1
                and (next(iter(page_set)), source_sha256, base_span) not in archaic_evidence
            ):
                errors.append(
                    f"{label} archaic modernization must match confirmed assessment evidence "
                    "by source span, logical page, and source_sha256"
                )
        if base_span and locutor_span:
            changes_by_output.setdefault(output_id, []).append(entry)
    if mode == "archaic-modernized" and not archaic_change_seen:
        errors.append("archaic-modernized narrator mode requires an archaic modernization change")

    for output_id, output in outputs_by_id.items():
        locutor_path = resolve_under(text_root, output.get("locutor_file"), (Path("locutor"),))
        base_entries = output.get("base_outputs")
        if (
            locutor_path is None
            or not locutor_path.is_file()
            or not isinstance(base_entries, list)
        ):
            continue
        base_text_by_id: dict[str, str] = {}
        ordered_base_ids: list[str] = []
        for base_entry in base_entries:
            if not isinstance(base_entry, dict) or not isinstance(base_entry.get("id"), str):
                continue
            base_id = base_entry["id"]
            base_record = base_outputs.get(base_id)
            if not isinstance(base_record, dict):
                continue
            base_file = (
                base_record.get("translation_file")
                if "translation_file" in base_record
                else base_record.get("source_file")
            )
            base_path = resolve_under(
                text_root,
                base_file,
                (Path("source"), Path("translation") / TARGET_LANGUAGE),
            )
            if base_path is None or not base_path.is_file():
                continue
            base_text_by_id[base_id] = normalized_text(base_path.read_text(encoding="utf-8"))
            ordered_base_ids.append(base_id)

        locutor_text = normalized_text(locutor_path.read_text(encoding="utf-8"))
        for index, change in enumerate(changes_by_output.get(output_id, []), start=1):
            base_id = change["base_output_id"]
            base_span = normalized_text(change["base_span"])
            locutor_span = normalized_text(change["locutor_span"])
            base_text = base_text_by_id.get(base_id)
            if base_text is None:
                continue
            if base_text.count(base_span) != 1:
                errors.append(
                    f"narrator change for {output_id} must use a unique base_span in {base_id}"
                )
                continue
            if locutor_text.count(locutor_span) != 1:
                errors.append(
                    f"narrator change for {output_id} must use a unique locutor_span"
                )
                continue
            marker = f"[[narrator-change-{output_id}-{index}]]"
            base_text_by_id[base_id] = base_text.replace(base_span, marker, 1)
            locutor_text = locutor_text.replace(locutor_span, marker, 1)
        expected_text = normalized_text(" ".join(base_text_by_id.get(base_id, "") for base_id in ordered_base_ids))
        if expected_text != locutor_text:
            errors.append(
                f"narrator output {output_id} has text not covered by its declared changes"
            )
    return errors


def validate_lineage(
    book_root: Path,
    narrator_changes_path: Path,
    input_file: Path | None = None,
) -> tuple[list[str], dict | None]:
    errors: list[str] = []
    map_path = book_root / "metadata" / "book-map.json"
    try:
        book_map = load_json(map_path)
        narrator_changes = load_json(narrator_changes_path)
    except RuntimeError as error:
        return [str(error)], None
    if not isinstance(book_map, dict):
        return ["book-map.json must be an object"], None
    if not isinstance(narrator_changes, dict):
        return ["narrator changes must be an object"], None
    book_map_sha256 = sha256_file(map_path)
    if narrator_changes.get("schema_version") != "2.0":
        errors.append("narrator changes schema_version must be '2.0'")
    source_hash = _book_source_hash(book_map, book_root)
    if source_hash is None:
        errors.append("cannot resolve the immutable source file for narrator lineage")
    elif narrator_changes.get("source_book_sha256") != source_hash:
        errors.append("narrator changes source_book_sha256 does not match the immutable source")
    if narrator_changes.get("book_map_sha256") != book_map_sha256:
        errors.append("narrator changes book_map_sha256 does not match book-map.json")

    mode = narrator_changes.get("mode")
    if mode not in NARRATOR_MODES:
        errors.append("narrator changes mode is invalid")
    base_errors, base_outputs, base_ledger_sha256, source_ledger = _load_base_context(
        book_root,
        book_map,
        book_map_sha256,
        narrator_changes,
    )
    errors += base_errors
    if base_ledger_sha256 is not None and narrator_changes.get("base_ledger_sha256") != base_ledger_sha256:
        errors.append("narrator changes base_ledger_sha256 does not match its validated base ledger")

    analysis = book_map.get("analysis") if isinstance(book_map.get("analysis"), dict) else {}
    source_language = analysis.get("source_language")
    base_edition = narrator_changes.get("base_edition")
    archaic_evidence: set[tuple[int, str, str]] = set()
    if mode == "archaic-modernized":
        if base_edition != "source":
            errors.append("archaic-modernized narrator mode must derive from source")
        if not is_portuguese_language(source_language):
            errors.append("archaic-modernized narrator mode requires a Portuguese source language")
        assessment_errors, archaic_evidence = _validate_archaic_assessment(
            narrator_changes,
            source_ledger,
            book_root / "text",
        )
        errors += assessment_errors
    elif mode == "translated-pt-br":
        if base_edition != "translated-pt-br":
            errors.append("translated-pt-br narrator mode must derive from translated-pt-br")
        if not require_text(source_language) or is_portuguese_language(source_language):
            errors.append(
                "translated-pt-br narrator mode requires a whole source work with non-Portuguese source_language"
            )
    elif mode == "faithful":
        if base_edition != "source":
            errors.append("faithful narrator mode must derive from source")
        if not is_portuguese_language(source_language):
            errors.append(
                "a non-Portuguese source must use translated-pt-br narrator mode after translation"
            )

    text_root = book_root / "text"
    resolved_input = input_file.resolve() if input_file is not None else None
    output_errors, selected = _validate_outputs(
        narrator_changes,
        base_outputs,
        text_root,
        resolved_input,
    )
    errors += output_errors
    outputs_by_id = {
        output.get("id"): output
        for output in narrator_changes.get("outputs", [])
        if isinstance(output, dict) and isinstance(output.get("id"), str)
    }
    if isinstance(mode, str):
        errors += _validate_changes(
            narrator_changes,
            outputs_by_id,
            base_outputs,
            text_root,
            mode,
            archaic_evidence,
        )
    if errors or selected is None and input_file is not None:
        return errors, None

    return (
        [],
        {
            "schema_version": narrator_changes["schema_version"],
            "narrator_changes_sha256": sha256_file(narrator_changes_path),
            "mode": narrator_changes["mode"],
            "base_edition": narrator_changes["base_edition"],
            "base_ledger_sha256": narrator_changes["base_ledger_sha256"],
            "output_id": selected.get("id") if selected is not None else None,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate that an Audiobook Codex narrator file has auditable source or PT-BR translation lineage."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--narrator-changes", type=Path)
    parser.add_argument("--input-file", type=Path)
    args = parser.parse_args()

    book_root = resolve_book_paths(args.book_root).assembly_root
    narrator_changes = (
        args.narrator_changes.expanduser().resolve()
        if args.narrator_changes
        else book_root / "metadata" / "narrator-changes.json"
    )
    input_file = args.input_file.expanduser().resolve() if args.input_file else None
    errors, provenance = validate_lineage(book_root, narrator_changes, input_file)
    if errors:
        print("INVALID narrator lineage:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID narrator lineage")
    if provenance is not None:
        print(json.dumps(provenance, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
