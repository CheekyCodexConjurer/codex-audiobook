from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from path_safety import resolve_under
from merge_ledger_shards import validate_shard
from swarm_claims import (
    SHARD_STAGE_BY_KIND,
    SwarmValidationError,
    claims_by_id,
    normalized_relative_path,
    validate_claim_map,
)


LEDGER_STATES = {"verified", "blank", "excluded"}
CLAIM_DIRECTORY_TARGETS = frozenset(
    {
        "metadata/work/text-ledger.d",
        "metadata/work/translation-ledger.d",
        "metadata/work/fluid-ledger.d",
        "metadata/work/narrator-changes.d",
        "metadata/work/narrator-review.d",
    }
)


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


def _path_conflict(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def book_root_from_text_root(text_root: Path) -> Path:
    return text_root.resolve().parent


def _claim_target_paths(claim: dict[str, Any]) -> tuple[list[str], list[str]]:
    targets: list[str] = []
    errors: list[str] = []
    for field in ("write_set", "canonical_targets"):
        value = claim.get(field)
        if not isinstance(value, list):
            errors.append(f"claim.{field} must be an array")
            continue
        for index, raw_path in enumerate(value):
            try:
                targets.append(normalized_relative_path(raw_path, label=f"claim.{field}[{index}]"))
            except SwarmValidationError as error:
                errors.append(str(error))
    return targets, errors


def _is_claim_target(path: str, targets: list[str]) -> bool:
    return any(
        path == target
        or (target in CLAIM_DIRECTORY_TARGETS and path.startswith(target + "/"))
        for target in targets
    )


def validate_claim_file_target(
    claim: dict[str, Any],
    text_relative_path: object,
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(text_relative_path, str) or not text_relative_path.strip():
        return [f"{label} must be non-empty before claim target validation"]
    try:
        book_relative = "text/" + normalized_relative_path(text_relative_path, label=label)
    except SwarmValidationError as error:
        return [str(error)]
    targets, target_errors = _claim_target_paths(claim)
    errors += target_errors
    if not targets:
        errors.append("claim write_set/canonical_targets must be non-empty for output validation")
    elif not _is_claim_target(book_relative, targets):
        errors.append(f"{label} is outside claim write_set/canonical_targets: {book_relative}")
    return errors


def _validate_claim_shard_path(
    claim: dict[str, Any],
    shard_path: Path | None,
    book_root: Path,
) -> list[str]:
    if shard_path is None:
        return []
    try:
        relative = shard_path.resolve().relative_to(book_root.resolve()).as_posix()
    except ValueError:
        return [f"shard path is outside derived book root: {shard_path}"]
    targets, target_errors = _claim_target_paths(claim)
    errors = target_errors
    if not targets:
        errors.append("claim write_set/canonical_targets must be non-empty for shard target validation")
    elif not _is_claim_target(relative, targets):
        errors.append(f"shard path is outside claim write_set/canonical_targets: {relative}")
    return errors


def claim_scope_unit_ids(claim: dict[str, Any]) -> set[str]:
    scope = claim.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("unit_ids"), list):
        return set()
    return {unit_id.strip() for unit_id in scope["unit_ids"] if isinstance(unit_id, str) and unit_id.strip()}


def page_records_by_number(book_map: dict) -> dict[int, dict]:
    return {
        page["logical_page"]: page
        for page in book_map.get("pages", [])
        if isinstance(page, dict)
        and isinstance(page.get("logical_page"), int)
        and not isinstance(page.get("logical_page"), bool)
    }


def record_scope_identifiers(section: str, record: dict, book_map: dict | None = None) -> set[str]:
    identifiers: set[str] = set()
    if section in {"pages", "source_pages"}:
        logical_page = record.get("logical_page")
        if isinstance(logical_page, int) and not isinstance(logical_page, bool):
            identifiers.update({str(logical_page), f"page-{logical_page:04d}"})
            if book_map is not None:
                mapped = page_records_by_number(book_map).get(logical_page)
                chapter_id = mapped.get("chapter_id") if isinstance(mapped, dict) else None
                if isinstance(chapter_id, str) and chapter_id.strip():
                    identifiers.add(chapter_id.strip())
    elif section == "chapter_outputs":
        output_id = record.get("id")
        if isinstance(output_id, str) and output_id.strip():
            identifiers.add(output_id.strip())
        source_pages = record.get("source_pages")
        if isinstance(source_pages, list):
            for source_page in source_pages:
                if isinstance(source_page, dict):
                    identifiers.update(record_scope_identifiers("source_pages", source_page, book_map))
    elif section == "blocks":
        for field in ("id", "output_id"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip())
    elif section in {"glossary_proposals", "ambiguities"}:
        for field in ("id", "term", "source_term"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip())
        for field in ("logical_page",):
            value = record.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                identifiers.update({str(value), f"page-{value:04d}"})
        source_pages = record.get("source_pages")
        if isinstance(source_pages, list):
            for page in source_pages:
                if isinstance(page, int) and not isinstance(page, bool):
                    identifiers.update({str(page), f"page-{page:04d}"})
    return identifiers


def _logical_page_scope_identifiers(logical_page: int, book_map: dict | None) -> set[str]:
    identifiers = {str(logical_page), f"page-{logical_page:04d}"}
    if book_map is not None:
        mapped = page_records_by_number(book_map).get(logical_page)
        chapter_id = mapped.get("chapter_id") if isinstance(mapped, dict) else None
        if isinstance(chapter_id, str) and chapter_id.strip():
            identifiers.add(chapter_id.strip())
    return identifiers


def claim_owned_logical_pages(book_map: dict, claim: dict[str, Any]) -> set[int]:
    unit_ids = claim_scope_unit_ids(claim)
    if not unit_ids:
        return set()
    owned: set[int] = set()
    for page in book_map.get("pages", []):
        if not isinstance(page, dict):
            continue
        logical_page = page.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            continue
        if not _logical_page_scope_identifiers(logical_page, book_map).isdisjoint(unit_ids):
            owned.add(logical_page)
    return owned


def validate_exact_page_coverage(
    owned_pages: set[int],
    actual_pages: set[int],
    *,
    label: str,
) -> list[str]:
    errors: list[str] = []
    missing = sorted(owned_pages - actual_pages)
    extra = sorted(actual_pages - owned_pages)
    if missing:
        errors.append(f"{label} is missing owned logical pages: {missing}")
    if extra:
        errors.append(f"{label} contains unowned logical pages: {extra}")
    return errors


def _validate_logical_page_in_scope(
    logical_page: object,
    unit_ids: set[str],
    *,
    label: str,
    book_map: dict | None,
) -> list[str]:
    if not isinstance(logical_page, int) or isinstance(logical_page, bool):
        return []
    if _logical_page_scope_identifiers(logical_page, book_map).isdisjoint(unit_ids):
        return [f"{label} is outside claim scope.unit_ids: {sorted(unit_ids)}"]
    return []


def validate_record_scope(
    claim: dict[str, Any],
    section: str,
    record: dict,
    *,
    label: str,
    book_map: dict | None = None,
) -> list[str]:
    unit_ids = claim_scope_unit_ids(claim)
    if not unit_ids:
        return [f"{label} cannot be checked because claim scope.unit_ids is empty"]
    errors: list[str] = []
    if section in {"pages", "source_pages"}:
        errors += _validate_logical_page_in_scope(
            record.get("logical_page"),
            unit_ids,
            label=label,
            book_map=book_map,
        )
    elif section == "chapter_outputs":
        source_pages = record.get("source_pages")
        if isinstance(source_pages, list):
            for page_index, source_page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                if isinstance(source_page, dict):
                    errors += _validate_logical_page_in_scope(
                        source_page.get("logical_page"),
                        unit_ids,
                        label=page_label,
                        book_map=book_map,
                    )
                elif isinstance(source_page, int) and not isinstance(source_page, bool):
                    errors += _validate_logical_page_in_scope(
                        source_page,
                        unit_ids,
                        label=page_label,
                        book_map=book_map,
                    )
        else:
            output_id = record.get("id")
            if isinstance(output_id, str) and output_id.strip() and output_id.strip() not in unit_ids:
                errors.append(f"{label}.id is outside claim scope.unit_ids: {sorted(unit_ids)}")
    elif section == "blocks":
        output_id = record.get("output_id")
        if isinstance(output_id, str) and output_id.strip():
            if output_id.strip() not in unit_ids:
                errors.append(f"{label}.output_id is outside claim scope.unit_ids: {sorted(unit_ids)}")
        else:
            block_id = record.get("id")
            if isinstance(block_id, str) and block_id.strip() and block_id.strip() not in unit_ids:
                errors.append(f"{label}.id is outside claim scope.unit_ids: {sorted(unit_ids)}")
    elif section in {"glossary_proposals", "ambiguities"}:
        errors += _validate_logical_page_in_scope(
            record.get("logical_page"),
            unit_ids,
            label=label,
            book_map=book_map,
        )
        source_pages = record.get("source_pages")
        if isinstance(source_pages, list):
            for page_index, source_page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                logical_page = (
                    source_page.get("logical_page")
                    if isinstance(source_page, dict)
                    else source_page
                )
                errors += _validate_logical_page_in_scope(
                    logical_page,
                    unit_ids,
                    label=page_label,
                    book_map=book_map,
                )
    else:
        identifiers = record_scope_identifiers(section, record, book_map)
        if not identifiers or identifiers.isdisjoint(unit_ids):
            errors.append(f"{label} is outside claim scope.unit_ids: {sorted(unit_ids)}")
    if not errors and not record_scope_identifiers(section, record, book_map).intersection(unit_ids):
        errors.append(f"{label} is outside claim scope.unit_ids: {sorted(unit_ids)}")
    return errors


def validate_claim_context(
    claim_map: Any,
    claim_id: str,
    shard: Any,
    *,
    kind: str,
    text_root: Path,
    shard_path: Path | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    book_root = book_root_from_text_root(text_root)
    errors = validate_claim_map(claim_map, book_root)
    if not isinstance(claim_map, dict):
        return None, errors
    if not isinstance(claim_map.get("claims"), list):
        return None, errors
    claim_index = claims_by_id(claim_map)
    claim = claim_index.get(claim_id)
    if claim is None:
        errors.append(f"claim-id is not present in claim map: {claim_id}")
    if isinstance(shard, dict) and shard.get("claim_id") != claim_id:
        errors.append(f"shard.claim_id must match --claim-id: {claim_id}")
    errors += validate_shard(shard, kind, claim_index, label="shard")
    if claim is not None:
        expected_stage = SHARD_STAGE_BY_KIND[kind]
        if claim.get("stage") != expected_stage:
            errors.append(
                f"claim stage {claim.get('stage')!r} is incompatible with {kind} shard; "
                f"expected {expected_stage}"
            )
        errors += _validate_claim_shard_path(claim, shard_path, book_root)
    return claim, errors


def page_requires_text(page: dict) -> bool:
    if page.get("blank") is True:
        return False
    return page.get("kind") not in {"ignored", "excluded"}


def chapter_output_records(ledger: object) -> dict[str, dict]:
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


def expected_chapter_outputs(book_map: dict, text_root: Path) -> tuple[dict[str, str], list[str]]:
    expected: dict[str, str] = {}
    errors: list[str] = []
    chapters_root = text_root / "source" / "chapters"
    if chapters_root.is_dir():
        for front_file in sorted(chapters_root.glob("front-*.txt")):
            parts = front_file.stem.split("-", 2)
            if len(parts) < 2 or not parts[1]:
                errors.append(f"Cannot derive a front-matter output id from {front_file.name}")
                continue
            output_id = f"front-{parts[1]}"
            if output_id in expected:
                errors.append(f"Duplicate chapter output id derived from source files: {output_id}")
                continue
            expected[output_id] = front_file.relative_to(text_root).as_posix()

    for chapter in sorted(book_map.get("chapters", []), key=lambda entry: entry.get("number", 0)):
        if not isinstance(chapter, dict):
            continue
        chapter_id = chapter.get("id")
        number = chapter.get("number")
        if not isinstance(chapter_id, str) or not chapter_id.strip() or not isinstance(number, int):
            errors.append("Each mapped chapter needs a non-empty id and integer number")
            continue
        matches = sorted(chapters_root.glob(f"chapter-{number:02d}-*.txt")) if chapters_root.is_dir() else []
        if len(matches) != 1:
            errors.append(f"Expected exactly one chapter source TXT for {chapter_id}")
            continue
        if chapter_id in expected:
            errors.append(f"Duplicate chapter output id in book map: {chapter_id}")
            continue
        expected[chapter_id] = matches[0].relative_to(text_root).as_posix()

    if expected:
        return expected, errors

    fallback = text_root / "source" / "book.txt"
    if not fallback.is_file():
        errors.append("No front, chapter, or book source TXT files are available for EPUB export")
    else:
        expected["book"] = fallback.relative_to(text_root).as_posix()
    return expected, errors


def verify_chapter_outputs(
    book_map: dict,
    ledger: dict,
    ledger_by_page: dict[int, dict],
    text_root: Path,
) -> list[str]:
    outputs = ledger.get("chapter_outputs")
    if not isinstance(outputs, list) or not outputs:
        return ["ledger must include non-empty chapter_outputs before EPUB export"]

    errors: list[str] = []
    expected, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    ids: set[str] = set()
    pages_by_output: dict[str, set[int]] = {}
    all_output_pages: dict[int, str] = {}
    for index, entry in enumerate(outputs):
        label = f"ledger.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not isinstance(output_id, str) or not output_id.strip():
            errors.append(f"{label}.id must be non-empty")
        elif output_id in ids:
            errors.append(f"{label}.id is duplicated: {output_id}")
        else:
            ids.add(output_id)

        relative_path = entry.get("source_file")
        source_file = resolve_under(
            text_root,
            relative_path,
            (Path("source") / "chapters", Path("source") / "book.txt"),
        )
        chapters_root = (text_root / "source" / "chapters").resolve()
        book_file = (text_root / "source" / "book.txt").resolve()
        if source_file is None or not (
            source_file == book_file or source_file.is_relative_to(chapters_root)
        ):
            errors.append(f"{label}.source_file must resolve under source/chapters or source/book.txt")
        elif not source_file.is_file() or not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.source_file is missing or empty: {relative_path}")
        elif entry.get("source_sha256") != sha256_file(source_file):
            errors.append(f"{label}.source_sha256 does not match source_file")
        normalized_path = str(relative_path).replace("\\", "/") if isinstance(relative_path, str) else ""
        if isinstance(output_id, str) and output_id in expected and normalized_path != expected[output_id]:
            errors.append(
                f"{label}.source_file does not match the expected source TXT for {output_id}"
            )

        source_pages = entry.get("source_pages")
        output_pages: set[int] = set()
        if not isinstance(source_pages, list) or not source_pages:
            errors.append(f"{label}.source_pages must be a non-empty array")
        else:
            seen_pages: set[int] = set()
            for page_index, source_page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                if not isinstance(source_page, dict):
                    errors.append(f"{page_label} must be an object")
                    continue
                logical_page = source_page.get("logical_page")
                if not isinstance(logical_page, int) or logical_page <= 0:
                    errors.append(f"{page_label}.logical_page must be positive")
                    continue
                if logical_page in seen_pages:
                    errors.append(f"{page_label}.logical_page is duplicated: {logical_page}")
                    continue
                seen_pages.add(logical_page)
                output_pages.add(logical_page)
                previous_output = all_output_pages.get(logical_page)
                if previous_output is not None and previous_output != output_id:
                    errors.append(
                        f"{page_label}.logical_page is already claimed by chapter output {previous_output}"
                    )
                elif isinstance(output_id, str):
                    all_output_pages[logical_page] = output_id
                page_ledger = ledger_by_page.get(logical_page)
                if not isinstance(page_ledger, dict) or page_ledger.get("status") != "verified":
                    errors.append(f"{page_label} must reference a verified ledger page")
                    continue
                if source_page.get("source_sha256") != page_ledger.get("source_sha256"):
                    errors.append(f"{page_label}.source_sha256 does not match the page ledger")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"{label}.verified_by must be non-empty")
        if isinstance(output_id, str) and output_id.strip():
            pages_by_output[output_id] = output_pages

    missing_outputs = sorted(set(expected) - ids)
    if missing_outputs:
        errors.append(f"ledger.chapter_outputs is missing expected records: {missing_outputs}")
    extra_outputs = sorted(ids - set(expected))
    if extra_outputs:
        errors.append(f"ledger.chapter_outputs contains unknown records: {extra_outputs}")

    required_pages = {
        page.get("logical_page")
        for page in book_map.get("pages", [])
        if isinstance(page, dict)
        and isinstance(page.get("logical_page"), int)
        and page_requires_text(page)
    }
    chapters_by_id = {
        chapter.get("id"): chapter
        for chapter in book_map.get("chapters", [])
        if isinstance(chapter, dict) and isinstance(chapter.get("id"), str)
    }
    for output_id in set(expected) & set(pages_by_output):
        expected_pages: set[int] | None = None
        if output_id == "book":
            expected_pages = required_pages
        elif output_id in chapters_by_id:
            expected_pages = {
                page.get("logical_page")
                for page in book_map.get("pages", [])
                if isinstance(page, dict)
                and page.get("chapter_id") == output_id
                and isinstance(page.get("logical_page"), int)
                and page_requires_text(page)
            }
        if expected_pages is not None and pages_by_output[output_id] != expected_pages:
            errors.append(
                f"chapter output {output_id} must reference exactly its mapped verified pages"
            )

    front_outputs = {output_id for output_id in expected if output_id.startswith("front-")}
    if front_outputs:
        expected_front_pages = {
            page.get("logical_page")
            for page in book_map.get("pages", [])
            if isinstance(page, dict)
            and not page.get("chapter_id")
            and isinstance(page.get("logical_page"), int)
            and page_requires_text(page)
        }
        actual_front_pages = set().union(
            *(pages_by_output.get(output_id, set()) for output_id in front_outputs)
        )
        if actual_front_pages != expected_front_pages:
            errors.append("front-matter chapter outputs must reference exactly the unmapped verified pages")
    return errors


def verify(
    book_map: object,
    book_map_sha256: str,
    ledger: object,
    text_root: Path,
    require_locutor: bool,
    require_chapter_outputs: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(book_map, dict) or not isinstance(book_map.get("pages"), list):
        return ["book map must include pages"]
    if not isinstance(ledger, dict) or not isinstance(ledger.get("pages"), list):
        return ["ledger must include pages"]
    if ledger.get("book_map_sha256") != book_map_sha256:
        return ["ledger.book_map_sha256 does not match the current book-map.json"]

    source_pages = [page for page in book_map["pages"] if isinstance(page, dict)]
    ledger_by_page: dict[int, dict] = {}
    for index, entry in enumerate(ledger["pages"]):
        label = f"ledger.pages[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
            continue
        if logical_page in ledger_by_page:
            errors.append(f"{label}.logical_page is duplicated: {logical_page}")
            continue
        ledger_by_page[logical_page] = entry

    mapped_numbers = {page.get("logical_page") for page in source_pages}
    extra_numbers = sorted(set(ledger_by_page) - mapped_numbers)
    if extra_numbers:
        errors.append(f"ledger contains unmapped logical pages: {extra_numbers}")

    for page in source_pages:
        logical_page = page.get("logical_page")
        entry = ledger_by_page.get(logical_page)
        if entry is None:
            errors.append(f"logical page {logical_page} is missing from the ledger")
            continue
        status = entry.get("status")
        if status not in LEDGER_STATES:
            errors.append(f"logical page {logical_page} has invalid status: {status!r}")
            continue

        needs_text = page_requires_text(page)
        if needs_text and status != "verified":
            errors.append(f"logical page {logical_page} requires verified source text")
            continue
        if not needs_text and status == "verified":
            errors.append(f"logical page {logical_page} is blank/excluded but is marked verified")
            continue
        if status != "verified":
            if not str(entry.get("notes", "")).strip():
                errors.append(f"logical page {logical_page} needs notes for status {status}")
            continue

        relative_path = entry.get("source_file")
        if not isinstance(relative_path, str) or not relative_path.strip():
            errors.append(f"logical page {logical_page} needs source_file")
            continue
        source_file = resolve_under(
            text_root,
            relative_path,
            (Path("source") / "pages",),
        )
        if source_file is None:
            errors.append(
                f"logical page {logical_page} source path must resolve under source/pages: {relative_path}"
            )
            continue
        if not source_file.is_file():
            errors.append(f"logical page {logical_page} source file is missing: {relative_path}")
            continue
        if not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"logical page {logical_page} source file is empty: {relative_path}")
            continue
        actual_hash = sha256_file(source_file)
        if entry.get("source_sha256") != actual_hash:
            errors.append(f"logical page {logical_page} source SHA-256 does not match")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"logical page {logical_page} is verified without verified_by")

        if require_locutor:
            locutor_path = entry.get("locutor_file")
            if not isinstance(locutor_path, str) or not locutor_path.strip():
                errors.append(f"logical page {logical_page} needs locutor_file")
                continue
            locutor_file = resolve_under(
                text_root,
                locutor_path,
                (Path("locutor"),),
            )
            if locutor_file is None:
                errors.append(
                    f"logical page {logical_page} locutor path must resolve under locutor: {locutor_path}"
                )
                continue
            if not locutor_file.is_file() or not locutor_file.read_text(encoding="utf-8").strip():
                errors.append(f"logical page {logical_page} locutor file is missing or empty")
                continue
            if entry.get("locutor_sha256") != sha256_file(locutor_file):
                errors.append(f"logical page {logical_page} locutor SHA-256 does not match")

    if require_chapter_outputs:
        errors += verify_chapter_outputs(book_map, ledger, ledger_by_page, text_root)

    return errors


