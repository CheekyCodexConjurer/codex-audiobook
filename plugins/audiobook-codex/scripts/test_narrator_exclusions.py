from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from validate_narrator_lineage import (
    _narration_excluded_base_ids,
    _narration_excluded_pages,
    _validate_changes,
    _validate_outputs,
    sha256_file,
)


class NarratorMappedExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.text_root = Path(self.temporary.name) / "text"
        translated = self.text_root / "translation" / "pt-BR" / "chapters"
        locutor = self.text_root / "locutor"
        locutor_chapters = locutor / "chapters"
        translated.mkdir(parents=True)
        locutor_chapters.mkdir(parents=True)

        self.retained = translated / "chapter-01.txt"
        self.excluded = translated / "front-02-contents.txt"
        self.locutor = locutor / "book.txt"
        self.locutor_chapter = locutor_chapters / "chapter-01.txt"
        self.retained.write_text("Capítulo um.\nTexto narrado.\n", encoding="utf-8")
        self.excluded.write_text("Sumário\nCapítulo um 1\n", encoding="utf-8")
        self.locutor.write_text(self.retained.read_text(encoding="utf-8"), encoding="utf-8")
        self.locutor_chapter.write_text(
            self.retained.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        self.base_outputs = {
            "chapter-01": {
                "translation_file": "translation/pt-BR/chapters/chapter-01.txt",
                "translation_sha256": sha256_file(self.retained),
                "source_pages": [{"logical_page": 7}],
            },
            "front-02": {
                "translation_file": "translation/pt-BR/chapters/front-02-contents.txt",
                "translation_sha256": sha256_file(self.excluded),
                "source_pages": [{"logical_page": 4}],
            },
        }
        self.book_map = {
            "ranges": {
                "narration_excluded": [
                    {
                        "logical_start_page": 4,
                        "logical_end_page": 4,
                        "reason": "Contents are not narrated.",
                    }
                ]
            }
        }
        self.narrator_changes = {
            "outputs": [
                {
                    "id": "book",
                    "kind": "full-book",
                    "locutor_file": "locutor/book.txt",
                    "locutor_sha256": sha256_file(self.locutor),
                    "reviewed_by": "codex",
                    "base_outputs": [
                        {
                            "id": "chapter-01",
                            "base_file": "translation/pt-BR/chapters/chapter-01.txt",
                            "base_sha256": sha256_file(self.retained),
                            "locutor_file": "locutor/chapters/chapter-01.txt",
                        }
                    ],
                }
            ],
            "changes": [
                {
                    "output_id": "book",
                    "kind": "mapped_exclusion",
                    "base_output_id": "front-02",
                    "base_span": "Sumário",
                    "locutor_span": "",
                    "logical_pages": [4],
                    "reason": "The validated book map excludes the contents from narration.",
                    "reviewed_by": "codex",
                }
            ],
        }

    def exclusion_context(self) -> tuple[set[str], set[int]]:
        return (
            _narration_excluded_base_ids(self.book_map, self.base_outputs),
            _narration_excluded_pages(self.book_map),
        )

    def test_full_book_can_omit_a_complete_map_backed_base_output(self) -> None:
        excluded_ids, excluded_pages = self.exclusion_context()
        output_errors, selected = _validate_outputs(
            self.narrator_changes,
            self.base_outputs,
            self.text_root,
            self.locutor.resolve(),
            excluded_ids,
        )
        self.assertEqual(output_errors, [])
        self.assertIsNotNone(selected)
        self.assertEqual(
            _validate_changes(
                self.narrator_changes,
                {"book": self.narrator_changes["outputs"][0]},
                self.base_outputs,
                self.text_root,
                "translated-pt-br",
                set(),
                excluded_ids,
                excluded_pages,
            ),
            [],
        )

    def test_omitted_base_output_requires_a_mapped_exclusion_record(self) -> None:
        excluded_ids, excluded_pages = self.exclusion_context()
        self.narrator_changes["changes"] = []
        errors = _validate_changes(
            self.narrator_changes,
            {"book": self.narrator_changes["outputs"][0]},
            self.base_outputs,
            self.text_root,
            "translated-pt-br",
            set(),
            excluded_ids,
            excluded_pages,
        )
        self.assertTrue(any("requires one mapped_exclusion" in error for error in errors))

    def test_full_book_cannot_omit_an_unmapped_base_output(self) -> None:
        output_errors, _ = _validate_outputs(
            self.narrator_changes,
            self.base_outputs,
            self.text_root,
            self.locutor.resolve(),
            set(),
        )
        self.assertTrue(
            any("complete map-backed narration exclusions" in error for error in output_errors)
        )

    def test_complete_mapped_exclusion_must_cover_exact_base_pages(self) -> None:
        excluded_ids, excluded_pages = self.exclusion_context()
        self.narrator_changes["changes"][0]["logical_pages"] = [4, 5]
        errors = _validate_changes(
            self.narrator_changes,
            {"book": self.narrator_changes["outputs"][0]},
            self.base_outputs,
            self.text_root,
            "translated-pt-br",
            set(),
            excluded_ids,
            excluded_pages,
        )
        self.assertTrue(
            any("must exactly cover the omitted base output" in error for error in errors)
        )


class NarratorFootnoteExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.text_root = Path(self.temporary.name) / "text"
        source = self.text_root / "source" / "chapters"
        locutor = self.text_root / "locutor"
        source.mkdir(parents=True)
        locutor.mkdir(parents=True)

        self.base = source / "chapter-01.txt"
        self.locutor = locutor / "book.txt"
        self.base.write_text(
            "Texto principal1. 1 Nota de rodapé. Continuação.\n",
            encoding="utf-8",
        )
        self.locutor.write_text(
            "Texto principal. Continuação.\n",
            encoding="utf-8",
        )
        self.base_outputs = {
            "chapter-01": {
                "source_file": "source/chapters/chapter-01.txt",
                "source_sha256": sha256_file(self.base),
                "source_pages": [{"logical_page": 1}],
            }
        }
        self.output = {
            "id": "book",
            "kind": "full-book",
            "locutor_file": "locutor/book.txt",
            "locutor_sha256": sha256_file(self.locutor),
            "reviewed_by": "codex",
            "base_outputs": [
                {
                    "id": "chapter-01",
                    "base_file": "source/chapters/chapter-01.txt",
                    "base_sha256": sha256_file(self.base),
                }
            ],
        }
        self.narrator_changes = {
            "outputs": [self.output],
            "changes": [
                {
                    "output_id": "book",
                    "kind": "footnote_exclusion",
                    "base_output_id": "chapter-01",
                    "note_id": "note-1",
                    "note_part": "marker",
                    "base_span": "Texto principal1.",
                    "locutor_span": "Texto principal.",
                    "logical_pages": [1],
                    "reason": "The attached semantic footnote marker is not narrated.",
                    "reviewed_by": "codex",
                },
                {
                    "output_id": "book",
                    "kind": "footnote_exclusion",
                    "base_output_id": "chapter-01",
                    "note_id": "note-1",
                    "note_part": "content",
                    "base_span": "1 Nota de rodapé.",
                    "locutor_span": "",
                    "logical_pages": [1],
                    "reason": "The semantic footnote remains textual but is not narrated.",
                    "reviewed_by": "codex",
                },
            ],
        }

    def validate(self) -> list[str]:
        return _validate_changes(
            self.narrator_changes,
            {"book": self.output},
            self.base_outputs,
            self.text_root,
            "faithful",
            set(),
            set(),
            set(),
        )

    def test_note_body_and_attached_marker_can_be_excluded_from_narration(self) -> None:
        self.assertEqual([], self.validate())

    def test_footnote_exclusion_requires_semantic_note_id(self) -> None:
        self.narrator_changes["changes"][1].pop("note_id")
        errors = self.validate()
        self.assertTrue(any("note_id must be non-empty" in error for error in errors))

    def test_footnote_content_requires_an_empty_spoken_span(self) -> None:
        self.narrator_changes["changes"][1]["locutor_span"] = "Continuação."
        errors = self.validate()
        self.assertTrue(
            any("empty string for footnote content" in error for error in errors)
        )


class NarratorSupplementaryMatterExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.text_root = Path(self.temporary.name) / "text"
        source = self.text_root / "source" / "chapters"
        locutor = self.text_root / "locutor"
        locutor_chapters = locutor / "chapters"
        source.mkdir(parents=True)
        locutor_chapters.mkdir(parents=True)

        self.base = source / "chapter-01.txt"
        self.locutor = locutor / "book.txt"
        self.locutor_chapter = locutor_chapters / "chapter-01.txt"
        self.base.write_text(
            "Conteúdo principal.\n\nReferências\n\nAUTOR. Obra. 2020.\n",
            encoding="utf-8",
        )
        self.locutor.write_text("Conteúdo principal.\n", encoding="utf-8")
        self.locutor_chapter.write_text("Conteúdo principal.\n", encoding="utf-8")
        self.base_outputs = {
            "chapter-01": {
                "source_file": "source/chapters/chapter-01.txt",
                "source_sha256": sha256_file(self.base),
                "source_pages": [
                    {"logical_page": 1},
                    {"logical_page": 2},
                ],
            }
        }
        self.output = {
            "id": "book",
            "kind": "full-book",
            "locutor_file": "locutor/book.txt",
            "locutor_sha256": sha256_file(self.locutor),
            "reviewed_by": "codex",
            "base_outputs": [
                {
                    "id": "chapter-01",
                    "base_file": "source/chapters/chapter-01.txt",
                    "base_sha256": sha256_file(self.base),
                    "locutor_file": "locutor/chapters/chapter-01.txt",
                }
            ],
        }
        self.change = {
            "output_id": "book",
            "kind": "supplementary_matter_exclusion",
            "matter_kind": "references",
            "base_output_id": "chapter-01",
            "base_span": "Referências AUTOR. Obra. 2020.",
            "locutor_span": "",
            "logical_pages": [2],
            "reason": (
                "The reference list remains in the textual edition but is not narrated."
            ),
            "reviewed_by": "codex",
        }
        self.narrator_changes = {
            "outputs": [self.output],
            "changes": [self.change],
        }

    def validate(self) -> list[str]:
        return _validate_changes(
            self.narrator_changes,
            {"book": self.output},
            self.base_outputs,
            self.text_root,
            "faithful",
            set(),
            set(),
            set(),
        )

    def test_trailing_reference_list_can_be_excluded_from_narration(self) -> None:
        self.assertEqual([], self.validate())

    def test_supplementary_exclusion_requires_empty_spoken_span(self) -> None:
        self.change["locutor_span"] = "Referências."
        errors = self.validate()
        self.assertTrue(
            any("empty string for a supplementary matter exclusion" in error for error in errors)
        )

    def test_supplementary_exclusion_must_be_trailing(self) -> None:
        self.change["base_span"] = "Conteúdo principal."
        errors = self.validate()
        self.assertTrue(
            any("must be a trailing span" in error for error in errors)
        )

    def test_supplementary_exclusion_requires_supported_kind(self) -> None:
        self.change["matter_kind"] = "appendix"
        errors = self.validate()
        self.assertTrue(
            any("supported supplementary back matter" in error for error in errors)
        )


class NarratorPageFurnitureExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.text_root = Path(self.temporary.name) / "text"
        fluid = self.text_root / "fluid" / "pt-BR" / "chapters"
        locutor = self.text_root / "locutor"
        fluid.mkdir(parents=True)
        locutor.mkdir(parents=True)

        self.base = fluid / "chapter-01.txt"
        self.locutor = locutor / "book.txt"
        self.base.write_text(
            "Primeiro parágrafo.\n\n281\n\nSegundo parágrafo.\n",
            encoding="utf-8",
        )
        self.locutor.write_text(
            "Primeiro parágrafo.\nSegundo parágrafo.\n",
            encoding="utf-8",
        )
        self.base_outputs = {
            "chapter-01": {
                "fluid_file": "fluid/pt-BR/chapters/chapter-01.txt",
                "fluid_sha256": sha256_file(self.base),
                "source_pages": [{"logical_page": 1}],
            }
        }
        self.output = {
            "id": "book",
            "kind": "full-book",
            "locutor_file": "locutor/book.txt",
            "locutor_sha256": sha256_file(self.locutor),
            "reviewed_by": "codex",
            "base_outputs": [
                {
                    "id": "chapter-01",
                    "base_file": "fluid/pt-BR/chapters/chapter-01.txt",
                    "base_sha256": sha256_file(self.base),
                }
            ],
        }
        self.change = {
            "output_id": "book",
            "kind": "page_furniture_exclusion",
            "base_output_id": "chapter-01",
            "base_span": "281",
            "locutor_span": "",
            "logical_pages": [1],
            "reason": "The standalone printed folio is page furniture, not narrated content.",
            "reviewed_by": "codex",
        }
        self.narrator_changes = {
            "outputs": [self.output],
            "changes": [self.change],
        }

    def validate(self) -> list[str]:
        return _validate_changes(
            self.narrator_changes,
            {"book": self.output},
            self.base_outputs,
            self.text_root,
            "fluid-pt-br",
            set(),
            set(),
            set(),
        )

    def test_standalone_numeric_folio_can_be_excluded(self) -> None:
        self.assertEqual([], self.validate())

    def test_page_furniture_requires_empty_spoken_span(self) -> None:
        self.change["locutor_span"] = "Duzentos e oitenta e um."
        errors = self.validate()
        self.assertTrue(
            any("empty string for a page furniture exclusion" in error for error in errors)
        )

    def test_page_furniture_must_be_a_standalone_numeric_folio(self) -> None:
        self.change["base_span"] = "Primeiro parágrafo."
        errors = self.validate()
        self.assertTrue(
            any("standalone numeric printed folio" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
