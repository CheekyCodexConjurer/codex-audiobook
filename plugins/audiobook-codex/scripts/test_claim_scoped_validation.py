from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from swarm_claims import claim_digest
from verify_fluid_edition_ledger import verify_claim as verify_fluid_claim
from verify_text_ledger import sha256_file, verify, verify_claim as verify_text_claim
from verify_translation_ledger import verify_claim as verify_translation_claim


PRODUCER = "audiobook-worker"
VERIFIER = "audiobook-verifier"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_for(stage: str, claim_id: str, paths: list[str], shard_path: str) -> dict:
    return {
        "claim_id": claim_id,
        "stage": stage,
        "status": "ready_for_verification",
        "claim_order": 1,
        "priority": 1,
        "depends_on": [],
        "producer": PRODUCER,
        "verifier": VERIFIER,
        "read_set": [],
        "write_set": [*paths, shard_path],
        "canonical_targets": paths,
        "no_touch": [],
        "scope": {
            "unit_kind": "chapter",
            "unit_ids": ["chapter-01"],
            "context_unit_ids": [],
        },
        "context": {},
        "validation": {"requires_verification": True, "commands": []},
        "lease": {"holder": "", "issued_at": "", "expires_at": ""},
    }


class ClaimScopedValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.book_root = Path(self.temp.name)
        self.text_root = self.book_root / "text"
        self.work_root = self.book_root / "metadata" / "work"

        self.book_map = {
            "schema_version": "1.0",
            "analysis": {"source_language": "English"},
            "pages": [
                {"logical_page": 1, "kind": "body", "blank": False, "chapter_id": "chapter-01"},
                {"logical_page": 2, "kind": "body", "blank": False, "chapter_id": "chapter-02"},
            ],
            "chapters": [
                {"id": "chapter-01", "number": 1, "title": "One"},
                {"id": "chapter-02", "number": 2, "title": "Two"},
            ],
        }
        self.book_map_path = self.book_root / "metadata" / "book-map.json"
        self.book_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.book_map_path.write_text(
            json.dumps(self.book_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.book_map_sha256 = sha256_file(self.book_map_path)

        self.source_page = "Original one.\n"
        self.source_chapter = self.source_page
        self.translation_page = "Original um.\n"
        self.translation_chapter = self.translation_page
        self.fluid_chapter = "Original fluido.\n"
        write_text(self.text_root / "source/pages/page-0001.txt", self.source_page)
        write_text(self.text_root / "source/chapters/chapter-01-one.txt", self.source_chapter)
        write_text(self.text_root / "translation/pt-BR/pages/page-0001.txt", self.translation_page)
        write_text(self.text_root / "translation/pt-BR/chapters/chapter-01-one.txt", self.translation_chapter)
        write_text(self.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt", self.fluid_chapter)

        self.source_page_hash = sha256_text(self.source_page)
        self.source_chapter_hash = sha256_text(self.source_chapter)
        self.translation_page_hash = sha256_text(self.translation_page)
        self.translation_chapter_hash = sha256_text(self.translation_chapter)
        self.fluid_chapter_hash = sha256_text(self.fluid_chapter)

        self.source_ledger = {
            "schema_version": "1.0",
            "book_map_sha256": self.book_map_sha256,
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": self.source_page_hash,
                    "verified_by": VERIFIER,
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "source_file": "source/chapters/chapter-01-one.txt",
                    "source_sha256": self.source_chapter_hash,
                    "source_pages": [{"logical_page": 1, "source_sha256": self.source_page_hash}],
                    "verified_by": VERIFIER,
                }
            ],
        }
        self.source_ledger_path = self.book_root / "metadata" / "text-ledger.json"
        self.source_ledger_path.write_text(
            json.dumps(self.source_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.source_ledger_sha256 = sha256_file(self.source_ledger_path)

        self.translation_ledger = {
            "schema_version": "1.1",
            "book_map_sha256": self.book_map_sha256,
            "text_ledger_sha256": self.source_ledger_sha256,
            "source_language": "English",
            "target_language": "pt-BR",
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": self.source_page_hash,
                    "translation_file": "translation/pt-BR/pages/page-0001.txt",
                    "translation_sha256": self.translation_page_hash,
                    "translated_by": PRODUCER,
                    "reviewed_by": VERIFIER,
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "source_file": "source/chapters/chapter-01-one.txt",
                    "source_sha256": self.source_chapter_hash,
                    "translation_file": "translation/pt-BR/chapters/chapter-01-one.txt",
                    "translation_sha256": self.translation_chapter_hash,
                    "source_pages": [{"logical_page": 1, "source_sha256": self.source_page_hash}],
                    "translated_by": PRODUCER,
                    "reviewed_by": VERIFIER,
                }
            ],
        }
        self.translation_ledger_path = self.book_root / "metadata" / "translation-ledger.json"
        self.translation_ledger_path.write_text(
            json.dumps(self.translation_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.translation_ledger_sha256 = sha256_file(self.translation_ledger_path)

        self.fluid_style = {
            "schema_version": "1.2",
            "profile": "fluid-faithful-ptbr-v1",
            "language": "pt-BR",
            "base_edition": "translated-pt-br",
            "voice": {
                "register": "natural",
                "tone": "clear",
                "cadence": "smooth",
                "terminology": "stable",
                "title_policy": "preserve titles",
            },
            "rules": {
                "preserve_meaning": True,
                "no_added_content": True,
                "no_omitted_content": True,
                "modernize_grammar_and_lexicon": True,
                "modernize_all_archaic_language": True,
                "modernize_historical_quotations": True,
                "modernize_orthography_and_diacritics": True,
                "omit_parenthetical_citation_references": True,
                "omit_immediate_duplicate_translations": True,
                "omit_translation_labels": True,
                "reduce_redundancy": True,
                "clarify_referents": True,
                "preserve_examples_and_arguments": True,
                "preserve_authorial_stance": True,
            },
            "glossary": [],
            "reviewed_by": VERIFIER,
        }
        self.fluid_style_path = self.book_root / "metadata" / "fluid-style.json"
        self.fluid_style_path.write_text(
            json.dumps(self.fluid_style, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.fluid_style_sha256 = sha256_file(self.fluid_style_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def read_set_for(self, *paths: str) -> list[dict]:
        return [
            {"path": path, "sha256": sha256_file(self.book_root / path)}
            for path in paths
        ]

    def write_current_book_map(self) -> None:
        self.book_map_path.write_text(
            json.dumps(self.book_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.book_map_sha256 = sha256_file(self.book_map_path)

    def required_reads_for_stage(self, stage: str) -> list[dict]:
        if stage == "TRANSCRIBE":
            return self.read_set_for("metadata/book-map.json")
        if stage == "TRANSLATE":
            return self.read_set_for(
                "metadata/book-map.json",
                "metadata/text-ledger.json",
                "metadata/translation-ledger.json",
            )
        if stage == "FLUID":
            return self.read_set_for(
                "metadata/book-map.json",
                "metadata/text-ledger.json",
                "metadata/translation-ledger.json",
                "metadata/fluid-style.json",
            )
        raise AssertionError(f"unknown stage {stage}")

    def text_shard_and_map(self) -> tuple[dict, dict, Path]:
        claim = claim_for(
            "TRANSCRIBE",
            "text:chapter-01",
            [
                "text/source/pages/page-0001.txt",
                "text/source/chapters/chapter-01-one.txt",
            ],
            "metadata/work/text-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("TRANSCRIBE")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "text": {
                "pages": copy.deepcopy(self.source_ledger["pages"]),
                "chapter_outputs": copy.deepcopy(self.source_ledger["chapter_outputs"]),
            },
        }
        return shard, {"schema_version": "1.0", "claims": [claim]}, self.work_root / "text-shard.json"

    def test_text_claim_partial_valid_without_rest_of_book(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        self.assertEqual(
            [],
            verify_text_claim(
                self.book_map,
                self.book_map_sha256,
                shard,
                claim_map,
                "text:chapter-01",
                self.text_root,
                False,
                shard_path,
            ),
        )
        claim_map_path = self.work_root / "claim-map.json"
        claim_map_path.parent.mkdir(parents=True, exist_ok=True)
        claim_map_path.write_text(
            json.dumps(claim_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shard_path.write_text(
            json.dumps(shard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "verify_text_ledger.py"),
                "--mode",
                "claim",
                "--book-map",
                str(self.book_map_path),
                "--text-root",
                str(self.text_root),
                "--claim-map",
                str(claim_map_path),
                "--claim-id",
                "text:chapter-01",
                "--shard",
                str(shard_path),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_record_outside_claim_scope_fails(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        write_text(self.text_root / "source/pages/page-0002.txt", "Original two.\n")
        shard["text"]["pages"][0] = {
            "logical_page": 2,
            "status": "verified",
            "source_file": "source/pages/page-0002.txt",
            "source_sha256": sha256_text("Original two.\n"),
            "verified_by": VERIFIER,
        }
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("outside claim scope.unit_ids" in error for error in errors), errors)

    def test_text_claim_rejects_empty_targets(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        claim_map["claims"][0]["write_set"] = []
        claim_map["claims"][0]["canonical_targets"] = []
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("write_set must be non-empty" in error for error in errors), errors)
        self.assertTrue(any("canonical_targets must be non-empty" in error for error in errors), errors)
        self.assertTrue(any("must be non-empty for output validation" in error for error in errors), errors)

    def test_text_claim_rejects_out_of_target_output_and_shard_path(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        claim_map["claims"][0]["write_set"] = [
            "text/source/pages/page-0001.txt",
            "metadata/work/other-shard.json",
        ]
        claim_map["claims"][0]["canonical_targets"] = ["text/source/pages/page-0001.txt"]
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("source_file is outside claim write_set/canonical_targets" in error for error in errors), errors)
        self.assertTrue(any("shard path is outside claim write_set/canonical_targets" in error for error in errors), errors)

    def test_text_claim_rejects_ancestor_targets_for_outputs_and_shards(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        claim_map["claims"][0]["write_set"] = [
            "text/source",
            "metadata/work",
        ]
        claim_map["claims"][0]["canonical_targets"] = ["text/source"]
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("source_file is outside claim write_set/canonical_targets" in error for error in errors), errors)
        self.assertTrue(any("shard path is outside claim write_set/canonical_targets" in error for error in errors), errors)

    def test_text_claim_allows_explicit_shard_directory_target(self) -> None:
        shard, claim_map, _shard_path = self.text_shard_and_map()
        shard_path = self.work_root / "text-ledger.d" / "chapter-01.json"
        shard_path.parent.mkdir(parents=True, exist_ok=True)
        claim_map["claims"][0]["write_set"] = [
            "text/source/pages/page-0001.txt",
            "text/source/chapters/chapter-01-one.txt",
            "metadata/work/text-ledger.d",
        ]
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertEqual([], errors)

    def test_chapter_output_rejects_foreign_context_page(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        claim_map["claims"][0]["scope"]["context_unit_ids"] = ["chapter-02"]
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        write_text(self.text_root / "source/pages/page-0002.txt", "Original two.\n")
        page_two = {
            "logical_page": 2,
            "status": "verified",
            "source_file": "source/pages/page-0002.txt",
            "source_sha256": sha256_text("Original two.\n"),
            "verified_by": VERIFIER,
        }
        shard["text"]["pages"].append(page_two)
        shard["text"]["chapter_outputs"][0]["source_pages"].append(
            {"logical_page": 2, "source_sha256": page_two["source_sha256"]}
        )
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("source_pages[1] is outside claim scope.unit_ids" in error for error in errors), errors)

    def test_chapter_output_rejects_missing_page_record(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        shard["text"]["pages"] = []
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("must reference a page validated by this shard" in error for error in errors), errors)

    def test_text_claim_rejects_omitted_owned_page_record(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        self.book_map["pages"].append(
            {"logical_page": 3, "kind": "body", "blank": False, "chapter_id": "chapter-01"}
        )
        self.write_current_book_map()
        claim_map["claims"][0]["read_set"] = self.required_reads_for_stage("TRANSCRIBE")
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("shard.text.pages is missing owned logical pages: [3]" in error for error in errors), errors)
        self.assertTrue(
            any("shard.text.chapter_outputs.source_pages is missing owned logical pages: [3]" in error for error in errors),
            errors,
        )

    def test_text_claim_valid_multi_page_owned_chapter(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        self.book_map["pages"].append(
            {"logical_page": 3, "kind": "body", "blank": False, "chapter_id": "chapter-01"}
        )
        self.write_current_book_map()
        claim_map["claims"][0]["read_set"] = self.required_reads_for_stage("TRANSCRIBE")
        claim_map["claims"][0]["write_set"].append("text/source/pages/page-0003.txt")
        claim_map["claims"][0]["canonical_targets"].append("text/source/pages/page-0003.txt")
        shard["claim_sha256"] = claim_digest(claim_map["claims"][0])
        page_three_text = "Original three.\n"
        write_text(self.text_root / "source/pages/page-0003.txt", page_three_text)
        page_three = {
            "logical_page": 3,
            "status": "verified",
            "source_file": "source/pages/page-0003.txt",
            "source_sha256": sha256_text(page_three_text),
            "verified_by": VERIFIER,
        }
        shard["text"]["pages"].append(page_three)
        combined = self.source_page + page_three_text
        write_text(self.text_root / "source/chapters/chapter-01-one.txt", combined)
        shard["text"]["chapter_outputs"][0]["source_sha256"] = sha256_text(combined)
        shard["text"]["chapter_outputs"][0]["source_pages"].append(
            {"logical_page": 3, "source_sha256": page_three["source_sha256"]}
        )
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertEqual([], errors)

    def test_stale_claim_hash_binding_fails(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        claim_map["claims"][0]["producer"] = "new-worker"
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("claim_sha256 diverges" in error for error in errors), errors)
        self.assertTrue(any("producer does not match" in error for error in errors), errors)

    def test_text_claim_rejects_book_map_mutation_after_claim_creation(self) -> None:
        shard, claim_map, shard_path = self.text_shard_and_map()
        self.book_map_path.write_text(
            json.dumps({**self.book_map, "mutated": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        errors = verify_text_claim(
            self.book_map,
            self.book_map_sha256,
            shard,
            claim_map,
            "text:chapter-01",
            self.text_root,
            False,
            shard_path,
        )
        self.assertTrue(any("read_set sha256 does not match current file: metadata/book-map.json" in error for error in errors), errors)

    def test_approval_mode_remains_global(self) -> None:
        errors = verify(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.text_root,
            False,
            False,
        )
        self.assertTrue(any("logical page 2 is missing" in error for error in errors), errors)

    def test_translation_claim_partial_valid_against_frozen_source(self) -> None:
        claim = claim_for(
            "TRANSLATE",
            "translation:chapter-01",
            [
                "text/translation/pt-BR/pages/page-0001.txt",
                "text/translation/pt-BR/chapters/chapter-01-one.txt",
            ],
            "metadata/work/translation-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("TRANSLATE")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "translation",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "translation": {
                "pages": copy.deepcopy(self.translation_ledger["pages"]),
                "chapter_outputs": copy.deepcopy(self.translation_ledger["chapter_outputs"]),
                "glossary_proposals": [],
                "ambiguities": [],
            },
        }
        errors = verify_translation_claim(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            shard,
            {"schema_version": "1.0", "claims": [claim]},
            claim["claim_id"],
            self.text_root,
            self.work_root / "translation-shard.json",
        )
        self.assertEqual([], errors)

    def test_translation_claim_rejects_omitted_owned_page(self) -> None:
        claim = claim_for(
            "TRANSLATE",
            "translation:chapter-01",
            [
                "text/translation/pt-BR/pages/page-0001.txt",
                "text/translation/pt-BR/chapters/chapter-01-one.txt",
            ],
            "metadata/work/translation-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("TRANSLATE")
        self.book_map["pages"].append(
            {"logical_page": 3, "kind": "body", "blank": False, "chapter_id": "chapter-01"}
        )
        self.write_current_book_map()
        claim["read_set"] = self.required_reads_for_stage("TRANSLATE")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "translation",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "translation": {
                "pages": copy.deepcopy(self.translation_ledger["pages"]),
                "chapter_outputs": copy.deepcopy(self.translation_ledger["chapter_outputs"]),
                "glossary_proposals": [],
                "ambiguities": [],
            },
        }
        errors = verify_translation_claim(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            shard,
            {"schema_version": "1.0", "claims": [claim]},
            claim["claim_id"],
            self.text_root,
            self.work_root / "translation-shard.json",
        )
        self.assertTrue(any("shard.translation.pages is missing owned logical pages: [3]" in error for error in errors), errors)
        self.assertTrue(
            any("shard.translation.chapter_outputs.source_pages is missing owned logical pages: [3]" in error for error in errors),
            errors,
        )

    def test_translation_claim_rejects_dependency_mutation_after_claim_creation(self) -> None:
        claim = claim_for(
            "TRANSLATE",
            "translation:chapter-01",
            [
                "text/translation/pt-BR/pages/page-0001.txt",
                "text/translation/pt-BR/chapters/chapter-01-one.txt",
            ],
            "metadata/work/translation-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("TRANSLATE")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "translation",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "translation": {
                "pages": copy.deepcopy(self.translation_ledger["pages"]),
                "chapter_outputs": copy.deepcopy(self.translation_ledger["chapter_outputs"]),
                "glossary_proposals": [],
                "ambiguities": [],
            },
        }
        self.source_ledger_path.write_text(
            json.dumps({**self.source_ledger, "mutated": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        errors = verify_translation_claim(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            shard,
            {"schema_version": "1.0", "claims": [claim]},
            claim["claim_id"],
            self.text_root,
            self.work_root / "translation-shard.json",
        )
        self.assertTrue(any("read_set sha256 does not match current file: metadata/text-ledger.json" in error for error in errors), errors)

    def test_fluid_claim_partial_valid_against_frozen_base_and_style(self) -> None:
        claim = claim_for(
            "FLUID",
            "fluid:chapter-01",
            ["text/fluid/pt-BR/chapters/chapter-01-one.txt"],
            "metadata/work/fluid-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("FLUID")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "fluid",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "fluid": {
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "base_file": "translation/pt-BR/chapters/chapter-01-one.txt",
                        "base_sha256": self.translation_chapter_hash,
                        "fluid_file": "fluid/pt-BR/chapters/chapter-01-one.txt",
                        "fluid_sha256": self.fluid_chapter_hash,
                        "source_pages": [{"logical_page": 1, "source_sha256": self.source_page_hash}],
                        "base_block_count": 1,
                        "fluid_block_count": 1,
                        "reviewed_by": VERIFIER,
                    }
                ],
                "blocks": [
                    {
                        "id": "chapter-01-b0001",
                        "output_id": "chapter-01",
                        "position": 1,
                        "base_sha256": sha256_text(self.translation_chapter.strip()),
                        "status": "included",
                        "fluid_position": 1,
                        "fluid_sha256": sha256_text(self.fluid_chapter.strip()),
                        "change_kinds": ["fluency"],
                        "reviewed_by": VERIFIER,
                    }
                ],
            },
        }
        errors = verify_fluid_claim(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            self.translation_ledger,
            self.translation_ledger_sha256,
            self.fluid_style,
            self.fluid_style_sha256,
            shard,
            {"schema_version": "1.0", "claims": [claim]},
            claim["claim_id"],
            self.text_root,
            self.work_root / "fluid-shard.json",
        )
        self.assertEqual([], errors)

    def test_fluid_claim_rejects_dependency_mutation_after_claim_creation(self) -> None:
        claim = claim_for(
            "FLUID",
            "fluid:chapter-01",
            ["text/fluid/pt-BR/chapters/chapter-01-one.txt"],
            "metadata/work/fluid-shard.json",
        )
        claim["read_set"] = self.required_reads_for_stage("FLUID")
        shard = {
            "schema_version": "1.0",
            "shard_kind": "fluid",
            "claim_id": claim["claim_id"],
            "claim_sha256": claim_digest(claim),
            "producer": PRODUCER,
            "verifier": VERIFIER,
            "order": 1,
            "fluid": {
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "base_file": "translation/pt-BR/chapters/chapter-01-one.txt",
                        "base_sha256": self.translation_chapter_hash,
                        "fluid_file": "fluid/pt-BR/chapters/chapter-01-one.txt",
                        "fluid_sha256": self.fluid_chapter_hash,
                        "source_pages": [{"logical_page": 1, "source_sha256": self.source_page_hash}],
                        "base_block_count": 1,
                        "fluid_block_count": 1,
                        "reviewed_by": VERIFIER,
                    }
                ],
                "blocks": [
                    {
                        "id": "chapter-01-b0001",
                        "output_id": "chapter-01",
                        "position": 1,
                        "base_sha256": sha256_text(self.translation_chapter.strip()),
                        "status": "included",
                        "fluid_position": 1,
                        "fluid_sha256": sha256_text(self.fluid_chapter.strip()),
                        "change_kinds": ["fluency"],
                        "reviewed_by": VERIFIER,
                    }
                ],
            },
        }
        self.fluid_style_path.write_text(
            json.dumps({**self.fluid_style, "mutated": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        errors = verify_fluid_claim(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            self.translation_ledger,
            self.translation_ledger_sha256,
            self.fluid_style,
            self.fluid_style_sha256,
            shard,
            {"schema_version": "1.0", "claims": [claim]},
            claim["claim_id"],
            self.text_root,
            self.work_root / "fluid-shard.json",
        )
        self.assertTrue(any("read_set sha256 does not match current file: metadata/fluid-style.json" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
