from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from path_safety import resolve_under
from verify_text_ledger import (
    chapter_output_records,
    claim_owned_logical_pages,
    expected_chapter_outputs,
    validate_claim_context,
    validate_claim_file_target,
    validate_exact_page_coverage,
    validate_record_scope,
    verify as verify_text_ledger,
)
from verify_translation_ledger import (
    is_portuguese_language,
    translation_chapter_output_records,
    verify as verify_translation_ledger,
)


SCHEMA_VERSION = "1.2"
STYLE_SCHEMA_VERSION = "1.2"
SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1", SCHEMA_VERSION}
SUPPORTED_STYLE_SCHEMA_VERSIONS = {"1.0", "1.1", STYLE_SCHEMA_VERSION}
ARCHAIC_POLICY_SCHEMA_VERSIONS = {"1.1", "1.2"}
EDITORIAL_EXCLUSION_SCHEMA_VERSION = "1.2"
TARGET_LANGUAGE = "pt-BR"
TRANSLATION_ROOT = Path("translation") / TARGET_LANGUAGE
FLUID_PROFILE = "fluid-faithful-ptbr-v1"
FLUID_ROOT = Path("fluid") / TARGET_LANGUAGE

BASE_EDITIONS = {"source", "translated-pt-br"}
VOICE_FIELDS = ("register", "tone", "cadence", "terminology", "title_policy")
RULE_FIELDS = (
    "preserve_meaning",
    "no_added_content",
    "no_omitted_content",
    "modernize_grammar_and_lexicon",
    "reduce_redundancy",
    "clarify_referents",
    "preserve_examples_and_arguments",
    "preserve_authorial_stance",
)
ARCHAIC_POLICY_RULE_FIELDS = (
    "modernize_all_archaic_language",
    "modernize_historical_quotations",
    "modernize_orthography_and_diacritics",
)
EDITORIAL_EXCLUSION_RULE_FIELDS = (
    "omit_parenthetical_citation_references",
    "omit_immediate_duplicate_translations",
    "omit_translation_labels",
)
REVIEW_FIELDS = (
    "semantic_fidelity",
    "no_additions",
    "no_omissions",
    "fluency",
    "whole_book_consistency",
)
ARCHAIC_POLICY_REVIEW_FIELDS = ("archaic_modernization",)
EDITORIAL_EXCLUSION_REVIEW_FIELDS = ("editorial_exclusions",)
CHANGE_KINDS = {
    "preserved",
    "archaic_modernization",
    "citation_reference_exclusion",
    "duplicate_translation_exclusion",
    "translation_label_exclusion",
    "redundancy_reduction",
    "syntactic_simplification",
    "clarity",
    "fluency",
    "punctuation",
    "foreign_quotation_translation",
    "figure_caption_numbering",
}
FULL_BLOCK_EXCLUSION_KINDS = {
    "duplicate_translation_exclusion",
    "translation_label_exclusion",
}
EXCLUDED_BLOCK_CHANGE_KINDS = {
    "citation_reference_exclusion",
    *FULL_BLOCK_EXCLUSION_KINDS,
}
EDITORIAL_EXCLUSION_CHANGE_KINDS = {
    "citation_reference_exclusion",
    *FULL_BLOCK_EXCLUSION_KINDS,
}
LEGACY_CHANGE_KINDS = CHANGE_KINDS - EDITORIAL_EXCLUSION_CHANGE_KINDS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def fluid_chapter_output_records(ledger: object) -> dict[str, dict]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("chapter_outputs"), list):
        return {}
    records: dict[str, dict] = {}
    for entry in ledger["chapter_outputs"]:
        if not isinstance(entry, dict):
            continue
        output_id = entry.get("id")
        if require_text(output_id):
            records[output_id.strip()] = entry
    return records


