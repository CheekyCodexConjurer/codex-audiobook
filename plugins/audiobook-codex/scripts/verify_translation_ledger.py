from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from path_safety import resolve_under
from verify_text_ledger import (
    chapter_output_records,
    expected_chapter_outputs,
    page_requires_text,
    verify as verify_text_ledger,
)


TRANSLATION_STATES = {"verified", "blank", "excluded"}
TARGET_LANGUAGE = "pt-BR"
TRANSLATION_ROOT = Path("translation") / TARGET_LANGUAGE


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
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_portuguese_language(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("_", "-").casefold()
    return normalized in {"pt", "por", "portuguese"} or normalized.startswith("pt-")


def translation_chapter_output_records(ledger: object) -> dict[str, dict]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("chapter_outputs"), list):
        return {}
    records: dict[str, dict] = {}
    for entry in ledger["chapter_outputs"]:
        if not isinstance(entry, dict):
            continue
        output_id = entry.get("id")
        if isinstance(output_id, str) and output_id.strip():
            records[output_id] = entry
    return records


def translated_document_titles(ledger: object) -> dict[str, str]:
    if not isinstance(ledger, dict):
        return {}
    edition = ledger.get("edition")
    if not isinstance(edition, dict) or not isinstance(edition.get("document_titles"), list):
        return {}
    titles: dict[str, str] = {}
    for entry in edition["document_titles"]:
        if not isinstance(entry, dict):
            continue
        output_id = entry.get("id")
        title = entry.get("title")
        if isinstance(output_id, str) and output_id.strip() and require_text(title):
            titles[output_id] = title.strip()
    return titles


def _source_pages_by_number(ledger: dict) -> dict[int, dict]:
    return {
        entry["logical_page"]: entry
        for entry in ledger.get("pages", [])
        if isinstance(entry, dict) and isinstance(entry.get("logical_page"), int)
    }


def _expected_verified_pages(book_map: dict) -> set[int]:
    return {
        page["logical_page"]
        for page in book_map.get("pages", [])
        if isinstance(page, dict)
        and isinstance(page.get("logical_page"), int)
        and page_requires_text(page)
    }


def _validate_translation_pages(
    book_map: dict,
    source_ledger: dict,
    translation_ledger: dict,
    text_root: Path,
) -> list[str]:
    errors: list[str] = []
    entries = translation_ledger.get("pages")
    if not isinstance(entries, list):
        return ["translation ledger must include pages"]

    source_by_page = _source_pages_by_number(source_ledger)
    source_page_numbers = {
        page["logical_page"]
        for page in book_map.get("pages", [])
        if isinstance(page, dict) and isinstance(page.get("logical_page"), int)
    }
    translation_by_page: dict[int, dict] = {}

    for index, entry in enumerate(entries):
        label = f"translation ledger.pages[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
            continue
        if logical_page in translation_by_page:
            errors.append(f"{label}.logical_page is duplicated: {logical_page}")
            continue
        translation_by_page[logical_page] = entry
        if logical_page not in source_page_numbers:
            errors.append(f"{label}.logical_page is not mapped by book-map.json")

    missing = sorted(source_page_numbers - set(translation_by_page))
    if missing:
        errors.append(f"translation ledger is missing mapped logical pages: {missing}")
    extra = sorted(set(translation_by_page) - source_page_numbers)
    if extra:
        errors.append(f"translation ledger contains unmapped logical pages: {extra}")

    for logical_page, source_entry in source_by_page.items():
        entry = translation_by_page.get(logical_page)
        if entry is None:
            continue
        label = f"translation ledger logical page {logical_page}"
        status = entry.get("status")
        if status not in TRANSLATION_STATES:
            errors.append(f"{label} has invalid status: {status!r}")
            continue
        source_status = source_entry.get("status")
        if status != source_status:
            errors.append(f"{label} status must match the verified source ledger")
            continue
        if status != "verified":
            if not require_text(entry.get("notes")):
                errors.append(f"{label} needs notes for status {status}")
            continue

        if entry.get("source_file") != source_entry.get("source_file"):
            errors.append(f"{label}.source_file does not match the source ledger")
        if entry.get("source_sha256") != source_entry.get("source_sha256"):
            errors.append(f"{label}.source_sha256 does not match the source ledger")
        translation_path = resolve_under(
            text_root,
            entry.get("translation_file"),
            (TRANSLATION_ROOT / "pages",),
        )
        if translation_path is None:
            errors.append(f"{label}.translation_file must resolve under {TRANSLATION_ROOT}/pages")
        elif not translation_path.is_file() or not translation_path.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.translation_file is missing or empty")
        elif entry.get("translation_sha256") != sha256_file(translation_path):
            errors.append(f"{label}.translation_sha256 does not match translation_file")
        if not require_text(entry.get("translated_by")):
            errors.append(f"{label}.translated_by must be non-empty")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    required_verified_pages = _expected_verified_pages(book_map)
    for logical_page in required_verified_pages:
        entry = translation_by_page.get(logical_page)
        if entry is not None and entry.get("status") != "verified":
            errors.append(f"translation ledger logical page {logical_page} must be verified")
    return errors


