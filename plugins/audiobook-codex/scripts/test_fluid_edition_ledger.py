from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from verify_fluid_edition_ledger import (
    FLUID_PROFILE,
    FLUID_ROOT,
    SCHEMA_VERSION,
    STYLE_SCHEMA_VERSION,
    TARGET_LANGUAGE,
    fluid_chapter_output_records,
    fluid_document_titles,
    sha256_file,
    sha256_text,
    verify,
)


EDITOR = "audiobook-editor"
REVIEWER = "audiobook-verifier"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_blocks(text: str) -> list[str]:
    return [block.strip() for block in text.strip().split("\n\n")]


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.text_root = root / "text"

        self.source_page_text = "Original one.\n\nOriginal two.\n"
        self.source_chapter_text = self.source_page_text
        self.translation_page_text = "Base um.\n\nBase dois.\n"
        self.translation_chapter_text = self.translation_page_text
        self.fluid_chapter_text = "Fluido um.\n\nFluido dois.\n"

        write_text(self.text_root / "source/pages/page-0001.txt", self.source_page_text)
        write_text(self.text_root / "source/chapters/chapter-01-one.txt", self.source_chapter_text)
        write_text(self.text_root / "translation/pt-BR/pages/page-0001.txt", self.translation_page_text)
        write_text(self.text_root / "translation/pt-BR/chapters/chapter-01-one.txt", self.translation_chapter_text)
        write_text(self.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt", self.fluid_chapter_text)
        write_text(self.text_root / "fluid/pt-BR/book.txt", self.fluid_chapter_text.rstrip() + "\n")

        self.book_map = {
            "schema_version": "1.0",
            "analysis": {"source_language": "English"},
            "pages": [
                {
                    "logical_page": 1,
                    "kind": "body",
                    "blank": False,
                    "chapter_id": "chapter-01",
                }
            ],
            "chapters": [
                {
                    "id": "chapter-01",
                    "number": 1,
                    "title": "One",
                    "start_logical_page": 1,
                    "end_logical_page": 1,
                }
            ],
        }
        self.book_map_path = root / "book-map.json"
        write_json(self.book_map_path, self.book_map)
        self.book_map_sha256 = sha256_file(self.book_map_path)

        source_page_hash = sha256_file(self.text_root / "source/pages/page-0001.txt")
        source_chapter_hash = sha256_file(self.text_root / "source/chapters/chapter-01-one.txt")
        self.source_ledger = {
            "schema_version": "1.0",
            "book_map_sha256": self.book_map_sha256,
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": source_page_hash,
                    "verified_by": REVIEWER,
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "source_file": "source/chapters/chapter-01-one.txt",
                    "source_sha256": source_chapter_hash,
                    "source_pages": [{"logical_page": 1, "source_sha256": source_page_hash}],
                    "verified_by": REVIEWER,
                }
            ],
        }
        self.source_ledger_path = root / "text-ledger.json"
        write_json(self.source_ledger_path, self.source_ledger)
        self.source_ledger_sha256 = sha256_file(self.source_ledger_path)

        translation_page_hash = sha256_file(self.text_root / "translation/pt-BR/pages/page-0001.txt")
        translation_chapter_hash = sha256_file(self.text_root / "translation/pt-BR/chapters/chapter-01-one.txt")
        self.translation_ledger = {
            "schema_version": "1.1",
            "book_map_sha256": self.book_map_sha256,
            "text_ledger_sha256": self.source_ledger_sha256,
            "source_language": "English",
            "target_language": TARGET_LANGUAGE,
            "translation_decision": {
                "scope": "whole-book",
                "reason": "Source language is English.",
                "reviewed_by": REVIEWER,
                "evidence": [
                    {
                        "logical_page": 1,
                        "source_sha256": source_page_hash,
                        "source_span": "Original one.",
                        "reason": "English sentence.",
                    }
                ],
            },
            "translation_quality": {
                "profile": "faithful-contextual-ptbr-v1",
                "context_policy": "whole-chapter-with-neighbors-v1",
                "research_policy": "context-first-evidence-recorded-v1",
                "brief": {
                    "genre": "nonfiction",
                    "period": "modern",
                    "setting": "general",
                    "narrator_voice": "clear",
                    "register": "standard",
                    "style_goals": "faithful PT-BR",
                    "names_policy": "preserve names",
                    "foreign_fragments_policy": "translate contextually",
                    "reviewed_by": REVIEWER,
                },
                "glossary": [],
                "ambiguities": [],
                "review": {
                    "semantic_fidelity": "approved",
                    "literary_naturalness": "approved",
                    "whole_book_consistency": "approved",
                    "independent": True,
                    "reviewed_by": REVIEWER,
                },
            },
            "edition": {
                "book": {"title": "Livro", "subtitle": ""},
                "document_titles": [{"id": "chapter-01", "title": "Um"}],
            },
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": source_page_hash,
                    "translation_file": "translation/pt-BR/pages/page-0001.txt",
                    "translation_sha256": translation_page_hash,
                    "translated_by": EDITOR,
                    "reviewed_by": REVIEWER,
                    "notes": "",
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "source_file": "source/chapters/chapter-01-one.txt",
                    "source_sha256": source_chapter_hash,
                    "translation_file": "translation/pt-BR/chapters/chapter-01-one.txt",
                    "translation_sha256": translation_chapter_hash,
                    "source_pages": [{"logical_page": 1, "source_sha256": source_page_hash}],
                    "translated_by": EDITOR,
                    "reviewed_by": REVIEWER,
                }
            ],
        }
        self.translation_ledger_path = root / "translation-ledger.json"
        write_json(self.translation_ledger_path, self.translation_ledger)
        self.translation_ledger_sha256 = sha256_file(self.translation_ledger_path)

        self.fluid_style = {
            "schema_version": STYLE_SCHEMA_VERSION,
            "profile": FLUID_PROFILE,
            "language": TARGET_LANGUAGE,
            "base_edition": "translated-pt-br",
            "voice": {
                "register": "natural standard PT-BR",
                "tone": "clear and faithful",
                "cadence": "smooth narration",
                "terminology": "prefer reviewed glossary forms",
                "title_policy": "preserve approved chapter titles without embellishment",
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
            "glossary": [
                {
                    "term": "Base",
                    "preferred_form": "Base",
                    "notes": "Keep term stable.",
                    "status": "approved",
                    "reviewed_by": REVIEWER,
                }
            ],
            "reviewed_by": REVIEWER,
        }
        self.fluid_style_path = root / "fluid-style.json"
        write_json(self.fluid_style_path, self.fluid_style)
        self.fluid_style_sha256 = sha256_file(self.fluid_style_path)

        self.fluid_ledger = self._fluid_ledger(translation_chapter_hash)

    def add_second_output(self) -> None:
        source_page_text = "Original three.\n\nOriginal four.\n"
        translation_page_text = "Base três.\n\nBase quatro.\n"
        fluid_chapter_text = "Fluido três.\n\nFluido quatro.\n"
        write_text(self.text_root / "source/pages/page-0002.txt", source_page_text)
        write_text(self.text_root / "source/chapters/chapter-02-two.txt", source_page_text)
        write_text(self.text_root / "translation/pt-BR/pages/page-0002.txt", translation_page_text)
        write_text(self.text_root / "translation/pt-BR/chapters/chapter-02-two.txt", translation_page_text)
        write_text(self.text_root / "fluid/pt-BR/chapters/chapter-02-two.txt", fluid_chapter_text)
        write_text(
            self.text_root / "fluid/pt-BR/book.txt",
            self.fluid_chapter_text.rstrip() + "\n\n" + fluid_chapter_text.rstrip() + "\n",
        )

        self.book_map["pages"].append(
            {
                "logical_page": 2,
                "kind": "body",
                "blank": False,
                "chapter_id": "chapter-02",
            }
        )
        self.book_map["chapters"].append(
            {
                "id": "chapter-02",
                "number": 2,
                "title": "Two",
                "start_logical_page": 2,
                "end_logical_page": 2,
            }
        )
        write_json(self.book_map_path, self.book_map)
        self.book_map_sha256 = sha256_file(self.book_map_path)

        source_page_hash = sha256_file(self.text_root / "source/pages/page-0002.txt")
        source_chapter_hash = sha256_file(self.text_root / "source/chapters/chapter-02-two.txt")
        self.source_ledger["book_map_sha256"] = self.book_map_sha256
        self.source_ledger["pages"].append(
            {
                "logical_page": 2,
                "status": "verified",
                "source_file": "source/pages/page-0002.txt",
                "source_sha256": source_page_hash,
                "verified_by": REVIEWER,
            }
        )
        self.source_ledger["chapter_outputs"].append(
            {
                "id": "chapter-02",
                "source_file": "source/chapters/chapter-02-two.txt",
                "source_sha256": source_chapter_hash,
                "source_pages": [{"logical_page": 2, "source_sha256": source_page_hash}],
                "verified_by": REVIEWER,
            }
        )
        write_json(self.source_ledger_path, self.source_ledger)
        self.source_ledger_sha256 = sha256_file(self.source_ledger_path)

        translation_page_hash = sha256_file(self.text_root / "translation/pt-BR/pages/page-0002.txt")
        translation_chapter_hash = sha256_file(self.text_root / "translation/pt-BR/chapters/chapter-02-two.txt")
        self.translation_ledger["book_map_sha256"] = self.book_map_sha256
        self.translation_ledger["text_ledger_sha256"] = self.source_ledger_sha256
        self.translation_ledger["translation_decision"]["evidence"].append(
            {
                "logical_page": 2,
                "source_sha256": source_page_hash,
                "source_span": "Original three.",
                "reason": "English sentence.",
            }
        )
        self.translation_ledger["edition"]["document_titles"].append({"id": "chapter-02", "title": "Dois"})
        self.translation_ledger["pages"].append(
            {
                "logical_page": 2,
                "status": "verified",
                "source_file": "source/pages/page-0002.txt",
                "source_sha256": source_page_hash,
                "translation_file": "translation/pt-BR/pages/page-0002.txt",
                "translation_sha256": translation_page_hash,
                "translated_by": EDITOR,
                "reviewed_by": REVIEWER,
                "notes": "",
            }
        )
        self.translation_ledger["chapter_outputs"].append(
            {
                "id": "chapter-02",
                "source_file": "source/chapters/chapter-02-two.txt",
                "source_sha256": source_chapter_hash,
                "translation_file": "translation/pt-BR/chapters/chapter-02-two.txt",
                "translation_sha256": translation_chapter_hash,
                "source_pages": [{"logical_page": 2, "source_sha256": source_page_hash}],
                "translated_by": EDITOR,
                "reviewed_by": REVIEWER,
            }
        )
        write_json(self.translation_ledger_path, self.translation_ledger)
        self.translation_ledger_sha256 = sha256_file(self.translation_ledger_path)

        second_fluid_hash = sha256_file(self.text_root / "fluid/pt-BR/chapters/chapter-02-two.txt")
        self.fluid_ledger["book_map_sha256"] = self.book_map_sha256
        self.fluid_ledger["text_ledger_sha256"] = self.source_ledger_sha256
        self.fluid_ledger["base_ledger_sha256"] = self.translation_ledger_sha256
        self.fluid_ledger["book_output"]["fluid_sha256"] = sha256_file(self.text_root / "fluid/pt-BR/book.txt")
        self.fluid_ledger["book_output"]["chapter_ids"].append("chapter-02")
        self.fluid_ledger["edition"]["document_titles"].append({"id": "chapter-02", "title": "Dois"})
        self.fluid_ledger["chapter_outputs"].append(
            {
                "id": "chapter-02",
                "base_file": "translation/pt-BR/chapters/chapter-02-two.txt",
                "base_sha256": translation_chapter_hash,
                "fluid_file": "fluid/pt-BR/chapters/chapter-02-two.txt",
                "fluid_sha256": second_fluid_hash,
                "source_pages": [{"logical_page": 2, "source_sha256": source_page_hash}],
                "base_block_count": 2,
                "fluid_block_count": 2,
                "reviewed_by": REVIEWER,
            }
        )
        for position, (base_block, fluid_block) in enumerate(
            zip(split_blocks(translation_page_text), split_blocks(fluid_chapter_text)),
            start=1,
        ):
            self.fluid_ledger["blocks"].append(
                {
                    "id": f"chapter-02-b{position:04d}",
                    "output_id": "chapter-02",
                    "position": position,
                    "base_sha256": sha256_text(base_block),
                    "status": "included",
                    "fluid_position": position,
                    "fluid_sha256": sha256_text(fluid_block),
                    "change_kinds": ["fluency"],
                    "reviewed_by": REVIEWER,
                }
            )

    def use_portuguese_source_base(self) -> None:
        self.source_page_text = "Fonte um.\n\nFonte dois.\n"
        self.source_chapter_text = self.source_page_text
        write_text(self.text_root / "source/pages/page-0001.txt", self.source_page_text)
        write_text(self.text_root / "source/chapters/chapter-01-one.txt", self.source_chapter_text)
        self.book_map["analysis"]["source_language"] = "Portuguese"
        write_json(self.book_map_path, self.book_map)
        self.book_map_sha256 = sha256_file(self.book_map_path)

        source_page_hash = sha256_file(self.text_root / "source/pages/page-0001.txt")
        source_chapter_hash = sha256_file(self.text_root / "source/chapters/chapter-01-one.txt")
        self.source_ledger["book_map_sha256"] = self.book_map_sha256
        self.source_ledger["pages"][0]["source_sha256"] = source_page_hash
        self.source_ledger["chapter_outputs"][0]["source_sha256"] = source_chapter_hash
        self.source_ledger["chapter_outputs"][0]["source_pages"] = [
            {"logical_page": 1, "source_sha256": source_page_hash}
        ]
        write_json(self.source_ledger_path, self.source_ledger)
        self.source_ledger_sha256 = sha256_file(self.source_ledger_path)

        self.fluid_style["base_edition"] = "source"
        write_json(self.fluid_style_path, self.fluid_style)
        self.fluid_style_sha256 = sha256_file(self.fluid_style_path)

        fluid_chapter_hash = sha256_file(self.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt")
        fluid_book_hash = sha256_file(self.text_root / "fluid/pt-BR/book.txt")
        blocks = []
        for position, (base_block, fluid_block) in enumerate(
            zip(split_blocks(self.source_chapter_text), split_blocks(self.fluid_chapter_text)),
            start=1,
        ):
            blocks.append(
                {
                    "id": f"chapter-01-b{position:04d}",
                    "output_id": "chapter-01",
                    "position": position,
                    "base_sha256": sha256_text(base_block),
                    "status": "included",
                    "fluid_position": position,
                    "fluid_sha256": sha256_text(fluid_block),
                    "change_kinds": ["fluency"],
                    "reviewed_by": REVIEWER,
                }
            )
        self.fluid_ledger = {
            "schema_version": SCHEMA_VERSION,
            "book_map_sha256": self.book_map_sha256,
            "text_ledger_sha256": self.source_ledger_sha256,
            "base_edition": "source",
            "base_ledger_sha256": self.source_ledger_sha256,
            "fluid_style_sha256": self.fluid_style_sha256,
            "language": TARGET_LANGUAGE,
            "profile": FLUID_PROFILE,
            "status": "approved",
            "edited_by": EDITOR,
            "reviewed_by": REVIEWER,
            "edition": {
                "book": {"title": "Livro fluido"},
                "document_titles": [{"id": "chapter-01", "title": "Um"}],
            },
            "review": {
                "semantic_fidelity": "approved",
                "no_additions": "approved",
                "no_omissions": "approved",
                "archaic_modernization": "approved",
                "editorial_exclusions": "approved",
                "fluency": "approved",
                "whole_book_consistency": "approved",
                "independent": True,
                "reviewed_by": REVIEWER,
            },
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "base_file": "source/chapters/chapter-01-one.txt",
                    "base_sha256": source_chapter_hash,
                    "fluid_file": "fluid/pt-BR/chapters/chapter-01-one.txt",
                    "fluid_sha256": fluid_chapter_hash,
                    "source_pages": [{"logical_page": 1, "source_sha256": source_page_hash}],
                    "base_block_count": 2,
                    "fluid_block_count": 2,
                    "reviewed_by": REVIEWER,
                }
            ],
            "blocks": blocks,
            "book_output": {
                "fluid_file": (FLUID_ROOT / "book.txt").as_posix(),
                "fluid_sha256": fluid_book_hash,
                "chapter_ids": ["chapter-01"],
                "separator": "double-newline",
                "reviewed_by": REVIEWER,
            },
        }

    def _fluid_ledger(self, translation_chapter_hash: str) -> dict:
        fluid_chapter_hash = sha256_file(self.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt")
        fluid_book_hash = sha256_file(self.text_root / "fluid/pt-BR/book.txt")
        base_blocks = split_blocks(self.translation_chapter_text)
        fluid_blocks = split_blocks(self.fluid_chapter_text)
        blocks = []
        for position, (base_block, fluid_block) in enumerate(zip(base_blocks, fluid_blocks), start=1):
            blocks.append(
                {
                    "id": f"chapter-01-b{position:04d}",
                    "output_id": "chapter-01",
                    "position": position,
                    "base_sha256": sha256_text(base_block),
                    "status": "included",
                    "fluid_position": position,
                    "fluid_sha256": sha256_text(fluid_block),
                    "change_kinds": ["fluency"],
                    "reviewed_by": REVIEWER,
                }
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "book_map_sha256": self.book_map_sha256,
            "text_ledger_sha256": self.source_ledger_sha256,
            "base_edition": "translated-pt-br",
            "base_ledger_sha256": self.translation_ledger_sha256,
            "fluid_style_sha256": self.fluid_style_sha256,
            "language": TARGET_LANGUAGE,
            "profile": FLUID_PROFILE,
            "status": "approved",
            "edited_by": EDITOR,
            "reviewed_by": REVIEWER,
            "edition": {
                "book": {"title": "Livro fluido"},
                "document_titles": [{"id": "chapter-01", "title": "Um"}],
            },
            "review": {
                "semantic_fidelity": "approved",
                "no_additions": "approved",
                "no_omissions": "approved",
                "archaic_modernization": "approved",
                "editorial_exclusions": "approved",
                "fluency": "approved",
                "whole_book_consistency": "approved",
                "independent": True,
                "reviewed_by": REVIEWER,
            },
            "chapter_outputs": [
                {
                    "id": "chapter-01",
                    "base_file": "translation/pt-BR/chapters/chapter-01-one.txt",
                    "base_sha256": translation_chapter_hash,
                    "fluid_file": "fluid/pt-BR/chapters/chapter-01-one.txt",
                    "fluid_sha256": fluid_chapter_hash,
                    "source_pages": self.translation_ledger["chapter_outputs"][0]["source_pages"],
                    "base_block_count": 2,
                    "fluid_block_count": 2,
                    "reviewed_by": REVIEWER,
                }
            ],
            "blocks": blocks,
            "book_output": {
                "fluid_file": (FLUID_ROOT / "book.txt").as_posix(),
                "fluid_sha256": fluid_book_hash,
                "chapter_ids": ["chapter-01"],
                "separator": "double-newline",
                "reviewed_by": REVIEWER,
            },
        }

    def verify(self, *, ledger: dict | None = None, style: dict | None = None) -> list[str]:
        return verify(
            self.book_map,
            self.book_map_sha256,
            self.source_ledger,
            self.source_ledger_sha256,
            self.translation_ledger,
            self.translation_ledger_sha256,
            self.fluid_style if style is None else style,
            self.fluid_style_sha256,
            self.fluid_ledger if ledger is None else ledger,
            self.text_root,
        )


class FluidEditionLedgerTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> Fixture:
        return Fixture(root)

    def downgrade_to_legacy_schema(
        self,
        fixture: Fixture,
        schema_version: str,
    ) -> tuple[dict, dict]:
        style = copy.deepcopy(fixture.fluid_style)
        style["schema_version"] = schema_version
        for field in (
            "omit_parenthetical_citation_references",
            "omit_immediate_duplicate_translations",
            "omit_translation_labels",
        ):
            style["rules"].pop(field)
        if schema_version == "1.0":
            for field in (
                "modernize_all_archaic_language",
                "modernize_historical_quotations",
                "modernize_orthography_and_diacritics",
            ):
                style["rules"].pop(field)

        ledger = copy.deepcopy(fixture.fluid_ledger)
        ledger["schema_version"] = schema_version
        ledger["review"].pop("editorial_exclusions")
        if schema_version == "1.0":
            ledger["review"].pop("archaic_modernization")
        for output in ledger["chapter_outputs"]:
            output["block_count"] = output.pop("base_block_count")
            output.pop("fluid_block_count")
        for block in ledger["blocks"]:
            block.pop("status")
            block.pop("fluid_position")
        return style, ledger

    def exclude_second_block(
        self,
        fixture: Fixture,
        change_kind: str = "duplicate_translation_exclusion",
    ) -> dict:
        fluid_text = "Fluido um.\n"
        write_text(
            fixture.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt",
            fluid_text,
        )
        write_text(fixture.text_root / "fluid/pt-BR/book.txt", fluid_text)

        ledger = copy.deepcopy(fixture.fluid_ledger)
        ledger["chapter_outputs"][0]["fluid_sha256"] = sha256_file(
            fixture.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt"
        )
        ledger["chapter_outputs"][0]["fluid_block_count"] = 1
        ledger["book_output"]["fluid_sha256"] = sha256_file(
            fixture.text_root / "fluid/pt-BR/book.txt"
        )
        ledger["blocks"][0]["fluid_sha256"] = sha256_text("Fluido um.")
        ledger["blocks"][1]["status"] = "excluded"
        ledger["blocks"][1]["fluid_position"] = None
        ledger["blocks"][1]["fluid_sha256"] = None
        ledger["blocks"][1]["change_kinds"] = [change_kind]
        return ledger

    def test_valid_translated_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            self.assertEqual([], fixture.verify())
            self.assertIn("chapter-01", fluid_chapter_output_records(fixture.fluid_ledger))
            self.assertEqual({"chapter-01": "Um"}, fluid_document_titles(fixture.fluid_ledger))

    def test_gap_duplicate_coverage_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = copy.deepcopy(fixture.fluid_ledger)
            ledger["blocks"][1]["position"] = 1
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(any("exactly once in order" in error for error in errors), errors)

    def test_base_and_fluid_hash_drift_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            write_text(fixture.text_root / "fluid/pt-BR/chapters/chapter-01-one.txt", "Fluido alterado.\n\nFluido dois.\n")
            errors = fixture.verify()
            self.assertTrue(any("fluid_sha256 does not match fluid_file" in error for error in errors), errors)
            self.assertTrue(any("fluid block text" in error for error in errors), errors)

    def test_inconsistent_profile_style_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            style = copy.deepcopy(fixture.fluid_style)
            style["profile"] = "wrong-profile"
            ledger = copy.deepcopy(fixture.fluid_ledger)
            ledger["profile"] = "wrong-profile"
            errors = fixture.verify(ledger=ledger, style=style)
            self.assertTrue(any("fluid style profile" in error for error in errors), errors)
            self.assertTrue(any("fluid ledger.profile" in error for error in errors), errors)

    def test_duplicate_resolved_fluid_file_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            fixture.add_second_output()
            ledger = copy.deepcopy(fixture.fluid_ledger)
            first_output = ledger["chapter_outputs"][0]
            second_output = ledger["chapter_outputs"][1]
            second_output["fluid_file"] = first_output["fluid_file"]
            second_output["fluid_sha256"] = first_output["fluid_sha256"]
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(any("duplicate fluid chapter path" in error for error in errors), errors)

    def test_top_level_reviewer_must_differ_from_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = copy.deepcopy(fixture.fluid_ledger)
            ledger["edited_by"] = REVIEWER
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(any("reviewed_by must differ from" in error for error in errors), errors)

    def test_missing_title_policy_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            style = copy.deepcopy(fixture.fluid_style)
            del style["voice"]["title_policy"]
            errors = fixture.verify(style=style)
            self.assertTrue(any("voice.title_policy" in error for error in errors), errors)

    def test_current_style_requires_comprehensive_archaism_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            style = copy.deepcopy(fixture.fluid_style)
            del style["rules"]["modernize_historical_quotations"]
            errors = fixture.verify(style=style)
            self.assertTrue(
                any("modernize_historical_quotations" in error for error in errors),
                errors,
            )

    def test_current_style_requires_editorial_exclusion_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            style = copy.deepcopy(fixture.fluid_style)
            del style["rules"]["omit_immediate_duplicate_translations"]
            errors = fixture.verify(style=style)
            self.assertTrue(
                any("omit_immediate_duplicate_translations" in error for error in errors),
                errors,
            )

    def test_legacy_style_and_ledger_schemas_remain_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            for schema_version in ("1.0", "1.1"):
                with self.subTest(schema_version=schema_version):
                    style, ledger = self.downgrade_to_legacy_schema(
                        fixture,
                        schema_version,
                    )
                    self.assertEqual([], fixture.verify(style=style, ledger=ledger))

    def test_legacy_schema_rejects_editorial_exclusion_change_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            style, ledger = self.downgrade_to_legacy_schema(fixture, "1.1")
            ledger["blocks"][0]["change_kinds"] = ["citation_reference_exclusion"]
            errors = fixture.verify(style=style, ledger=ledger)
            self.assertTrue(
                any("change_kinds contains invalid values" in error for error in errors),
                errors,
            )

    def test_valid_duplicate_translation_block_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = self.exclude_second_block(fixture)
            self.assertEqual([], fixture.verify(ledger=ledger))

    def test_valid_translation_label_block_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = self.exclude_second_block(
                fixture,
                "translation_label_exclusion",
            )
            self.assertEqual([], fixture.verify(ledger=ledger))

    def test_valid_standalone_citation_block_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = self.exclude_second_block(
                fixture,
                "citation_reference_exclusion",
            )
            self.assertEqual([], fixture.verify(ledger=ledger))

    def test_excluded_block_rejects_fluid_position_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = self.exclude_second_block(fixture)
            ledger["blocks"][1]["fluid_position"] = 2
            ledger["blocks"][1]["fluid_sha256"] = sha256_text("Fluido dois.")
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(
                any("fluid_position must be null for an excluded block" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("fluid_sha256 must be null for an excluded block" in error for error in errors),
                errors,
            )

    def test_malformed_mixed_change_kinds_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            ledger = copy.deepcopy(fixture.fluid_ledger)
            ledger["blocks"][0]["change_kinds"] = ["fluency", {"bad": "value"}, ["clarity"], 42]
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(any("change_kinds[1] must be a string" in error for error in errors), errors)
            self.assertTrue(any("change_kinds[2] must be a string" in error for error in errors), errors)
            self.assertTrue(any("change_kinds[3] must be a string" in error for error in errors), errors)

    def test_valid_portuguese_source_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            fixture.use_portuguese_source_base()
            self.assertEqual([], fixture.verify())

    def test_aggregate_book_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.make_fixture(Path(temp))
            book_path = fixture.text_root / "fluid/pt-BR/book.txt"
            write_text(book_path, "Conteúdo agregado errado.\n")
            ledger = copy.deepcopy(fixture.fluid_ledger)
            ledger["book_output"]["fluid_sha256"] = sha256_file(book_path)
            errors = fixture.verify(ledger=ledger)
            self.assertTrue(any("canonical chapter join" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