def fluid_document_titles(ledger: object) -> dict[str, str]:
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
        if require_text(output_id) and require_text(title):
            titles[output_id.strip()] = title.strip()
    return titles


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _split_blocks(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [block.strip() for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]


def _validate_style(style: dict) -> list[str]:
    errors: list[str] = []
    schema_version = style.get("schema_version")
    if schema_version not in SUPPORTED_STYLE_SCHEMA_VERSIONS:
        errors.append(
            "fluid style schema_version must be one of "
            f"{sorted(SUPPORTED_STYLE_SCHEMA_VERSIONS)!r}"
        )
    if style.get("profile") != FLUID_PROFILE:
        errors.append(f"fluid style profile must be {FLUID_PROFILE!r}")
    if style.get("language") != TARGET_LANGUAGE:
        errors.append(f"fluid style language must be {TARGET_LANGUAGE!r}")
    if style.get("base_edition") not in BASE_EDITIONS:
        errors.append("fluid style base_edition must be source or translated-pt-br")

    voice = style.get("voice")
    if not isinstance(voice, dict):
        errors.append("fluid style voice must be an object")
    else:
        for field in VOICE_FIELDS:
            if not require_text(voice.get(field)):
                errors.append(f"fluid style voice.{field} must be non-empty")

    rules = style.get("rules")
    if not isinstance(rules, dict):
        errors.append("fluid style rules must be an object")
    else:
        for field in RULE_FIELDS:
            if rules.get(field) is not True:
                errors.append(f"fluid style rules.{field} must be true")
        if schema_version in ARCHAIC_POLICY_SCHEMA_VERSIONS:
            for field in ARCHAIC_POLICY_RULE_FIELDS:
                if rules.get(field) is not True:
                    errors.append(f"fluid style rules.{field} must be true")
        if schema_version == EDITORIAL_EXCLUSION_SCHEMA_VERSION:
            for field in EDITORIAL_EXCLUSION_RULE_FIELDS:
                if rules.get(field) is not True:
                    errors.append(f"fluid style rules.{field} must be true")

    glossary = style.get("glossary")
    if not isinstance(glossary, list):
        errors.append("fluid style glossary must be an array")
    else:
        seen_terms: set[str] = set()
        for index, entry in enumerate(glossary):
            label = f"fluid style glossary[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{label} must be an object")
                continue
            term = entry.get("term")
            if not require_text(term):
                errors.append(f"{label}.term must be non-empty")
            else:
                normalized = term.strip().casefold()
                if normalized in seen_terms:
                    errors.append(f"{label}.term is duplicated: {term.strip()}")
                seen_terms.add(normalized)
            for field in ("preferred_form", "reviewed_by"):
                if not require_text(entry.get(field)):
                    errors.append(f"{label}.{field} must be non-empty")
            if not isinstance(entry.get("notes"), str):
                errors.append(f"{label}.notes must be a string")
            if entry.get("status") != "approved":
                errors.append(f"{label}.status must be approved")

    if not require_text(style.get("reviewed_by")):
        errors.append("fluid style reviewed_by must be non-empty")
    return errors


def _base_records(base_edition: str, source_ledger: dict, translation_ledger: dict | None) -> dict[str, dict]:
    if base_edition == "translated-pt-br" and translation_ledger is not None:
        return translation_chapter_output_records(translation_ledger)
    return chapter_output_records(source_ledger)


def _base_file_and_hash(base_edition: str, output: dict) -> tuple[object, object]:
    if base_edition == "translated-pt-br":
        return output.get("translation_file"), output.get("translation_sha256")
    return output.get("source_file"), output.get("source_sha256")