def verify_claim(
    book_map: object,
    book_map_sha256: str,
    shard: object,
    claim_map: object,
    claim_id: str,
    text_root: Path,
    require_locutor: bool,
    shard_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(book_map, dict) or not isinstance(book_map.get("pages"), list):
        return ["book map must include pages"]
    claim, context_errors = validate_claim_context(
        claim_map,
        claim_id,
        shard,
        kind="text",
        text_root=text_root,
        shard_path=shard_path,
    )
    errors += context_errors
    if not isinstance(shard, dict):
        return errors
    payload = shard.get("text")
    if not isinstance(payload, dict):
        return errors + ["shard.text must be an object"]
    if claim is None:
        return errors

    owned_pages = claim_owned_logical_pages(book_map, claim)
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return errors + ["shard.text.pages must be an array"]
    mapped_pages = page_records_by_number(book_map)
    ledger_by_page: dict[int, dict] = {}
    for index, entry in enumerate(pages):
        label = f"shard.text.pages[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        errors += validate_record_scope(claim, "pages", entry, label=label, book_map=book_map)
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
            continue
        if logical_page in ledger_by_page:
            errors.append(f"{label}.logical_page is duplicated: {logical_page}")
            continue
        ledger_by_page[logical_page] = entry
        mapped_page = mapped_pages.get(logical_page)
        if mapped_page is None:
            errors.append(f"{label}.logical_page is not mapped by book-map.json")
            continue

        status = entry.get("status")
        if status not in LEDGER_STATES:
            errors.append(f"logical page {logical_page} has invalid status: {status!r}")
            continue
        needs_text = page_requires_text(mapped_page)
        if needs_text and status != "verified":
            errors.append(f"logical page {logical_page} requires verified source text")
            continue
        if not needs_text and status == "verified":
            errors.append(f"logical page {logical_page} is blank/excluded but is marked verified")
            continue
        if status != "verified":
            if not str(entry.get("notes", "")).strip():
                errors.append(f"logical page {logical_page} needs notes for status {status}")
            continue

        relative_path = entry.get("source_file")
        errors += validate_claim_file_target(
            claim,
            relative_path,
            label=f"{label}.source_file",
        )
        source_file = resolve_under(text_root, relative_path, (Path("source") / "pages",))
        if source_file is None:
            errors.append(
                f"logical page {logical_page} source path must resolve under source/pages: {relative_path}"
            )
            continue
        if not source_file.is_file():
            errors.append(f"logical page {logical_page} source file is missing: {relative_path}")
            continue
        if not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"logical page {logical_page} source file is empty: {relative_path}")
            continue
        if entry.get("source_sha256") != sha256_file(source_file):
            errors.append(f"logical page {logical_page} source SHA-256 does not match")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"logical page {logical_page} is verified without verified_by")

        if require_locutor:
            locutor_path = entry.get("locutor_file")
            errors += validate_claim_file_target(
                claim,
                locutor_path,
                label=f"{label}.locutor_file",
            )
            locutor_file = resolve_under(text_root, locutor_path, (Path("locutor"),))
            if locutor_file is None:
                errors.append(
                    f"logical page {logical_page} locutor path must resolve under locutor: {locutor_path}"
                )
                continue
            if not locutor_file.is_file() or not locutor_file.read_text(encoding="utf-8").strip():
                errors.append(f"logical page {logical_page} locutor file is missing or empty")
                continue
            if entry.get("locutor_sha256") != sha256_file(locutor_file):
                errors.append(f"logical page {logical_page} locutor SHA-256 does not match")
    errors += validate_exact_page_coverage(owned_pages, set(ledger_by_page), label="shard.text.pages")

    outputs = payload.get("chapter_outputs")
    if not isinstance(outputs, list):
        return errors + ["shard.text.chapter_outputs must be an array"]
    output_ids: set[str] = set()
    all_output_pages: dict[int, str] = {}
    for index, entry in enumerate(outputs):
        label = f"shard.text.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        errors += validate_record_scope(claim, "chapter_outputs", entry, label=label, book_map=book_map)
        output_id = entry.get("id")
        if not isinstance(output_id, str) or not output_id.strip():
            errors.append(f"{label}.id must be non-empty")
        elif output_id in output_ids:
            errors.append(f"{label}.id is duplicated: {output_id}")
        else:
            output_ids.add(output_id)
        relative_path = entry.get("source_file")
        errors += validate_claim_file_target(
            claim,
            relative_path,
            label=f"{label}.source_file",
        )
        source_file = resolve_under(
            text_root,
            relative_path,
            (Path("source") / "chapters", Path("source") / "book.txt"),
        )
        if source_file is None:
            errors.append(f"{label}.source_file must resolve under source/chapters or source/book.txt")
        elif not source_file.is_file() or not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.source_file is missing or empty: {relative_path}")
        elif entry.get("source_sha256") != sha256_file(source_file):
            errors.append(f"{label}.source_sha256 does not match source_file")
        source_pages = entry.get("source_pages")
        if not isinstance(source_pages, list) or not source_pages:
            errors.append(f"{label}.source_pages must be a non-empty array")
        else:
            seen_pages: set[int] = set()
            for page_index, source_page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                if not isinstance(source_page, dict):
                    errors.append(f"{page_label} must be an object")
                    continue
                logical_page = source_page.get("logical_page")
                if not isinstance(logical_page, int) or logical_page <= 0:
                    errors.append(f"{page_label}.logical_page must be positive")
                    continue
                if logical_page in seen_pages:
                    errors.append(f"{page_label}.logical_page is duplicated: {logical_page}")
                    continue
                seen_pages.add(logical_page)
                if logical_page not in mapped_pages:
                    errors.append(f"{page_label}.logical_page is not mapped by book-map.json")
                prior_output = all_output_pages.get(logical_page)
                if prior_output is not None and prior_output != output_id:
                    errors.append(
                        f"{page_label}.logical_page is already claimed by chapter output {prior_output}"
                    )
                elif isinstance(output_id, str):
                    all_output_pages[logical_page] = output_id
                page_ledger = ledger_by_page.get(logical_page)
                if not isinstance(page_ledger, dict):
                    errors.append(f"{page_label} must reference a page validated by this shard")
                elif page_ledger.get("status") != "verified":
                    errors.append(f"{page_label} must reference a verified page validated by this shard")
                elif source_page.get("source_sha256") != page_ledger.get("source_sha256"):
                    errors.append(f"{page_label}.source_sha256 does not match the page ledger")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"{label}.verified_by must be non-empty")
    errors += validate_exact_page_coverage(
        owned_pages,
        set(all_output_pages),
        label="shard.text.chapter_outputs.source_pages",
    )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify source text coverage against an Audiobook Codex page ledger.")
    parser.add_argument("--mode", choices=("approval", "claim"), default="approval")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--require-locutor", action="store_true")
    parser.add_argument("--require-chapter-outputs", action="store_true")
    parser.add_argument("--claim-map", type=Path)
    parser.add_argument("--claim-id")
    parser.add_argument("--shard", type=Path)
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        if args.mode == "approval":
            if args.ledger is None:
                parser.error("--ledger is required in approval mode")
            errors = verify(
                load_json(map_path),
                sha256_file(map_path),
                load_json(args.ledger.expanduser().resolve()),
                args.text_root.expanduser().resolve(),
                args.require_locutor,
                args.require_chapter_outputs,
            )
        else:
            if args.claim_map is None or args.claim_id is None or args.shard is None:
                parser.error("--claim-map, --claim-id, and --shard are required in claim mode")
            shard_path = args.shard.expanduser().resolve()
            errors = verify_claim(
                load_json(map_path),
                sha256_file(map_path),
                load_json(shard_path),
                load_json(args.claim_map.expanduser().resolve()),
                args.claim_id,
                args.text_root.expanduser().resolve(),
                args.require_locutor,
                shard_path,
            )
    except RuntimeError as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID text ledger:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID text ledger")


if __name__ == "__main__":
    main()