def _validate_translation_outputs(
    book_map: dict,
    source_ledger: dict,
    translation_ledger: dict,
    text_root: Path,
) -> list[str]:
    errors: list[str] = []
    expected_outputs, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    source_outputs = chapter_output_records(source_ledger)
    outputs = translation_ledger.get("chapter_outputs")
    if not isinstance(outputs, list) or not outputs:
        return errors + ["translation ledger must include non-empty chapter_outputs"]

    by_id: dict[str, dict] = {}
    all_pages: dict[int, str] = {}
    for index, entry in enumerate(outputs):
        label = f"translation ledger.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not require_text(output_id):
            errors.append(f"{label}.id must be non-empty")
            continue
        output_id = output_id.strip()
        if output_id in by_id:
            errors.append(f"{label}.id is duplicated: {output_id}")
            continue
        by_id[output_id] = entry

        source_output = source_outputs.get(output_id)
        if not isinstance(source_output, dict):
            errors.append(f"{label}.id has no matching source chapter output")
            continue
        if entry.get("source_file") != source_output.get("source_file"):
            errors.append(f"{label}.source_file does not match the source chapter output")
        if entry.get("source_sha256") != source_output.get("source_sha256"):
            errors.append(f"{label}.source_sha256 does not match the source chapter output")

        translation_path = resolve_under(
            text_root,
            entry.get("translation_file"),
            (TRANSLATION_ROOT / "chapters", TRANSLATION_ROOT / "book.txt"),
        )
        if translation_path is None:
            errors.append(
                f"{label}.translation_file must resolve under {TRANSLATION_ROOT}/chapters or "
                f"{TRANSLATION_ROOT}/book.txt"
            )
        elif not translation_path.is_file() or not translation_path.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.translation_file is missing or empty")
        elif entry.get("translation_sha256") != sha256_file(translation_path):
            errors.append(f"{label}.translation_sha256 does not match translation_file")

        source_pages = entry.get("source_pages")
        if not isinstance(source_pages, list) or not source_pages:
            errors.append(f"{label}.source_pages must be a non-empty array")
        else:
            expected_pages = source_output.get("source_pages")
            expected_page_hashes = {
                page.get("logical_page"): page.get("source_sha256")
                for page in expected_pages
                if isinstance(page, dict) and isinstance(page.get("logical_page"), int)
            } if isinstance(expected_pages, list) else {}
            seen_pages: set[int] = set()
            for page_index, page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                if not isinstance(page, dict):
                    errors.append(f"{page_label} must be an object")
                    continue
                logical_page = page.get("logical_page")
                if not isinstance(logical_page, int) or logical_page <= 0:
                    errors.append(f"{page_label}.logical_page must be positive")
                    continue
                if logical_page in seen_pages:
                    errors.append(f"{page_label}.logical_page is duplicated")
                    continue
                seen_pages.add(logical_page)
                if page.get("source_sha256") != expected_page_hashes.get(logical_page):
                    errors.append(f"{page_label}.source_sha256 does not match its source page")
                prior_output = all_pages.get(logical_page)
                if prior_output is not None and prior_output != output_id:
                    errors.append(
                        f"{page_label}.logical_page is already claimed by translation output {prior_output}"
                    )
                else:
                    all_pages[logical_page] = output_id
        if not require_text(entry.get("translated_by")):
            errors.append(f"{label}.translated_by must be non-empty")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    expected_ids = set(expected_outputs)
    actual_ids = set(by_id)
    missing_ids = sorted(expected_ids - actual_ids)
    if missing_ids:
        errors.append(f"translation ledger.chapter_outputs is missing expected records: {missing_ids}")
    extra_ids = sorted(actual_ids - expected_ids)
    if extra_ids:
        errors.append(f"translation ledger.chapter_outputs contains unknown records: {extra_ids}")

    for output_id in expected_ids & actual_ids:
        source_output = source_outputs.get(output_id)
        translation_output = by_id[output_id]
        expected_pages = {
            page.get("logical_page")
            for page in source_output.get("source_pages", [])
            if isinstance(page, dict) and isinstance(page.get("logical_page"), int)
        } if isinstance(source_output, dict) else set()
        actual_pages = {
            page.get("logical_page")
            for page in translation_output.get("source_pages", [])
            if isinstance(page, dict) and isinstance(page.get("logical_page"), int)
        }
        if actual_pages != expected_pages:
            errors.append(f"translation chapter output {output_id} must cover its exact source pages")
    return errors