def _validate_ledger_header(
    book_map: dict,
    book_map_sha256: str,
    source_ledger_sha256: str,
    translation_ledger_sha256: str | None,
    fluid_style: dict,
    fluid_style_sha256: str,
    fluid_ledger: dict,
) -> list[str]:
    errors: list[str] = []
    ledger_schema_version = fluid_ledger.get("schema_version")
    style_schema_version = fluid_style.get("schema_version")
    if ledger_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            "fluid ledger schema_version must be one of "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)!r}"
        )
    if (
        ledger_schema_version in SUPPORTED_SCHEMA_VERSIONS
        and style_schema_version in SUPPORTED_STYLE_SCHEMA_VERSIONS
        and ledger_schema_version != style_schema_version
    ):
        errors.append(
            "fluid style and ledger schema_version values must match"
        )
    if fluid_ledger.get("book_map_sha256") != book_map_sha256:
        errors.append("fluid ledger.book_map_sha256 does not match book-map.json")
    if fluid_ledger.get("text_ledger_sha256") != source_ledger_sha256:
        errors.append("fluid ledger.text_ledger_sha256 does not match text-ledger.json")
    base_edition = fluid_ledger.get("base_edition")
    if base_edition not in BASE_EDITIONS:
        errors.append("fluid ledger.base_edition must be source or translated-pt-br")
    elif fluid_style.get("base_edition") != base_edition:
        errors.append("fluid style base_edition must match fluid ledger.base_edition")
    expected_base_hash = source_ledger_sha256 if base_edition == "source" else translation_ledger_sha256
    if fluid_ledger.get("base_ledger_sha256") != expected_base_hash:
        errors.append("fluid ledger.base_ledger_sha256 does not match the selected base ledger")
    if fluid_ledger.get("fluid_style_sha256") != fluid_style_sha256:
        errors.append("fluid ledger.fluid_style_sha256 does not match fluid-style.json")
    if fluid_ledger.get("language") != TARGET_LANGUAGE:
        errors.append(f"fluid ledger.language must be {TARGET_LANGUAGE!r}")
    if fluid_ledger.get("profile") != FLUID_PROFILE:
        errors.append(f"fluid ledger.profile must be {FLUID_PROFILE!r}")
    if fluid_ledger.get("status") != "approved":
        errors.append("fluid ledger.status must be approved")
    edited_by = fluid_ledger.get("edited_by")
    reviewed_by = fluid_ledger.get("reviewed_by")
    if not require_text(edited_by):
        errors.append("fluid ledger.edited_by must be non-empty")
    if not require_text(reviewed_by):
        errors.append("fluid ledger.reviewed_by must be non-empty")
    review = fluid_ledger.get("review")
    review_reviewer = review.get("reviewed_by") if isinstance(review, dict) else None
    if require_text(reviewed_by) and require_text(review_reviewer):
        if reviewed_by.strip() != review_reviewer.strip():
            errors.append("fluid ledger.reviewed_by must match fluid ledger.review.reviewed_by")
    if require_text(edited_by) and require_text(reviewed_by) and edited_by.strip() == reviewed_by.strip():
        errors.append("fluid ledger.reviewed_by must differ from fluid ledger.edited_by")

    if base_edition == "source":
        analysis = book_map.get("analysis") if isinstance(book_map.get("analysis"), dict) else {}
        if not is_portuguese_language(analysis.get("source_language")):
            errors.append("source-base fluid edition requires analysis.source_language to be Portuguese")
    return errors


def _validate_edition(fluid_ledger: dict, expected_ids: list[str]) -> list[str]:
    errors: list[str] = []
    edition = fluid_ledger.get("edition")
    if not isinstance(edition, dict):
        return ["fluid ledger.edition must be an object"]
    book = edition.get("book")
    if not isinstance(book, dict) or not require_text(book.get("title")):
        errors.append("fluid ledger.edition.book.title must be non-empty")
    document_titles = edition.get("document_titles")
    if not isinstance(document_titles, list):
        return errors + ["fluid ledger.edition.document_titles must be an array"]
    titles: dict[str, str] = {}
    for index, entry in enumerate(document_titles):
        label = f"fluid ledger.edition.document_titles[{index}]"
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
    actual_ids = list(titles)
    if actual_ids != expected_ids:
        errors.append(
            "fluid ledger.edition.document_titles must exactly cover output ids in order: "
            f"{expected_ids}"
        )
    return errors


def _validate_review(fluid_ledger: dict, style_schema_version: object) -> list[str]:
    errors: list[str] = []
    review = fluid_ledger.get("review")
    if not isinstance(review, dict):
        return ["fluid ledger.review must be an object"]
    fields = REVIEW_FIELDS
    if style_schema_version in ARCHAIC_POLICY_SCHEMA_VERSIONS:
        fields += ARCHAIC_POLICY_REVIEW_FIELDS
    if style_schema_version == EDITORIAL_EXCLUSION_SCHEMA_VERSION:
        fields += EDITORIAL_EXCLUSION_REVIEW_FIELDS
    for field in fields:
        if review.get(field) != "approved":
            errors.append(f"fluid ledger.review.{field} must be approved")
    if review.get("independent") is not True:
        errors.append("fluid ledger.review.independent must be true")
    if not require_text(review.get("reviewed_by")):
        errors.append("fluid ledger.review.reviewed_by must be non-empty")
    return errors


