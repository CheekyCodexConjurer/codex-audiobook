from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile

from narrator_quality import (
    QUALITY_PROFILE,
    audit_narrator_quality,
    audit_text,
    draft_review,
)
from validate_narrator_quality import validate_review


ROOT = Path(__file__).resolve().parent
MANDATORY_KINDS = {
    "introduced_punctuation",
    "corrupted_phrase_split",
    "mechanical_lowercase_start",
    "pronunciation_sensitive_term",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_book(root: Path, source_text: str, locutor_text: str, changes: list[dict]) -> tuple[Path, Path, Path]:
    book_root = root / "book"
    source_path = book_root / "text" / "source" / "chapters" / "chapter-01.txt"
    locutor_path = book_root / "text" / "locutor" / "chapters" / "chapter-01.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    locutor_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8")
    locutor_path.write_text(locutor_text, encoding="utf-8")
    page_path = book_root / "text" / "locutor" / "pages" / "page-1.txt"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(locutor_text, encoding="utf-8")

    ledger = {
        "chapter_outputs": [
            {
                "id": "chapter-01",
                "source_file": "source/chapters/chapter-01.txt",
                "source_sha256": sha256_file(source_path),
                "source_pages": [{"logical_page": 1}],
            }
        ]
    }
    write_json(book_root / "metadata" / "text-ledger.json", ledger)
    narrator_changes = {
        "schema_version": "2.0",
        "base_edition": "source",
        "outputs": [
            {
                "id": "chapter-01-locutor",
                "kind": "chapter",
                "locutor_file": "locutor/chapters/chapter-01.txt",
                "locutor_sha256": sha256_file(locutor_path),
                "reviewed_by": "codex",
                "base_outputs": [
                    {
                        "id": "chapter-01",
                        "base_file": "source/chapters/chapter-01.txt",
                        "base_sha256": sha256_file(source_path),
                    }
                ],
            }
        ],
        "changes": changes,
    }
    changes_path = book_root / "metadata" / "narrator-changes.json"
    write_json(changes_path, narrator_changes)
    return book_root, locutor_path, changes_path


def approve_review(review: dict) -> dict:
    approved = copy.deepcopy(review)
    approved["status"] = "approved"
    approved["reviewed_by"] = "codex"
    for finding in approved["findings"]:
        finding["category"] = finding.get("suggested_category") or "prose"
        finding["status"] = "preserved"
        finding["reason"] = "Reviewed against source-bound narrator quality evidence."
        finding["reviewed_by"] = "codex"
    approved["pronunciation_review"]["status"] = "approved"
    approved["pronunciation_review"]["reviewed_by"] = "codex"
    return approved


def test_evidence_backed_blocking_findings() -> None:
    with tempfile.TemporaryDirectory(prefix="narrator-quality-") as tmp:
        source_text = (
            "E pasmem que ele chegou. Mas aí ficou. "
            "Esta frase continua em outra linha."
        )
        locutor_text = (
            "E. Pasmem que ele chegou.\n"
            "Mas. Aí ficou.\n"
            "Esta frase continua\n"
            "em outra linha."
        )
        changes = [
            {
                "output_id": "chapter-01-locutor",
                "base_output_id": "chapter-01",
                "kind": "punctuation",
                "base_span": "E pasmem que ele chegou.",
                "locutor_span": "E. Pasmem que ele chegou.",
                "logical_pages": [1],
                "reason": "Test punctuation edit.",
                "reviewed_by": "codex",
            },
            {
                "output_id": "chapter-01-locutor",
                "base_output_id": "chapter-01",
                "kind": "punctuation",
                "base_span": "Mas aí ficou.",
                "locutor_span": "Mas. Aí ficou.",
                "logical_pages": [1],
                "reason": "Test punctuation edit.",
                "reviewed_by": "codex",
            },
        ]
        book_root, locutor_path, changes_path = make_book(Path(tmp), source_text, locutor_text, changes)
        findings = audit_narrator_quality(book_root, locutor_path, changes_path)
        kinds = [finding.kind for finding in findings]
        assert kinds.count("introduced_punctuation") == 2, kinds
        assert kinds.count("corrupted_phrase_split") == 2, kinds
        assert "mechanical_lowercase_start" in kinds, kinds

        draft = draft_review(book_root, locutor_path, findings, changes_path)
        review = approve_review(draft)
        review_path = book_root / "metadata" / "narrator-review.json"
        write_json(review_path, review)
        errors, _ = validate_review(book_root, review_path, locutor_path, changes_path)
        assert not any("blocking finding introduced_punctuation" in error for error in errors), errors
        assert any("blocking finding corrupted_phrase_split" in error for error in errors), errors
        assert any("blocking finding mechanical_lowercase_start" in error for error in errors), errors