def _validate_edition(translation_ledger: dict, expected_output_ids: set[str]) -> list[str]:
    errors: list[str] = []
    edition = translation_ledger.get("edition")
    if not isinstance(edition, dict):
        return ["translation ledger.edition must be an object"]
    book = edition.get("book")
    if not isinstance(book, dict) or not require_text(book.get("title")):
        errors.append("translation ledger.edition.book.title must be non-empty")
    document_titles = edition.get("document_titles")
    if not isinstance(document_titles, list):
        return errors + ["translation ledger.edition.document_titles must be an array"]
    titles: dict[str, str] = {}
    for index, entry in enumerate(document_titles):
        label = f"translation ledger.edition.document_titles[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not require_text(output_id):
            errors.append(f"{label}.id must be non-empty")
            continue
        output_id = output_id.strip()
        if output_id in titles:
            errors.append(f"{label}.id is duplicated: {output_id}")
            continue
        if not require_text(entry.get("title")):
            errors.append(f"{label}.title must be non-empty")
            continue
        titles[output_id] = entry["title"].strip()
    missing = sorted(expected_output_ids - set(titles))
    if missing:
        errors.append(f"translation ledger.edition.document_titles is missing records: {missing}")
    extra = sorted(set(titles) - expected_output_ids)
    if extra:
        errors.append(f"translation ledger.edition.document_titles contains unknown records: {extra}")
    return errors