def _validate_chapter_outputs_and_blocks(
    expected_ids: list[str],
    base_edition: str,
    base_outputs: dict[str, dict],
    fluid_ledger: dict,
    text_root: Path,
    schema_version: object,
) -> tuple[list[str], list[dict], list[str]]:
    errors: list[str] = []
    outputs = fluid_ledger.get("chapter_outputs")
    if not isinstance(outputs, list) or not outputs:
        return ["fluid ledger.chapter_outputs must be a non-empty array"], [], []

    output_records: dict[str, dict] = {}
    seen_fluid_paths: dict[Path, str] = {}
    expected_blocks: list[tuple[str, int, str, str]] = []
    expected_base_blocks: list[tuple[str, int, str]] = []
    fluid_block_hashes: dict[str, list[str]] = {}
    chapter_texts: list[str] = []

    for index, entry in enumerate(outputs):
        label = f"fluid ledger.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not require_text(output_id):
            errors.append(f"{label}.id must be non-empty")
            continue
        output_id = output_id.strip()
        if output_id in output_records:
            errors.append(f"{label}.id is duplicated: {output_id}")
            continue
        output_records[output_id] = entry

        base_output = base_outputs.get(output_id)
        if not isinstance(base_output, dict):
            errors.append(f"{label} has no validated base output")
            continue
        base_file, base_sha256 = _base_file_and_hash(base_edition, base_output)
        if entry.get("base_file") != base_file:
            errors.append(f"{label}.base_file does not match the selected base output")
        if entry.get("base_sha256") != base_sha256:
            errors.append(f"{label}.base_sha256 does not match the selected base output")
        if entry.get("source_pages") != base_output.get("source_pages"):
            errors.append(f"{label}.source_pages does not match the selected base output")

        fluid_file = entry.get("fluid_file")
        fluid_path = resolve_under(text_root, fluid_file, (FLUID_ROOT / "chapters",))
        if fluid_path is None:
            errors.append(f"{label}.fluid_file must resolve under {FLUID_ROOT.as_posix()}/chapters")
            continue
        previous_label = seen_fluid_paths.get(fluid_path)
        if previous_label is not None:
            errors.append(f"{label}.fluid_file resolves to duplicate fluid chapter path used by {previous_label}")
            continue
        seen_fluid_paths[fluid_path] = label
        if not fluid_path.is_file() or not fluid_path.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.fluid_file is missing or empty")
            continue
        if entry.get("fluid_sha256") != sha256_file(fluid_path):
            errors.append(f"{label}.fluid_sha256 does not match fluid_file")

        base_path = resolve_under(text_root, base_file, (Path("source") / "chapters", TRANSLATION_ROOT / "chapters"))
        if base_path is None or not base_path.is_file():
            errors.append(f"{label}.base_file is not available")
            continue
        base_text = base_path.read_text(encoding="utf-8")
        fluid_text = fluid_path.read_text(encoding="utf-8")
        chapter_texts.append(fluid_text)
        base_blocks = _split_blocks(base_text)
        fluid_blocks = _split_blocks(fluid_text)
        if schema_version == EDITORIAL_EXCLUSION_SCHEMA_VERSION:
            if entry.get("base_block_count") != len(base_blocks):
                errors.append(
                    f"{label}.base_block_count must match the base block count"
                )
            if entry.get("fluid_block_count") != len(fluid_blocks):
                errors.append(
                    f"{label}.fluid_block_count must match the fluid block count"
                )
            expected_base_blocks.extend(
                (output_id, position, sha256_text(base_block.strip()))
                for position, base_block in enumerate(base_blocks, start=1)
            )
            fluid_block_hashes[output_id] = [
                sha256_text(fluid_block.strip()) for fluid_block in fluid_blocks
            ]
        else:
            if len(base_blocks) != len(fluid_blocks):
                errors.append(f"{label} base and fluid block counts must match")
            if entry.get("block_count") != len(base_blocks):
                errors.append(f"{label}.block_count must match the base block count")
            for position, (base_block, fluid_block) in enumerate(
                zip(base_blocks, fluid_blocks),
                start=1,
            ):
                expected_blocks.append(
                    (
                        output_id,
                        position,
                        sha256_text(base_block.strip()),
                        sha256_text(fluid_block.strip()),
                    )
                )
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    actual_ids = list(output_records)
    if actual_ids != expected_ids:
        errors.append(
            "fluid ledger.chapter_outputs must exactly preserve source output order: "
            f"{expected_ids}"
        )

    if schema_version == EDITORIAL_EXCLUSION_SCHEMA_VERSION:
        errors += _validate_blocks_with_exclusions(
            fluid_ledger.get("blocks"),
            expected_base_blocks,
            fluid_block_hashes,
        )
    else:
        errors += _validate_blocks(fluid_ledger.get("blocks"), expected_blocks)
    return errors, list(output_records.values()), chapter_texts


