from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from narrator_quality import (
    FINDING_KINDS,
    FINDING_STATUSES,
    LOCUTION_CATEGORIES,
    PRONUNCIATION_DECISIONS,
    PRONUNCIATION_KINDS,
    QUALITY_PROFILE,
    REVIEW_SCHEMA_VERSION,
    audit_narrator_quality,
    narrator_output_pages,
    narration_plan_continuation_lines,
    normalized_text,
    sha256_file,
)
from path_safety import resolve_under


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def _positive_pages(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(page, int) and page > 0 for page in value
    )


def _validate_review_scope(
    value: object,
    output_pages: set[int],
) -> tuple[list[str], set[int]]:
    if not isinstance(value, dict):
        return ["narrator review review_scope must be an object"], set()
    errors: list[str] = []
    categories = value.get("categories")
    if not isinstance(categories, list) or not categories or any(
        not require_text(category) or category not in LOCUTION_CATEGORIES
        for category in categories
    ):
        errors.append(
            "narrator review review_scope.categories must be a non-empty valid category array"
        )
    pages = value.get("logical_pages")
    if not _positive_pages(pages):
        errors.append(
            "narrator review review_scope.logical_pages must contain positive integers"
        )
        return errors, set()
    page_set = set(pages)
    if page_set != output_pages:
        errors.append(
            "narrator review review_scope.logical_pages must exactly cover the selected narrator output"
        )
    return errors, page_set