def _validate_translation_decision(
    book_map: dict,
    source_ledger: dict,
    translation_ledger: dict,
    text_root: Path,
) -> list[str]:
    errors: list[str] = []
    decision = translation_ledger.get("translation_decision")
    if not isinstance(decision, dict):
        return ["translation ledger.translation_decision must be an object"]
    if decision.get("scope") != "whole-book":
        errors.append("translation decision scope must be whole-book")
    if not require_text(decision.get("reason")):
        errors.append("translation decision reason must be non-empty")
    if not require_text(decision.get("reviewed_by")):
        errors.append("translation decision reviewed_by must be non-empty")
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return errors + ["translation decision must include page-backed language evidence"]

    source_by_page = _source_pages_by_number(source_ledger)
    required_pages = _expected_verified_pages(book_map)
    covered_pages: set[int] = set()
    for index, entry in enumerate(evidence):
        label = f"translation decision evidence[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
            continue
        source_page = source_by_page.get(logical_page)
        if not isinstance(source_page, dict) or source_page.get("status") != "verified":
            errors.append(f"{label}.logical_page must reference a verified source page")
            continue
        if entry.get("source_sha256") != source_page.get("source_sha256"):
            errors.append(f"{label}.source_sha256 does not match its verified source page")
            continue
        if not require_text(entry.get("source_span")):
            errors.append(f"{label}.source_span must be non-empty")
            continue
        if not require_text(entry.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        source_path = resolve_under(
            text_root,
            source_page.get("source_file"),
            (Path("source") / "pages",),
        )
        if source_path is None or not source_path.is_file():
            errors.append(f"{label}.logical_page source file is unavailable")
            continue
        source_text = source_path.read_text(encoding="utf-8")
        if str(entry["source_span"]) not in source_text:
            errors.append(f"{label}.source_span does not occur in its cited source page")
            continue
        covered_pages.add(logical_page)
    missing = sorted(required_pages - covered_pages)
    if missing:
        errors.append(
            f"translation decision evidence must cover every verified source page: {missing}"
        )
    return errors


def verify(
    book_map: object,
    book_map_sha256: str,
    source_ledger: object,
    source_ledger_sha256: str,
    translation_ledger: object,
    text_root: Path,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(book_map, dict):
        return ["book map must be an object"]
    if not isinstance(source_ledger, dict):
        return ["source text ledger must be an object"]
    if not isinstance(translation_ledger, dict):
        return ["translation ledger must be an object"]

    errors += verify_text_ledger(
        book_map,
        book_map_sha256,
        source_ledger,
        text_root,
        False,
        True,
    )
    if translation_ledger.get("schema_version") != "1.0":
        errors.append("translation ledger schema_version must be '1.0'")
    if translation_ledger.get("book_map_sha256") != book_map_sha256:
        errors.append("translation ledger.book_map_sha256 does not match book-map.json")
    if translation_ledger.get("text_ledger_sha256") != source_ledger_sha256:
        errors.append("translation ledger.text_ledger_sha256 does not match text-ledger.json")

    analysis = book_map.get("analysis") if isinstance(book_map.get("analysis"), dict) else {}
    source_language = analysis.get("source_language")
    if not require_text(source_language):
        errors.append("translation requires analysis.source_language to be non-empty")
    elif is_portuguese_language(source_language):
        errors.append(
            "translation is only for a whole source work whose analysis.source_language is non-Portuguese"
        )
    elif translation_ledger.get("source_language") != source_language:
        errors.append("translation ledger.source_language does not match analysis.source_language")
    if translation_ledger.get("target_language") != TARGET_LANGUAGE:
        errors.append(f"translation ledger.target_language must be {TARGET_LANGUAGE}")
    errors += _validate_translation_decision(book_map, source_ledger, translation_ledger, text_root)
    errors += _validate_translation_pages(book_map, source_ledger, translation_ledger, text_root)
    errors += _validate_translation_outputs(book_map, source_ledger, translation_ledger, text_root)
    expected_outputs, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    errors += _validate_edition(translation_ledger, set(expected_outputs))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a reviewed whole-book PT-BR translation against Audiobook Codex source text."
    )
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--translation-ledger", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        ledger_path = args.ledger.expanduser().resolve()
        errors = verify(
            load_json(map_path),
            sha256_file(map_path),
            load_json(ledger_path),
            sha256_file(ledger_path),
            load_json(args.translation_ledger.expanduser().resolve()),
            args.text_root.expanduser().resolve(),
        )
    except RuntimeError as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID translation ledger:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID translation ledger")


if __name__ == "__main__":
    main()