def test_pronunciation_sensitive_change_requires_decision() -> None:
    with tempfile.TemporaryDirectory(prefix="narrator-quality-") as tmp:
        source_text = "Oxóssi falou."
        locutor_text = "Oshossi falou."
        changes = [
            {
                "output_id": "chapter-01-locutor",
                "base_output_id": "chapter-01",
                "kind": "pronunciation",
                "base_span": "Oxóssi",
                "locutor_span": "Oshossi",
                "logical_pages": [1],
                "reason": "Approved pronunciation form for a religious term.",
                "reviewed_by": "codex",
            }
        ]
        book_root, locutor_path, changes_path = make_book(Path(tmp), source_text, locutor_text, changes)
        findings = audit_narrator_quality(book_root, locutor_path, changes_path)
        assert [finding.kind for finding in findings] == ["pronunciation_sensitive_term"]
        review = approve_review(draft_review(book_root, locutor_path, findings, changes_path))
        review_path = book_root / "metadata" / "narrator-review.json"
        write_json(review_path, review)
        missing_errors, _ = validate_review(book_root, review_path, locutor_path, changes_path)
        assert any("needs a pronunciation decision" in error for error in missing_errors), missing_errors

        review["pronunciation_review"]["entries"] = [
            {
                "term": "Oxóssi",
                "kind": "religious_term",
                "decision": "spoken_form",
                "spoken_form": "Oshossi",
                "locutor_span": "Oshossi",
                "logical_pages": [1],
                "reason": "The source-bound pronunciation change was reviewed.",
                "reviewed_by": "codex",
            }
        ]
        write_json(review_path, review)
        errors, provenance = validate_review(book_root, review_path, locutor_path, changes_path)
        assert not errors, errors
        assert provenance is not None and provenance["profile"] == QUALITY_PROFILE


def test_style_only_text_does_not_create_mandatory_findings() -> None:
    findings = audit_text("E. Pasmem\nMas. Aí\nem outra linha.")
    assert not MANDATORY_KINDS.intersection(finding.kind for finding in findings)


def test_multiline_aggregate_change_maps_findings_to_physical_lines() -> None:
    with tempfile.TemporaryDirectory(prefix="narrator-quality-") as tmp:
        source_text = (
            "Primeiro paragrafo intacto. "
            "E, pasmem, depois chegou. "
            "Mas, ai de nos! A luta reaparece."
        )
        locutor_text = (
            "Primeiro paragrafo intacto.\n"
            "E. Pasmem! Depois chegou.\n"
            "Mas. Ai de nos! A luta reaparece."
        )
        changes = [
            {
                "output_id": "chapter-01-locutor",
                "base_output_id": "chapter-01",
                "kind": "punctuation",
                "base_span": source_text,
                "locutor_span": locutor_text,
                "logical_pages": [1],
                "reason": "Aggregate multiline narrator edit.",
                "reviewed_by": "codex",
            }
        ]
        book_root, locutor_path, changes_path = make_book(
            Path(tmp),
            source_text,
            locutor_text,
            changes,
        )
        findings = audit_narrator_quality(book_root, locutor_path, changes_path)
        corrupted = [
            finding for finding in findings if finding.kind == "corrupted_phrase_split"
        ]
        assert [(finding.line_number, finding.column) for finding in corrupted] == [
            (2, 1),
            (3, 1),
        ], corrupted
        assert any(
            finding.kind == "introduced_punctuation" and finding.line_number in {2, 3}
            for finding in findings
        ), findings


def main() -> None:
    test_evidence_backed_blocking_findings()
    test_pronunciation_sensitive_change_requires_decision()
    test_style_only_text_does_not_create_mandatory_findings()
    test_multiline_aggregate_change_maps_findings_to_physical_lines()
    print("Narrator quality focused tests passed.")


if __name__ == "__main__":
    main()
