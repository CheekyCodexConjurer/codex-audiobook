from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from pypdf import PdfReader

from book_layout import BookPaths
from epub_layout import validate_layout
from export_epub import (
    _layout_contract_edition,
    join_semantic_values,
    semantic_block_groups,
    semantic_body_parts,
    published_documents,
    validate_documents,
    write_epub,
)
from export_pdf import write_pdf
from narration_plan import _chapter_records
from publish_artifacts import validate_fluid_publication_sidecar
from publish_artifacts import (
    Publication,
    align_audio_publications_with_reader_names,
)
from test_fluid_edition_ledger import Fixture, sha256_file, write_json, write_text
from validate_narrator_lineage import _validate_outputs


ROOT = Path(__file__).resolve().parent


def opf_identifier(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        content = archive.read("OEBPS/content.opf").decode("utf-8")
    marker = '<dc:identifier id="bookid">'
    return content.split(marker, 1)[1].split("</dc:identifier>", 1)[0]


class FluidExportTests(unittest.TestCase):
    def test_fluid_audiobook_uses_the_reader_filename_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            public_root = Path(temp) / "Livro - 2026 - Autor"
            assembly_root = public_root / "assembly"
            public_root.mkdir()
            source = assembly_root / "audio" / "masculina-v1" / "audiobook.mp3"
            write_text(source, "audio")
            audio = Publication(
                "audio",
                source,
                public_root / "audiobook.mp3",
                {
                    "path": "audiobook.mp3",
                    "path_root": "book",
                    "source_path": "audio/masculina-v1/audiobook.mp3",
                    "source_path_root": "assembly",
                    "source_sha256": sha256_file(source),
                    "sha256": sha256_file(source),
                    "published_at": "2026-07-20T00:00:00+00:00",
                    "text_edition": "fluid-pt-br",
                },
                "fluid-pt-br",
            )
            reader = Publication(
                "epub",
                assembly_root / "exports" / "epub" / "livro.epub",
                public_root / "Livro-fluido.epub",
                {
                    "text_edition": "fluid-pt-br",
                    "image_edition": "original",
                },
                "fluid-pt-br:original",
            )
            manifests = {source: {"publication": audio.record}}
            aligned = align_audio_publications_with_reader_names(
                BookPaths(public_root, assembly_root, "new"),
                {},
                [audio, reader],
                manifests,
            )
            renamed = aligned[0]
            self.assertEqual("Livro-fluido.mp3", renamed.destination.name)
            self.assertEqual("Livro-fluido.mp3", manifests[source]["publication"]["path"])

    def test_fluid_reader_exports_omit_terminal_supplementary_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            chapter_path = root / "text" / "fluid" / "pt-BR" / "chapters" / "chapter.txt"
            references_path = (
                root / "text" / "fluid" / "pt-BR" / "chapters" / "references.txt"
            )
            write_text(
                chapter_path,
                "Capítulo\n\nConteúdo principal.\n\nReferências\n\nAUTOR. Obra. 2026.\n",
            )
            write_text(references_path, "Referências\n\nAUTOR. Obra. 2026.\n")
            chapter_file = chapter_path.relative_to(root).as_posix()
            documents = [
                {
                    "id": "chapter-01",
                    "kind": "chapter",
                    "title": "Capítulo",
                    "asset_ids": [],
                    "_text_path": chapter_path,
                    "_layout_blocks": [
                        {
                            "kind": "heading",
                            "level": 1,
                            "text_file": chapter_file,
                            "block_index": 1,
                        },
                        {
                            "kind": "paragraph",
                            "text_file": chapter_file,
                            "block_index": 2,
                        },
                        {
                            "kind": "heading",
                            "level": 1,
                            "text_file": chapter_file,
                            "block_index": 3,
                        },
                        {
                            "kind": "paragraph",
                            "text_file": chapter_file,
                            "block_index": 4,
                        },
                    ],
                },
                {
                    "id": "references",
                    "kind": "chapter",
                    "title": "Referências",
                    "asset_ids": [],
                    "_text_path": references_path,
                },
            ]
            assets = {"chapter-01": [], "references": []}
            self.assertEqual(
                ["chapter-01"],
                [
                    document["id"]
                    for document in published_documents(documents, "fluid-pt-br")
                ],
            )

            epub_path = root / "exports" / "epub" / "fluid.epub"
            write_epub(
                epub_path,
                root,
                {
                    "title": "Livro fluido",
                    "subtitle": "",
                    "author": "Autor",
                    "publication_year": 2026,
                    "publication_place": "",
                },
                "pt-BR",
                "fluid-pt-br",
                documents,
                assets,
                None,
            )
            with zipfile.ZipFile(epub_path) as archive:
                content = archive.read("OEBPS/content.opf").decode("utf-8")
                nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
                chapter = archive.read("OEBPS/text/001-chapter-01.xhtml").decode("utf-8")
                self.assertNotIn("002-references.xhtml", "\n".join(archive.namelist()))
            self.assertIn("Conteúdo principal.", chapter)
            self.assertNotIn("Referências", content)
            self.assertNotIn("Referências", nav)

            pdf_path = root / "exports" / "pdf" / "fluid.pdf"
            write_pdf(
                pdf_path,
                root,
                {
                    "title": "Livro fluido",
                    "subtitle": "",
                    "author": "Autor",
                    "publication_year": 2026,
                    "publication_place": "",
                },
                "pt-BR",
                "fluid-pt-br",
                documents,
                assets,
                None,
            )
            extracted = " ".join(
                page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages
            )
            self.assertIn("Conteúdo principal.", extracted)
            self.assertNotIn("Referências", extracted)

    def test_revised_edition_reuses_original_layout_contract(self) -> None:
        self.assertEqual("original", _layout_contract_edition("original"))
        self.assertEqual("original", _layout_contract_edition("revised-pt-br"))
        self.assertEqual(
            "fluid-pt-br",
            _layout_contract_edition("fluid-pt-br"),
        )

    def test_fluid_layout_epub_pdf_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = Fixture(root)
            metadata = root / "metadata"
            metadata.mkdir()
            fluid_ledger_path = metadata / "fluid-edition-ledger.json"
            write_json(fluid_ledger_path, fixture.fluid_ledger)
            fluid_output = fixture.fluid_ledger["chapter_outputs"][0]
            fluid_file = f"text/{fluid_output['fluid_file']}"
            layout = {
                "schema_version": "1.0",
                "text_edition": "fluid-pt-br",
                "book_map_sha256": fixture.book_map_sha256,
                "text_ledger_sha256": fixture.source_ledger_sha256,
                "fluid_edition_ledger_sha256": sha256_file(fluid_ledger_path),
                "documents": [
                    {
                        "id": "chapter-01",
                        "blocks": [
                            {
                                "kind": "heading",
                                "level": 1,
                                "text_file": fluid_file,
                                "text_sha256": fluid_output["fluid_sha256"],
                                "block_index": 1,
                            },
                            {
                                "kind": "paragraph",
                                "text_file": fluid_file,
                                "text_sha256": fluid_output["fluid_sha256"],
                                "block_index": 2,
                            },
                        ],
                    }
                ],
            }
            self.assertEqual(
                [],
                validate_layout(
                    layout,
                    root,
                    fixture.book_map_sha256,
                    fixture.source_ledger_sha256,
                    fixture.source_ledger,
                    ["chapter-01"],
                    text_edition="fluid-pt-br",
                    edition_ledger_sha256=sha256_file(fluid_ledger_path),
                    edition_outputs={"chapter-01": fluid_output},
                ),
            )

            source_output = fixture.source_ledger["chapter_outputs"][0]
            translation_output = fixture.translation_ledger["chapter_outputs"][0]
            manifest = {
                "documents": [
                    {
                        "id": "chapter-01",
                        "kind": "chapter",
                        "title": "Um",
                        "source_file": f"text/{source_output['source_file']}",
                        "source_sha256": source_output["source_sha256"],
                        "translation_file": (
                            f"text/{translation_output['translation_file']}"
                        ),
                        "translation_sha256": translation_output[
                            "translation_sha256"
                        ],
                        "fluid_file": fluid_file,
                        "fluid_sha256": fluid_output["fluid_sha256"],
                        "asset_ids": [],
                    }
                ]
            }
            documents, assets = validate_documents(
                root,
                manifest,
                {"assets": []},
                fixture.source_ledger,
                "fluid-pt-br",
                fixture.translation_ledger,
                None,
                fixture.fluid_ledger,
                layout,
            )
            self.assertEqual({}, assets)
            book = {
                "title": "Livro fluido",
                "subtitle": "",
                "author": "Autor",
                "publication_year": 2026,
                "publication_place": "",
            }
            selected_assets = {"chapter-01": []}
            epub_root = root / "exports" / "epub"
            fluid_epub = epub_root / "fluid.epub"
            translated_epub = epub_root / "translated.epub"
            write_epub(
                fluid_epub,
                root,
                book,
                "pt-BR",
                "fluid-pt-br",
                documents,
                selected_assets,
                None,
            )
            write_epub(
                translated_epub,
                root,
                book,
                "pt-BR",
                "translated-pt-br",
                documents,
                selected_assets,
                None,
            )
            self.assertNotEqual(
                opf_identifier(fluid_epub),
                opf_identifier(translated_epub),
            )
            with zipfile.ZipFile(fluid_epub) as archive:
                xhtml = archive.read(
                    "OEBPS/text/001-chapter-01.xhtml"
                ).decode("utf-8")
            self.assertIn("Fluido um.", xhtml)
            self.assertIn("Fluido dois.", xhtml)

            pdf_path = root / "exports" / "pdf" / "fluid.pdf"
            write_pdf(
                pdf_path,
                root,
                book,
                "pt-BR",
                "fluid-pt-br",
                documents,
                selected_assets,
                None,
            )
            extracted = " ".join(
                page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages
            )
            self.assertIn("Fluido um.", extracted)
            self.assertIn("Fluido dois.", extracted)

    def test_fluid_layout_joins_verified_paragraph_continuations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = Fixture(root)
            fluid_output = fixture.fluid_ledger["chapter_outputs"][0]
            fluid_file = f"text/{fluid_output['fluid_file']}"
            blocks = [
                {
                    "kind": "paragraph",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 1,
                },
                {
                    "kind": "paragraph",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 2,
                    "join_with_previous": True,
                },
            ]
            layout = {
                "schema_version": "1.0",
                "text_edition": "fluid-pt-br",
                "book_map_sha256": fixture.book_map_sha256,
                "text_ledger_sha256": fixture.source_ledger_sha256,
                "fluid_edition_ledger_sha256": "f" * 64,
                "documents": [{"id": "chapter-01", "blocks": blocks}],
            }
            self.assertEqual(
                [],
                validate_layout(
                    layout,
                    root,
                    fixture.book_map_sha256,
                    fixture.source_ledger_sha256,
                    fixture.source_ledger,
                    ["chapter-01"],
                    text_edition="fluid-pt-br",
                    edition_ledger_sha256="f" * 64,
                    edition_outputs={"chapter-01": fluid_output},
                ),
            )
            self.assertEqual(1, len(semantic_block_groups(blocks)))
            markup = "\n".join(
                semantic_body_parts(
                    blocks,
                    root,
                    [],
                )
            )
            self.assertEqual(1, markup.count("<p>"))
            self.assertIn("<p>Fluido um. Fluido dois.</p>", markup)
            self.assertEqual(
                "Neville-O’Neill",
                join_semantic_values(["Neville-", "O’Neill"]),
            )

            invalid = json.loads(json.dumps(layout))
            invalid["documents"][0]["blocks"][0]["join_with_previous"] = True
            errors = validate_layout(
                invalid,
                root,
                fixture.book_map_sha256,
                fixture.source_ledger_sha256,
                fixture.source_ledger,
                ["chapter-01"],
                text_edition="fluid-pt-br",
                edition_ledger_sha256="f" * 64,
                edition_outputs={"chapter-01": fluid_output},
            )
            self.assertTrue(
                any("requires a preceding paragraph block" in error for error in errors),
                errors,
            )

            note_separated_text = (
                "Começo* meio1 fim2\n\n"
                "* Nota estrela.\n\n"
                "1 Nota um.\n\n"
                "2 Nota dois.\n\n"
                "continuação concluída.\n"
            )
            fluid_path = root / "text" / fluid_output["fluid_file"]
            write_text(fluid_path, note_separated_text)
            fluid_output["fluid_sha256"] = sha256_file(fluid_path)
            note_separated_blocks = [
                {
                    "kind": "paragraph",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 1,
                },
                {
                    "kind": "note",
                    "id": "note-star",
                    "marker": "*",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 2,
                },
                {
                    "kind": "note",
                    "id": "note-1",
                    "marker": "1",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 3,
                },
                {
                    "kind": "note",
                    "id": "note-2",
                    "marker": "2",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 4,
                },
                {
                    "kind": "paragraph",
                    "text_file": fluid_file,
                    "text_sha256": fluid_output["fluid_sha256"],
                    "block_index": 5,
                    "join_with_previous": True,
                },
            ]
            note_separated_layout = {
                **layout,
                "documents": [
                    {"id": "chapter-01", "blocks": note_separated_blocks}
                ],
            }
            self.assertEqual(
                [],
                validate_layout(
                    note_separated_layout,
                    root,
                    fixture.book_map_sha256,
                    fixture.source_ledger_sha256,
                    fixture.source_ledger,
                    ["chapter-01"],
                    text_edition="fluid-pt-br",
                    edition_ledger_sha256="f" * 64,
                    edition_outputs={"chapter-01": fluid_output},
                ),
            )
            groups = semantic_block_groups(note_separated_blocks)
            self.assertEqual([0, 1, 2, 3], [index for index, _ in groups])
            self.assertEqual([2, 1, 1, 1], [len(group) for _, group in groups])
            self.assertEqual(
                [1, 5],
                [block["block_index"] for block in groups[0][1]],
            )
            note_markup = "\n".join(
                semantic_body_parts(
                    note_separated_blocks,
                    root,
                    [],
                )
            )
            paragraph_end = note_markup.index("</p>")
            self.assertLess(
                note_markup.index("continuação concluída"),
                paragraph_end,
            )
            for note_text in ("Nota estrela.", "Nota um.", "Nota dois."):
                self.assertGreater(note_markup.index(note_text), paragraph_end)
            self.assertEqual(3, note_markup.count('epub:type="noteref"'))
            self.assertEqual(3, note_markup.count('epub:type="footnote"'))
            for note_id in ("note-star", "note-1", "note-2"):
                self.assertIn(f'href="#noteref-{note_id}"', note_markup)

    def test_narration_plan_accepts_fluid_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = Fixture(root)
            metadata = root / "metadata"
            metadata.mkdir()
            renamed_fluid = (
                root
                / "text"
                / "fluid"
                / "pt-BR"
                / "chapters"
                / "renamed-fluid.txt"
            )
            write_text(renamed_fluid, fixture.fluid_chapter_text)
            fluid_ledger = json.loads(json.dumps(fixture.fluid_ledger))
            fluid_ledger["chapter_outputs"][0]["fluid_file"] = (
                renamed_fluid.relative_to(root / "text").as_posix()
            )
            fluid_ledger["chapter_outputs"][0]["fluid_sha256"] = sha256_file(
                renamed_fluid
            )
            write_json(
                metadata / "fluid-edition-ledger.json",
                fluid_ledger,
            )
            write_json(
                metadata / "epub-layout.fluid.json",
                {
                    "documents": [
                        {
                            "id": "chapter-01",
                            "blocks": [
                                {"kind": "paragraph", "block_index": 1},
                                {"kind": "paragraph", "block_index": 2},
                            ],
                        }
                    ]
                },
            )
            locutor_chapter = (
                root / "text" / "locutor" / "chapters" / "chapter-01-one.txt"
            )
            locutor_book = root / "text" / "locutor" / "book.txt"
            write_text(locutor_chapter, "Fluido um.\n\nFluido dois.\n")
            write_text(locutor_book, "Fluido um.\n\nFluido dois.\n")
            write_json(
                metadata / "narrator-changes.json",
                {
                    "base_edition": "fluid-pt-br",
                    "outputs": [
                        {
                            "id": "book",
                            "kind": "full-book",
                            "locutor_file": "locutor/book.txt",
                            "base_outputs": [
                                {
                                    "id": "chapter-01",
                                    "base_file": (
                                        "fluid/pt-BR/chapters/renamed-fluid.txt"
                                    ),
                                    "base_sha256": fluid_ledger[
                                        "chapter_outputs"
                                    ][0]["fluid_sha256"],
                                    "locutor_file": (
                                        "locutor/chapters/chapter-01-one.txt"
                                    ),
                                }
                            ],
                        }
                    ],
                },
            )
            records = _chapter_records(root, locutor_book)
            self.assertEqual(1, len(records))
            self.assertEqual(
                root
                / "text"
                / "fluid"
                / "pt-BR"
                / "chapters"
                / "renamed-fluid.txt",
                records[0]["source_path"],
            )

    def test_lineage_binds_explicit_and_fallback_chapter_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text_root = root / "text"
            aggregate = text_root / "locutor" / "book.txt"
            explicit = text_root / "locutor" / "chapters" / "custom.txt"
            fallback = (
                text_root / "locutor" / "chapters" / "base-chapter.txt"
            )
            approved = "Primeiro bloco.\n\nSegundo bloco.\n"
            write_text(aggregate, approved)
            write_text(explicit, approved)
            base_outputs = {
                "chapter-01": {
                    "fluid_file": "fluid/pt-BR/chapters/base-chapter.txt",
                    "fluid_sha256": "a" * 64,
                    "source_pages": [
                        {"logical_page": 1, "source_sha256": "b" * 64}
                    ],
                }
            }
            output = {
                "id": "book",
                "kind": "full-book",
                "locutor_file": "locutor/book.txt",
                "locutor_sha256": sha256_file(aggregate),
                "base_outputs": [
                    {
                        "id": "chapter-01",
                        "base_file": "fluid/pt-BR/chapters/base-chapter.txt",
                        "base_sha256": "a" * 64,
                        "locutor_file": "locutor/chapters/custom.txt",
                    }
                ],
                "reviewed_by": "audiobook-verifier",
            }
            errors, selected = _validate_outputs(
                {"outputs": [output]},
                base_outputs,
                text_root,
                aggregate.resolve(),
                set(),
            )
            self.assertEqual([], errors)
            self.assertIs(output, selected)

            write_text(explicit, "Primeiro bloco!\n\nSegundo bloco.\n")
            errors, _ = _validate_outputs(
                {"outputs": [output]},
                base_outputs,
                text_root,
                aggregate.resolve(),
                set(),
            )
            self.assertTrue(
                any("ordered normalized concatenation" in error for error in errors),
                errors,
            )

            output["base_outputs"][0].pop("locutor_file")
            write_text(fallback, approved)
            errors, _ = _validate_outputs(
                {"outputs": [output]},
                base_outputs,
                text_root,
                aggregate.resolve(),
                set(),
            )
            self.assertEqual([], errors)
            write_text(fallback, "Primeiro bloco?\n\nSegundo bloco.\n")
            errors, _ = _validate_outputs(
                {"outputs": [output]},
                base_outputs,
                text_root,
                aggregate.resolve(),
                set(),
            )
            self.assertTrue(
                any("ordered normalized concatenation" in error for error in errors),
                errors,
            )

    def test_publication_requires_current_fluid_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = Fixture(root)
            metadata = root / "metadata"
            metadata.mkdir()
            for name, value in (
                ("book-map.json", fixture.book_map),
                ("text-ledger.json", fixture.source_ledger),
                (
                    "assets-manifest.json",
                    {
                        "schema_version": "1.0",
                        "source_sha256": "0" * 64,
                        "assets": [],
                    },
                ),
                ("translation-ledger.json", fixture.translation_ledger),
                ("fluid-style.json", fixture.fluid_style),
                ("fluid-edition-ledger.json", fixture.fluid_ledger),
                ("epub-manifest.fluid.json", {"language": "pt-BR"}),
            ):
                write_json(metadata / name, value)
            fluid_layout_path = metadata / "epub-layout.fluid.json"
            write_json(fluid_layout_path, {"documents": []})
            fluid_layout = {
                "mode": "semantic",
                "path": "metadata/epub-layout.fluid.json",
                "sha256": sha256_file(fluid_layout_path),
            }
            write_json(
                metadata / "epub-manifest.fluid.json",
                {
                    "text_edition": "fluid-pt-br",
                    "language": "pt-BR",
                    "book_map_sha256": sha256_file(metadata / "book-map.json"),
                    "text_ledger_sha256": sha256_file(metadata / "text-ledger.json"),
                    "assets_manifest_sha256": sha256_file(metadata / "assets-manifest.json"),
                    "layout": fluid_layout,
                    "profile": "fluid-faithful-ptbr-v1",
                    "base_edition": fixture.fluid_ledger["base_edition"],
                    "base_ledger_sha256": fixture.fluid_ledger["base_ledger_sha256"],
                    "fluid_style_sha256": sha256_file(metadata / "fluid-style.json"),
                    "fluid_edition_ledger_sha256": sha256_file(metadata / "fluid-edition-ledger.json"),
                    "translation_ledger_sha256": sha256_file(metadata / "translation-ledger.json"),
                    "source_language": "English",
                },
            )
            sidecar = {
                "profile": "fluid-faithful-ptbr-v1",
                "base_edition": "translated-pt-br",
                "base_ledger_sha256": sha256_file(
                    metadata / "translation-ledger.json"
                ),
                "fluid_style_sha256": sha256_file(
                    metadata / "fluid-style.json"
                ),
                "fluid_edition_ledger_sha256": sha256_file(
                    metadata / "fluid-edition-ledger.json"
                ),
                "translation_ledger_sha256": sha256_file(
                    metadata / "translation-ledger.json"
                ),
                "source_language": "English",
            }
            validate_fluid_publication_sidecar(root, sidecar)
            invalid = dict(sidecar)
            invalid.pop("fluid_style_sha256")
            with self.assertRaisesRegex(
                RuntimeError,
                "fluid_style_sha256",
            ):
                validate_fluid_publication_sidecar(root, invalid)

    def test_fluid_publication_stays_beside_the_faithful_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            public_root = Path(temp) / "Livro - 2026 - Autor"
            assembly_root = public_root / "assembly"
            fixture = Fixture(assembly_root)
            metadata = assembly_root / "metadata"
            metadata.mkdir(parents=True)
            for name, value in (
                ("book-map.json", fixture.book_map),
                ("text-ledger.json", fixture.source_ledger),
                (
                    "assets-manifest.json",
                    {
                        "schema_version": "1.0",
                        "source_sha256": "0" * 64,
                        "assets": [],
                    },
                ),
                ("translation-ledger.json", fixture.translation_ledger),
                ("fluid-style.json", fixture.fluid_style),
                ("fluid-edition-ledger.json", fixture.fluid_ledger),
                ("epub-manifest.fluid.json", {"language": "pt-BR"}),
            ):
                write_json(metadata / name, value)
            fluid_layout_path = metadata / "epub-layout.fluid.json"
            write_json(fluid_layout_path, {"documents": []})
            fluid_layout = {
                "mode": "semantic",
                "path": "metadata/epub-layout.fluid.json",
                "sha256": sha256_file(fluid_layout_path),
            }
            write_json(
                metadata / "epub-manifest.fluid.json",
                {
                    "text_edition": "fluid-pt-br",
                    "language": "pt-BR",
                    "book_map_sha256": sha256_file(metadata / "book-map.json"),
                    "text_ledger_sha256": sha256_file(metadata / "text-ledger.json"),
                    "assets_manifest_sha256": sha256_file(metadata / "assets-manifest.json"),
                    "layout": fluid_layout,
                    "profile": "fluid-faithful-ptbr-v1",
                    "base_edition": fixture.fluid_ledger["base_edition"],
                    "base_ledger_sha256": fixture.fluid_ledger["base_ledger_sha256"],
                    "fluid_style_sha256": sha256_file(metadata / "fluid-style.json"),
                    "fluid_edition_ledger_sha256": sha256_file(metadata / "fluid-edition-ledger.json"),
                    "translation_ledger_sha256": sha256_file(metadata / "translation-ledger.json"),
                    "source_language": "English",
                },
            )

            faithful_epub = public_root / f"{public_root.name}.epub"
            faithful_pdf = public_root / f"{public_root.name}.pdf"
            faithful_mp3 = public_root / f"{public_root.name}.mp3"
            faithful_epub.write_bytes(b"faithful epub")
            faithful_pdf.write_bytes(b"faithful pdf")
            faithful_mp3.write_bytes(b"faithful mp3")

            def publication_record(path: Path, source_path: str) -> dict:
                digest = sha256_file(path)
                return {
                    "path": path.name,
                    "path_root": "book",
                    "sha256": digest,
                    "source_path": source_path,
                    "source_path_root": "assembly",
                    "source_sha256": digest,
                    "published_at": "2026-07-18T00:00:00+00:00",
                }

            faithful_epub_record = publication_record(
                faithful_epub,
                "exports/epub/livro-pt-br.epub",
            )
            faithful_pdf_record = publication_record(
                faithful_pdf,
                "exports/pdf/livro-pt-br.pdf",
            )
            write_json(
                metadata / "publication-manifest.json",
                {
                    "schema_version": "1.1",
                    "artifacts": {
                        "audio": publication_record(
                            faithful_mp3,
                            "audio/feminina-v1/audiobook.mp3",
                        ),
                        "epub": faithful_epub_record,
                        "epub_editions": {
                            "translated-pt-br:original": faithful_epub_record,
                        },
                        "pdf": faithful_pdf_record,
                        "pdf_editions": {
                            "translated-pt-br:original": faithful_pdf_record,
                        },
                    },
                },
            )

            fluid_epub = (
                assembly_root
                / "exports"
                / "epub"
                / "livro-fluida.epub"
            )
            fluid_pdf = (
                assembly_root
                / "exports"
                / "pdf"
                / "livro-fluida.pdf"
            )
            fluid_epub.parent.mkdir(parents=True)
            fluid_pdf.parent.mkdir(parents=True)
            fluid_epub.write_bytes(b"fluid epub")
            fluid_pdf.write_bytes(b"fluid pdf")
            lineage = {
                "text_edition": "fluid-pt-br",
                "image_edition": "original",
                "language": "pt-BR",
                "book_map_sha256": sha256_file(metadata / "book-map.json"),
                "text_ledger_sha256": sha256_file(
                    metadata / "text-ledger.json"
                ),
                "assets_manifest_sha256": sha256_file(
                    metadata / "assets-manifest.json"
                ),
                "layout": fluid_layout,
                "profile": "fluid-faithful-ptbr-v1",
                "base_edition": "translated-pt-br",
                "base_ledger_sha256": sha256_file(
                    metadata / "translation-ledger.json"
                ),
                "fluid_style_sha256": sha256_file(
                    metadata / "fluid-style.json"
                ),
                "fluid_edition_ledger_sha256": sha256_file(
                    metadata / "fluid-edition-ledger.json"
                ),
                "translation_ledger_sha256": sha256_file(
                    metadata / "translation-ledger.json"
                ),
                "source_language": "English",
            }
            write_json(
                fluid_epub.with_suffix(".epub.json"),
                {
                    **lineage,
                    "epub_path": fluid_epub.relative_to(
                        assembly_root
                    ).as_posix(),
                    "epub_sha256": sha256_file(fluid_epub),
                },
            )
            write_json(
                fluid_pdf.with_suffix(".pdf.json"),
                {
                    **lineage,
                    "pdf_path": fluid_pdf.relative_to(
                        assembly_root
                    ).as_posix(),
                    "pdf_sha256": sha256_file(fluid_pdf),
                },
            )

            def run_publish(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "publish_artifacts.py"),
                        "--book-root",
                        str(public_root),
                        *arguments,
                    ],
                    text=True,
                    capture_output=True,
                )

            only_epub = run_publish("--epub", str(fluid_epub))
            self.assertNotEqual(0, only_epub.returncode)
            self.assertIn(
                "requires one matching EPUB/PDF pair",
                only_epub.stderr,
            )
            self.assertFalse((public_root / fluid_epub.name).exists())

            pdf_sidecar_path = fluid_pdf.with_suffix(".pdf.json")
            pdf_sidecar = json.loads(
                pdf_sidecar_path.read_text(encoding="utf-8")
            )
            pdf_sidecar["image_edition"] = "approved-restored"
            write_json(pdf_sidecar_path, pdf_sidecar)
            mismatched_pair = run_publish(
                "--epub",
                str(fluid_epub),
                "--pdf",
                str(fluid_pdf),
            )
            self.assertNotEqual(0, mismatched_pair.returncode)
            self.assertIn(
                "requires one matching EPUB/PDF pair",
                mismatched_pair.stderr,
            )
            pdf_sidecar["image_edition"] = "original"
            write_json(pdf_sidecar_path, pdf_sidecar)

            completed = run_publish(
                "--epub",
                str(fluid_epub),
                "--pdf",
                str(fluid_pdf),
            )
            self.assertEqual(
                0,
                completed.returncode,
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            self.assertEqual(b"faithful epub", faithful_epub.read_bytes())
            self.assertEqual(b"faithful pdf", faithful_pdf.read_bytes())
            self.assertEqual(
                fluid_epub.read_bytes(),
                (public_root / fluid_epub.name).read_bytes(),
            )
            self.assertEqual(
                fluid_pdf.read_bytes(),
                (public_root / fluid_pdf.name).read_bytes(),
            )
            publication_manifest = json.loads(
                (metadata / "publication-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            artifacts = publication_manifest["artifacts"]
            self.assertEqual(faithful_epub.name, artifacts["epub"]["path"])
            self.assertEqual(faithful_pdf.name, artifacts["pdf"]["path"])
            self.assertEqual(
                fluid_epub.name,
                artifacts["epub_editions"]["fluid-pt-br:original"]["path"],
            )
            self.assertEqual(
                fluid_pdf.name,
                artifacts["pdf_editions"]["fluid-pt-br:original"]["path"],
            )

            manifest_before_rename = (
                metadata / "publication-manifest.json"
            ).read_bytes()
            renamed_epub = fluid_epub.with_name(
                "livro-fluida-renomeada.epub"
            )
            renamed_pdf = fluid_pdf.with_name(
                "livro-fluida-renomeada.pdf"
            )
            renamed_epub.write_bytes(fluid_epub.read_bytes())
            renamed_pdf.write_bytes(fluid_pdf.read_bytes())
            renamed_epub_sidecar = json.loads(
                fluid_epub.with_suffix(".epub.json").read_text(
                    encoding="utf-8"
                )
            )
            renamed_epub_sidecar["epub_path"] = renamed_epub.relative_to(
                assembly_root
            ).as_posix()
            renamed_epub_sidecar["epub_sha256"] = sha256_file(renamed_epub)
            write_json(
                renamed_epub.with_suffix(".epub.json"),
                renamed_epub_sidecar,
            )
            renamed_pdf_sidecar = json.loads(
                fluid_pdf.with_suffix(".pdf.json").read_text(
                    encoding="utf-8"
                )
            )
            renamed_pdf_sidecar["pdf_path"] = renamed_pdf.relative_to(
                assembly_root
            ).as_posix()
            renamed_pdf_sidecar["pdf_sha256"] = sha256_file(renamed_pdf)
            write_json(
                renamed_pdf.with_suffix(".pdf.json"),
                renamed_pdf_sidecar,
            )
            renamed = run_publish(
                "--epub",
                str(renamed_epub),
                "--pdf",
                str(renamed_pdf),
            )
            self.assertNotEqual(0, renamed.returncode)
            self.assertIn(
                "Refusing to rename an existing EPUB publication",
                renamed.stderr,
            )
            self.assertFalse((public_root / renamed_epub.name).exists())
            self.assertFalse((public_root / renamed_pdf.name).exists())
            self.assertEqual(
                manifest_before_rename,
                (metadata / "publication-manifest.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