def _validate_pronunciation(
    value: object,
    locutor_text: str,
    review_pages: set[int],
) -> tuple[list[str], list[dict]]:
    if not isinstance(value, dict):
        return ["narrator review pronunciation_review must be an object"], []
    errors: list[str] = []
    entries_out: list[dict] = []
    if value.get("status") != "approved":
        errors.append("narrator review pronunciation_review.status must be approved")
    if not require_text(value.get("reviewed_by")):
        errors.append("narrator review pronunciation_review.reviewed_by must be non-empty")
    entries = value.get("entries")
    if not isinstance(entries, list):
        return (
            errors + ["narrator review pronunciation_review.entries must be an array"],
            entries_out,
        )
    for index, entry in enumerate(entries):
        label = f"narrator review pronunciation_review.entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        if not require_text(entry.get("term")):
            errors.append(f"{label}.term must be non-empty")
        if entry.get("kind") not in PRONUNCIATION_KINDS:
            errors.append(f"{label}.kind is invalid")
        if entry.get("decision") not in PRONUNCIATION_DECISIONS:
            errors.append(f"{label}.decision is invalid")
        if entry.get("decision") == "spoken_form" and not require_text(entry.get("spoken_form")):
            errors.append(f"{label}.spoken_form must be non-empty for spoken_form decisions")
        span = entry.get("locutor_span")
        if not require_text(span):
            errors.append(f"{label}.locutor_span must be non-empty")
        elif normalized_text(str(span)) not in locutor_text:
            errors.append(f"{label}.locutor_span does not occur in the narrator output")
        if not _positive_pages(entry.get("logical_pages")):
            errors.append(f"{label}.logical_pages must contain positive integers")
        elif not set(entry["logical_pages"]).issubset(review_pages):
            errors.append(f"{label}.logical_pages must be inside review_scope")
        if not require_text(entry.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")
        entries_out.append(entry)
    return errors, entries_out


def _pronunciation_entry_exists(
    entries: list[dict],
    span: str,
    expected_kind: str,
) -> bool:
    return any(
        entry.get("kind") == expected_kind and entry.get("locutor_span") == span
        for entry in entries
    )


def _pronunciation_span_entry_exists(entries: list[dict], span: str) -> bool:
    return any(entry.get("locutor_span") == span for entry in entries)


def _is_all_caps_multiword_line(context: str) -> bool:
    words = [token for token in context.split() if any(character.isalpha() for character in token)]
    return len(words) > 1 and all(
        token.upper() == token and token.lower() != token for token in words
    )


def validate_review(
    book_root: Path,
    narrator_review_path: Path,
    input_file: Path,
    narrator_changes_path: Path | None = None,
) -> tuple[list[str], dict | None]:
    errors: list[str] = []
    metadata_root = book_root / "metadata"
    text_root = book_root / "text"
    try:
        narrator_review_path.resolve().relative_to(metadata_root.resolve())
    except ValueError:
        return ["narrator review must resolve under metadata/"], None
    try:
        review = load_json(narrator_review_path)
    except RuntimeError as error:
        return [str(error)], None
    if not isinstance(review, dict):
        return ["narrator review must be an object"], None

    locutor_path = resolve_under(text_root, review.get("output_file"), (Path("locutor"),))
    if locutor_path is None:
        errors.append("narrator review output_file must resolve under locutor/")
    elif locutor_path != input_file.resolve():
        errors.append("narrator review output_file does not match the selected narrator input")
    elif not locutor_path.is_file():
        errors.append("narrator review output_file is missing")

    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        errors.append(f"narrator review schema_version must be {REVIEW_SCHEMA_VERSION!r}")
    if review.get("profile") != QUALITY_PROFILE:
        errors.append(f"narrator review profile must be {QUALITY_PROFILE!r}")
    if review.get("status") != "approved":
        errors.append("narrator review status must be approved")
    if not require_text(review.get("reviewed_by")):
        errors.append("narrator review reviewed_by must be non-empty")
    if review.get("output_sha256") != sha256_file(input_file):
        errors.append("narrator review output_sha256 does not match the narrator input")

    narrator_changes = narrator_changes_path or metadata_root / "narrator-changes.json"
    if not narrator_changes.is_file():
        errors.append("narrator review requires narrator-changes metadata")
    elif review.get("narrator_changes_sha256") != sha256_file(narrator_changes):
        errors.append("narrator review narrator_changes_sha256 does not match current metadata")

    output_scope_errors, output_pages = narrator_output_pages(
        book_root,
        input_file,
        narrator_changes,
    )
    errors += output_scope_errors
    scope_errors, review_pages = _validate_review_scope(
        review.get("review_scope"), output_pages
    )
    errors += scope_errors
    review_scope = review.get("review_scope")
    review_categories = (
        set(review_scope.get("categories", []))
        if isinstance(review_scope, dict)
        else set()
    )
    locutor_text = normalized_text(input_file.read_text(encoding="utf-8"))
    pronunciation_errors, pronunciation_entries = _validate_pronunciation(
        review.get("pronunciation_review"), locutor_text, review_pages
    )
    errors += pronunciation_errors

    findings = review.get("findings")
    if not isinstance(findings, list):
        return errors + ["narrator review findings must be an array"], None
    by_id: dict[str, dict] = {}
    for index, entry in enumerate(findings):
        label = f"narrator review findings[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        finding_id = entry.get("id")
        if not require_text(finding_id):
            errors.append(f"{label}.id must be non-empty")
            continue
        if finding_id in by_id:
            errors.append(f"{label}.id is duplicated")
            continue
        by_id[finding_id] = entry
        if entry.get("kind") not in FINDING_KINDS:
            errors.append(f"{label}.kind is invalid")
        if entry.get("severity") not in {"blocking", "review"}:
            errors.append(f"{label}.severity is invalid")
        if not isinstance(entry.get("line_number"), int) or entry["line_number"] <= 0:
            errors.append(f"{label}.line_number must be positive")
        if not isinstance(entry.get("column"), int) or entry["column"] <= 0:
            errors.append(f"{label}.column must be positive")
        if not require_text(entry.get("locutor_span")):
            errors.append(f"{label}.locutor_span must be non-empty")
        if not require_text(entry.get("context")):
            errors.append(f"{label}.context must be non-empty")
        if entry.get("category") not in LOCUTION_CATEGORIES:
            errors.append(f"{label}.category is invalid")
        elif entry["category"] not in review_categories:
            errors.append(f"{label}.category must be inside review_scope.categories")
        if not _positive_pages(entry.get("logical_pages")):
            errors.append(f"{label}.logical_pages must contain positive integers")
        elif not set(entry["logical_pages"]).issubset(review_pages):
            errors.append(f"{label}.logical_pages must be inside review_scope")
        if entry.get("status") not in FINDING_STATUSES:
            errors.append(f"{label}.status must be resolved or preserved")
        if not require_text(entry.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    active_findings = audit_narrator_quality(
        book_root,
        input_file,
        narrator_changes,
        narration_plan_continuation_lines(book_root, input_file),
    )
    for finding in active_findings:
        entry = by_id.get(finding.id)
        if entry is None:
            errors.append(f"narrator review is missing active finding {finding.id}")
            continue
        if entry.get("kind") != finding.kind:
            errors.append(f"narrator review finding {finding.id} kind does not match current output")
        if entry.get("locutor_span") != finding.locutor_span:
            errors.append(f"narrator review finding {finding.id} span does not match current output")
        if entry.get("line_number") != finding.line_number:
            errors.append(
                f"narrator review finding {finding.id} line number does not match current output"
            )
        if entry.get("column") != finding.column:
            errors.append(
                f"narrator review finding {finding.id} column does not match current output"
            )
        if entry.get("context") != finding.context:
            errors.append(
                f"narrator review finding {finding.id} context does not match current output"
            )
        if finding.severity == "blocking":
            errors.append(f"narrator output still contains blocking finding {finding.id}")
        elif entry.get("status") != "preserved":
            errors.append(
                f"active narrator review finding {finding.id} must be explicitly preserved"
            )
        if (
            finding.kind == "uppercase_token"
            and not (
                entry.get("category") == "heading"
                and _is_all_caps_multiword_line(finding.context)
            )
            and not _pronunciation_entry_exists(
                pronunciation_entries, finding.locutor_span, "acronym"
            )
        ):
            errors.append(
                f"narrator review needs an acronym pronunciation decision for {finding.id}"
            )
        if finding.kind == "abbreviation" and not _pronunciation_entry_exists(
            pronunciation_entries, finding.locutor_span, "abbreviation"
        ):
            errors.append(
                f"narrator review needs an abbreviation pronunciation decision for {finding.id}"
            )
        if finding.kind == "pronunciation_sensitive_term" and not _pronunciation_span_entry_exists(
            pronunciation_entries, finding.locutor_span
        ):
            errors.append(
                f"narrator review needs a pronunciation decision for {finding.id}"
            )

    if errors:
        return errors, None
    return (
        [],
        {
            "schema_version": review["schema_version"],
            "profile": review["profile"],
            "narrator_review_sha256": sha256_file(narrator_review_path),
            "output_file": review["output_file"],
            "output_sha256": review["output_sha256"],
            "reviewed_by": review["reviewed_by"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an approved faithful-natural narrator review."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--narrator-review", type=Path)
    parser.add_argument("--narrator-changes", type=Path)
    parser.add_argument("--input-file", required=True, type=Path)
    args = parser.parse_args()

    book_root = args.book_root.expanduser().resolve()
    narrator_review = (
        args.narrator_review.expanduser().resolve()
        if args.narrator_review
        else book_root / "metadata" / "narrator-review.json"
    )
    input_file = args.input_file.expanduser().resolve()
    narrator_changes = (
        args.narrator_changes.expanduser().resolve()
        if args.narrator_changes
        else None
    )
    if not input_file.is_file():
        print(f"INVALID narrator review: narrator input is missing: {input_file}", file=sys.stderr)
        raise SystemExit(1)

    errors, provenance = validate_review(
        book_root,
        narrator_review,
        input_file,
        narrator_changes,
    )
    if errors:
        print("INVALID narrator review:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID narrator review")
    print(json.dumps(provenance, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