def _validate_blocks(blocks: object, expected_blocks: list[tuple[str, int, str, str]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(blocks, list) or not blocks:
        return ["fluid ledger.blocks must be a non-empty array"]
    if len(blocks) != len(expected_blocks):
        errors.append("fluid ledger.blocks must exactly cover all base/fluid text blocks")

    seen_ids: set[str] = set()
    actual_positions: list[tuple[str, int]] = []
    for index, entry in enumerate(blocks):
        label = f"fluid ledger.blocks[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        block_id = entry.get("id")
        if not require_text(block_id):
            errors.append(f"{label}.id must be non-empty")
        elif block_id.strip() in seen_ids:
            errors.append(f"{label}.id is duplicated: {block_id.strip()}")
        else:
            seen_ids.add(block_id.strip())
        output_id = entry.get("output_id")
        position = entry.get("position")
        if not require_text(output_id):
            errors.append(f"{label}.output_id must be non-empty")
            output_id = ""
        else:
            output_id = output_id.strip()
        if not isinstance(position, int) or isinstance(position, bool) or position <= 0:
            errors.append(f"{label}.position must be positive")
            position = 0
        actual_positions.append((output_id, position))

        kinds = entry.get("change_kinds")
        if not isinstance(kinds, list) or not kinds:
            errors.append(f"{label}.change_kinds must be a non-empty array")
        else:
            invalid: list[str] = []
            for kind_index, kind in enumerate(kinds):
                if not isinstance(kind, str):
                    errors.append(f"{label}.change_kinds[{kind_index}] must be a string")
                elif kind not in LEGACY_CHANGE_KINDS:
                    invalid.append(kind)
            if invalid:
                errors.append(f"{label}.change_kinds contains invalid values: {sorted(set(invalid))}")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

        if index < len(expected_blocks):
            expected_output, expected_position, expected_base_hash, expected_fluid_hash = expected_blocks[index]
            if (output_id, position) != (expected_output, expected_position):
                errors.append(
                    f"{label} must cover {expected_output} block {expected_position} in order"
                )
            if entry.get("base_sha256") != expected_base_hash:
                errors.append(f"{label}.base_sha256 does not match base block text")
            if entry.get("fluid_sha256") != expected_fluid_hash:
                errors.append(f"{label}.fluid_sha256 does not match fluid block text")

    expected_positions = [(output_id, position) for output_id, position, _, _ in expected_blocks]
    if actual_positions != expected_positions:
        errors.append("fluid ledger.blocks must cover each chapter block exactly once in order")
    return errors


def _validate_blocks_with_exclusions(
    blocks: object,
    expected_base_blocks: list[tuple[str, int, str]],
    fluid_block_hashes: dict[str, list[str]],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(blocks, list) or not blocks:
        return ["fluid ledger.blocks must be a non-empty array"]
    if len(blocks) != len(expected_base_blocks):
        errors.append("fluid ledger.blocks must exactly cover all base text blocks")

    seen_ids: set[str] = set()
    actual_positions: list[tuple[str, int]] = []
    next_fluid_position = {output_id: 1 for output_id in fluid_block_hashes}

    for index, entry in enumerate(blocks):
        label = f"fluid ledger.blocks[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue

        block_id = entry.get("id")
        if not require_text(block_id):
            errors.append(f"{label}.id must be non-empty")
        elif block_id.strip() in seen_ids:
            errors.append(f"{label}.id is duplicated: {block_id.strip()}")
        else:
            seen_ids.add(block_id.strip())

        output_id = entry.get("output_id")
        position = entry.get("position")
        if not require_text(output_id):
            errors.append(f"{label}.output_id must be non-empty")
            output_id = ""
        else:
            output_id = output_id.strip()
        if not isinstance(position, int) or isinstance(position, bool) or position <= 0:
            errors.append(f"{label}.position must be positive")
            position = 0
        actual_positions.append((output_id, position))

        kinds = entry.get("change_kinds")
        valid_kinds: set[str] = set()
        if not isinstance(kinds, list) or not kinds:
            errors.append(f"{label}.change_kinds must be a non-empty array")
        else:
            invalid: list[str] = []
            for kind_index, kind in enumerate(kinds):
                if not isinstance(kind, str):
                    errors.append(f"{label}.change_kinds[{kind_index}] must be a string")
                elif kind not in CHANGE_KINDS:
                    invalid.append(kind)
                else:
                    valid_kinds.add(kind)
            if invalid:
                errors.append(
                    f"{label}.change_kinds contains invalid values: "
                    f"{sorted(set(invalid))}"
                )
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

        expected_base_hash = None
        if index < len(expected_base_blocks):
            expected_output, expected_position, expected_base_hash = expected_base_blocks[index]
            if (output_id, position) != (expected_output, expected_position):
                errors.append(
                    f"{label} must cover {expected_output} block {expected_position} in order"
                )
            if entry.get("base_sha256") != expected_base_hash:
                errors.append(f"{label}.base_sha256 does not match base block text")

        status = entry.get("status")
        whole_block_exclusion_kinds = valid_kinds.intersection(
            FULL_BLOCK_EXCLUSION_KINDS
        )
        if status == "included":
            fluid_position = entry.get("fluid_position")
            expected_fluid_position = next_fluid_position.get(output_id, 1)
            if (
                not isinstance(fluid_position, int)
                or isinstance(fluid_position, bool)
                or fluid_position <= 0
            ):
                errors.append(f"{label}.fluid_position must be positive for an included block")
            elif fluid_position != expected_fluid_position:
                errors.append(
                    f"{label}.fluid_position must be {expected_fluid_position} "
                    "to preserve fluid block order"
                )
            else:
                output_hashes = fluid_block_hashes.get(output_id, [])
                if fluid_position > len(output_hashes):
                    errors.append(f"{label}.fluid_position exceeds its fluid chapter")
                elif entry.get("fluid_sha256") != output_hashes[fluid_position - 1]:
                    errors.append(f"{label}.fluid_sha256 does not match fluid block text")
                next_fluid_position[output_id] = expected_fluid_position + 1
            if whole_block_exclusion_kinds:
                errors.append(
                    f"{label} included blocks cannot use whole-block exclusion change kinds"
                )
            if (
                "citation_reference_exclusion" in valid_kinds
                and expected_base_hash is not None
                and entry.get("fluid_sha256") == expected_base_hash
            ):
                errors.append(
                    f"{label} citation_reference_exclusion must change the fluid block text"
                )
        elif status == "excluded":
            if entry.get("fluid_position") is not None:
                errors.append(f"{label}.fluid_position must be null for an excluded block")
            if entry.get("fluid_sha256") is not None:
                errors.append(f"{label}.fluid_sha256 must be null for an excluded block")
            exclusion_kinds = valid_kinds.intersection(
                EXCLUDED_BLOCK_CHANGE_KINDS
            )
            if not exclusion_kinds:
                errors.append(
                    f"{label} excluded blocks require citation_reference_exclusion, "
                    "duplicate_translation_exclusion, or translation_label_exclusion"
                )
            unsupported_kinds = valid_kinds - EXCLUDED_BLOCK_CHANGE_KINDS
            if unsupported_kinds:
                errors.append(
                    f"{label} excluded blocks cannot use non-exclusion change kinds: "
                    f"{sorted(unsupported_kinds)}"
                )
        else:
            errors.append(f"{label}.status must be included or excluded")

    expected_positions = [
        (output_id, position)
        for output_id, position, _base_hash in expected_base_blocks
    ]
    if actual_positions != expected_positions:
        errors.append(
            "fluid ledger.blocks must cover each base chapter block exactly once in order"
        )
    for output_id, hashes in fluid_block_hashes.items():
        covered = next_fluid_position.get(output_id, 1) - 1
        if covered != len(hashes):
            errors.append(
                f"fluid ledger.blocks must cover every fluid block for {output_id} "
                "exactly once in order"
            )
    return errors


def _validate_book_output(
    fluid_ledger: dict,
    expected_ids: list[str],
    chapter_texts: list[str],
    text_root: Path,
) -> list[str]:
    errors: list[str] = []
    book_output = fluid_ledger.get("book_output")
    if not isinstance(book_output, dict):
        return ["fluid ledger.book_output must be an object"]
    if book_output.get("fluid_file") != (FLUID_ROOT / "book.txt").as_posix():
        errors.append(f"fluid ledger.book_output.fluid_file must be {(FLUID_ROOT / 'book.txt').as_posix()}")
    book_path = resolve_under(text_root, book_output.get("fluid_file"), (FLUID_ROOT / "book.txt",))
    if book_path is None or book_path != (text_root / FLUID_ROOT / "book.txt").resolve():
        errors.append("fluid ledger.book_output.fluid_file must resolve exactly under fluid/pt-BR/book.txt")
    elif not book_path.is_file() or not book_path.read_text(encoding="utf-8").strip():
        errors.append("fluid ledger.book_output.fluid_file is missing or empty")
    elif book_output.get("fluid_sha256") != sha256_file(book_path):
        errors.append("fluid ledger.book_output.fluid_sha256 does not match fluid_file")
    if book_output.get("chapter_ids") != expected_ids:
        errors.append("fluid ledger.book_output.chapter_ids must match ordered chapter output ids")
    if book_output.get("separator") != "double-newline":
        errors.append("fluid ledger.book_output.separator must be double-newline")
    if not require_text(book_output.get("reviewed_by")):
        errors.append("fluid ledger.book_output.reviewed_by must be non-empty")

    if book_path is not None and book_path.is_file() and len(chapter_texts) == len(expected_ids):
        canonical = "\n\n".join(text.rstrip() for text in chapter_texts) + "\n"
        if book_path.read_text(encoding="utf-8") != canonical:
            errors.append("fluid ledger.book_output.fluid_file does not equal the canonical chapter join")
    return errors


def verify(
    book_map: object,
    book_map_sha256: str,
    source_ledger: object,
    source_ledger_sha256: str,
    translation_ledger: object | None,
    translation_ledger_sha256: str | None,
    fluid_style: object,
    fluid_style_sha256: str,
    fluid_ledger: object,
    text_root: Path,
) -> list[str]:
    if not isinstance(book_map, dict):
        return ["book map must be an object"]
    if not isinstance(source_ledger, dict):
        return ["source text ledger must be an object"]
    if not isinstance(fluid_style, dict):
        return ["fluid style must be an object"]
    if not isinstance(fluid_ledger, dict):
        return ["fluid ledger must be an object"]

    errors: list[str] = []
    errors += verify_text_ledger(
        book_map,
        book_map_sha256,
        source_ledger,
        text_root,
        False,
        True,
    )
    errors += _validate_style(fluid_style)
    errors += _validate_ledger_header(
        book_map,
        book_map_sha256,
        source_ledger_sha256,
        translation_ledger_sha256,
        fluid_style,
        fluid_style_sha256,
        fluid_ledger,
    )

    base_edition = fluid_ledger.get("base_edition")
    if base_edition == "translated-pt-br":
        if not isinstance(translation_ledger, dict) or not translation_ledger_sha256:
            errors.append("translated-pt-br fluid edition requires translation-ledger.json")
        else:
            errors += verify_translation_ledger(
                book_map,
                book_map_sha256,
                source_ledger,
                source_ledger_sha256,
                translation_ledger,
                text_root,
            )
    elif translation_ledger is not None and not isinstance(translation_ledger, dict):
        errors.append("translation ledger must be an object when provided")

    expected_outputs, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    expected_ids = list(expected_outputs)
    base_outputs = _base_records(
        base_edition if isinstance(base_edition, str) else "source",
        source_ledger,
        translation_ledger if isinstance(translation_ledger, dict) else None,
    )
    errors += _validate_edition(fluid_ledger, expected_ids)
    errors += _validate_review(fluid_ledger, fluid_style.get("schema_version"))
    chapter_errors, _, chapter_texts = _validate_chapter_outputs_and_blocks(
        expected_ids,
        base_edition if isinstance(base_edition, str) else "source",
        base_outputs,
        fluid_ledger,
        text_root,
        fluid_ledger.get("schema_version"),
    )
    errors += chapter_errors
    errors += _validate_book_output(fluid_ledger, expected_ids, chapter_texts, text_root)
    return errors


def verify_claim(
    book_map: object,
    book_map_sha256: str,
    source_ledger: object,
    source_ledger_sha256: str,
    translation_ledger: object | None,
    translation_ledger_sha256: str | None,
    fluid_style: object,
    fluid_style_sha256: str,
    shard: object,
    claim_map: object,
    claim_id: str,
    text_root: Path,
    shard_path: Path | None = None,
) -> list[str]:
    if not isinstance(book_map, dict):
        return ["book map must be an object"]
    if not isinstance(source_ledger, dict):
        return ["source text ledger must be an object"]
    if not isinstance(fluid_style, dict):
        return ["fluid style must be an object"]

    errors: list[str] = []
    if source_ledger.get("book_map_sha256") != book_map_sha256:
        errors.append("source text ledger.book_map_sha256 does not match book-map.json")
    claim, context_errors = validate_claim_context(
        claim_map,
        claim_id,
        shard,
        kind="fluid",
        text_root=text_root,
        shard_path=shard_path,
    )
    errors += context_errors
    errors += _validate_style(fluid_style)
    if not isinstance(shard, dict):
        return errors
    payload = shard.get("fluid")
    if not isinstance(payload, dict):
        return errors + ["shard.fluid must be an object"]
    if claim is None:
        return errors

    base_edition = fluid_style.get("base_edition")
    if base_edition not in BASE_EDITIONS:
        return errors + ["fluid style base_edition must be source or translated-pt-br"]
    if base_edition == "translated-pt-br" and not isinstance(translation_ledger, dict):
        errors.append("translated-pt-br fluid claim validation requires translation-ledger.json")
    elif base_edition == "translated-pt-br":
        if translation_ledger.get("book_map_sha256") != book_map_sha256:
            errors.append("translation ledger.book_map_sha256 does not match book-map.json")
        if translation_ledger.get("text_ledger_sha256") != source_ledger_sha256:
            errors.append("translation ledger.text_ledger_sha256 does not match text-ledger.json")
    elif translation_ledger is not None and not isinstance(translation_ledger, dict):
        errors.append("translation ledger must be an object when provided")

    outputs = payload.get("chapter_outputs")
    blocks = payload.get("blocks")
    if not isinstance(outputs, list):
        errors.append("shard.fluid.chapter_outputs must be an array")
        outputs = []
    if not isinstance(blocks, list):
        errors.append("shard.fluid.blocks must be an array")
        blocks = []

    for index, entry in enumerate(outputs):
        label = f"shard.fluid.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        errors += validate_record_scope(claim, "chapter_outputs", entry, label=label, book_map=book_map)
        errors += validate_claim_file_target(
            claim,
            entry.get("fluid_file"),
            label=f"{label}.fluid_file",
        )
    output_pages: set[int] = set()
    for entry in outputs:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_pages"), list):
            continue
        for source_page in entry["source_pages"]:
            logical_page = source_page.get("logical_page") if isinstance(source_page, dict) else source_page
            if isinstance(logical_page, int) and not isinstance(logical_page, bool) and logical_page > 0:
                output_pages.add(logical_page)
    errors += validate_exact_page_coverage(
        claim_owned_logical_pages(book_map, claim),
        output_pages,
        label="shard.fluid.chapter_outputs.source_pages",
    )
    for index, entry in enumerate(blocks):
        label = f"shard.fluid.blocks[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        errors += validate_record_scope(claim, "blocks", entry, label=label, book_map=book_map)

    if errors:
        return errors

    expected_ids = [
        entry["id"].strip()
        for entry in outputs
        if isinstance(entry, dict) and require_text(entry.get("id"))
    ]
    base_outputs = _base_records(
        base_edition,
        source_ledger,
        translation_ledger if isinstance(translation_ledger, dict) else None,
    )
    scoped_ledger = {
        "chapter_outputs": outputs,
        "blocks": blocks,
    }
    chapter_errors, _, _ = _validate_chapter_outputs_and_blocks(
        expected_ids,
        base_edition,
        base_outputs,
        scoped_ledger,
        text_root,
        fluid_style.get("schema_version"),
    )
    errors += chapter_errors
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an approved fluid PT-BR edition ledger.")
    parser.add_argument("--mode", choices=("approval", "claim"), default="approval")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--fluid-style", required=True, type=Path)
    parser.add_argument("--fluid-ledger", type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--translation-ledger", type=Path)
    parser.add_argument("--claim-map", type=Path)
    parser.add_argument("--claim-id")
    parser.add_argument("--shard", type=Path)
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        ledger_path = args.ledger.expanduser().resolve()
        translation_path = args.translation_ledger.expanduser().resolve() if args.translation_ledger else None
        style_path = args.fluid_style.expanduser().resolve()
        if args.mode == "approval":
            if args.fluid_ledger is None:
                parser.error("--fluid-ledger is required in approval mode")
            errors = verify(
                load_json(map_path),
                sha256_file(map_path),
                load_json(ledger_path),
                sha256_file(ledger_path),
                load_json(translation_path) if translation_path else None,
                sha256_file(translation_path) if translation_path else None,
                load_json(style_path),
                sha256_file(style_path),
                load_json(args.fluid_ledger.expanduser().resolve()),
                args.text_root.expanduser().resolve(),
            )
        else:
            if args.claim_map is None or args.claim_id is None or args.shard is None:
                parser.error("--claim-map, --claim-id, and --shard are required in claim mode")
            shard_path = args.shard.expanduser().resolve()
            errors = verify_claim(
                load_json(map_path),
                sha256_file(map_path),
                load_json(ledger_path),
                sha256_file(ledger_path),
                load_json(translation_path) if translation_path else None,
                sha256_file(translation_path) if translation_path else None,
                load_json(style_path),
                sha256_file(style_path),
                load_json(shard_path),
                load_json(args.claim_map.expanduser().resolve()),
                args.claim_id,
                args.text_root.expanduser().resolve(),
                shard_path,
            )
    except RuntimeError as error:
        errors = [str(error)]

    if errors:
        print("INVALID fluid edition ledger:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    if args.mode == "approval":
        print(f"VALID fluid edition ledger: {args.fluid_ledger.expanduser().resolve()}")
    else:
        print(f"VALID fluid edition claim shard: {args.shard.expanduser().resolve()}")


if __name__ == "__main__":
    main()
