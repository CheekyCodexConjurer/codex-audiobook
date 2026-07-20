from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

from epub_layout import validate_layout
from test_tools import sha256_file, translation_ledger_for, write_epub, write_json


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def run_fails(*args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        raise AssertionError(f"Command unexpectedly succeeded: {' '.join(args)}")
    return completed


def prepare_translated_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    source_epub = root / "source.epub"
    library_root = root / "library"
    public_root = library_root / "Livro Traduzido - 2024 - Autora Teste"
    assembly_root = public_root / "assembly"
    write_epub(source_epub)
    run(
        str(ROOT / "preflight.py"),
        "--source",
        str(source_epub),
        "--library-root",
        str(library_root),
        "--title",
        "Livro Traduzido",
        "--publication-year",
        "2024",
        "--author",
        "Autora Teste",
    )

    map_path = assembly_root / "metadata" / "book-map.json"
    book_map = json.loads(map_path.read_text(encoding="utf-8"))
    book_map["analysis"]["status"] = "approved"
    book_map["analysis"]["source_language"] = "en"
    for page in book_map["pages"]:
        page["status"] = "mapped"
        page["blank"] = False
        page["chapter_id"] = "chapter-001"
    book_map["chapters"] = [
        {
            "id": "chapter-001",
            "number": 1,
            "title": "Source Chapter",
            "start_logical_page": 1,
            "end_logical_page": 2,
        }
    ]
    book_map["book"] = {
        "title": "Livro Traduzido",
        "author": "Autora Teste",
        "original_publication_year": 2024,
    }
    write_json(map_path, book_map)
    write_json(
        assembly_root / "metadata" / "assets-manifest.json",
        {
            "schema_version": "1.0",
            "source_sha256": book_map["source"]["sha256"],
            "assets": [],
        },
    )

    text_root = assembly_root / "text"
    page_records = []
    for logical_page, text in {
        1: "I\nSOURCE CHAPTER\n\nSource paragraph one.",
        2: "Source paragraph two.",
    }.items():
        page_path = text_root / "source" / "pages" / f"page-{logical_page:04d}.txt"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(text, encoding="utf-8")
        page_records.append(
            {
                "logical_page": logical_page,
                "status": "verified",
                "source_file": f"source/pages/page-{logical_page:04d}.txt",
                "source_sha256": sha256_file(page_path),
                "transcribed_by": "codex",
                "verified_by": "codex",
                "notes": "",
            }
        )
    chapter_path = text_root / "source" / "chapters" / "chapter-01-source.txt"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(
        "SOURCE CHAPTER\n\nSource paragraph one.\n\nSource paragraph two.",
        encoding="utf-8",
    )
    ledger_path = assembly_root / "metadata" / "text-ledger.json"
    source_ledger = {
        "schema_version": "1.0",
        "book_map_sha256": sha256_file(map_path),
        "pages": page_records,
        "chapter_outputs": [
            {
                "id": "chapter-001",
                "source_file": "source/chapters/chapter-01-source.txt",
                "source_sha256": sha256_file(chapter_path),
                "source_pages": [
                    {
                        "logical_page": record["logical_page"],
                        "source_sha256": record["source_sha256"],
                    }
                    for record in page_records
                ],
                "verified_by": "codex",
            }
        ],
    }
    write_json(ledger_path, source_ledger)
    run(
        str(ROOT / "verify_text_ledger.py"),
        "--book-map",
        str(map_path),
        "--ledger",
        str(ledger_path),
        "--text-root",
        str(text_root),
    )

    translation_ledger_path = assembly_root / "metadata" / "translation-ledger.json"
    translation_ledger = translation_ledger_for(
        map_path,
        ledger_path,
        source_ledger,
        text_root,
        "en",
        "Livro Traduzido",
        {"chapter-001": "Capítulo Fonte"},
    )
    translated_chapter = text_root / translation_ledger["chapter_outputs"][0]["translation_file"]
    translated_chapter.write_text(
        "Capítulo Fonte\n\nParágrafo traduzido um.\n\nParágrafo traduzido dois.",
        encoding="utf-8",
    )
    translation_ledger["chapter_outputs"][0]["translation_sha256"] = sha256_file(
        translated_chapter
    )
    write_json(translation_ledger_path, translation_ledger)
    run(
        str(ROOT / "verify_translation_ledger.py"),
        "--book-map",
        str(map_path),
        "--ledger",
        str(ledger_path),
        "--translation-ledger",
        str(translation_ledger_path),
        "--text-root",
        str(text_root),
    )
    return public_root, assembly_root, map_path, ledger_path


def translated_layout(assembly_root: Path, map_path: Path, ledger_path: Path) -> dict:
    translation_ledger_path = assembly_root / "metadata" / "translation-ledger.json"
    translation_ledger = json.loads(translation_ledger_path.read_text(encoding="utf-8"))
    output = translation_ledger["chapter_outputs"][0]
    text_file = f"text/{output['translation_file']}"
    return {
        "schema_version": "1.0",
        "text_edition": "translated-pt-br",
        "book_map_sha256": sha256_file(map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "translation_ledger_sha256": sha256_file(translation_ledger_path),
        "documents": [
            {
                "id": output["id"],
                "blocks": [
                    {
                        "kind": "heading",
                        "level": 1,
                        "text_file": text_file,
                        "text_sha256": output["translation_sha256"],
                        "block_index": 1,
                    },
                    {
                        "kind": "paragraph",
                        "text_file": text_file,
                        "text_sha256": output["translation_sha256"],
                        "block_index": 2,
                    },
                    {
                        "kind": "paragraph",
                        "text_file": text_file,
                        "text_sha256": output["translation_sha256"],
                        "block_index": 3,
                    },
                ],
            }
        ],
    }


def test_edition_layout_confines_blocks_to_their_document() -> None:
    cases = [
        {
            "text_edition": "translated-pt-br",
            "subtree": "translation",
            "file_key": "translation_file",
            "sha_key": "translation_sha256",
            "ledger_key": "translation_ledger_sha256",
            "ledger_sha": "translation-ledger-sha",
            "label": "translated",
        },
        {
            "text_edition": "fluid-pt-br",
            "subtree": "fluid",
            "file_key": "fluid_file",
            "sha_key": "fluid_sha256",
            "ledger_key": "fluid_edition_ledger_sha256",
            "ledger_sha": "fluid-ledger-sha",
            "label": "fluid",
        },
    ]
    for case in cases:
        with tempfile.TemporaryDirectory(
            prefix=f"audiobook-{case['subtree']}-layout-"
        ) as raw:
            book_root = Path(raw) / "book"
            chapter_root = (
                book_root / "text" / case["subtree"] / "pt-BR" / "chapters"
            )
            chapter_root.mkdir(parents=True)
            chapter_paths = {
                "chapter-a": chapter_root / "chapter-a.txt",
                "chapter-b": chapter_root / "chapter-b.txt",
            }
            for document_id, chapter_path in chapter_paths.items():
                chapter_path.write_text(
                    f"{document_id} bloco 1\n\n{document_id} bloco 2",
                    encoding="utf-8",
                )

            outputs = {
                document_id: {
                    case["file_key"]: (
                        f"{case['subtree']}/pt-BR/chapters/{chapter_path.name}"
                    ),
                    case["sha_key"]: sha256_file(chapter_path),
                }
                for document_id, chapter_path in chapter_paths.items()
            }

            def block(document_id: str, block_index: int) -> dict:
                output = outputs[document_id]
                return {
                    "kind": "paragraph",
                    "text_file": f"text/{output[case['file_key']]}",
                    "text_sha256": output[case["sha_key"]],
                    "block_index": block_index,
                }

            chapter_a_blocks = [block("chapter-a", 1), block("chapter-a", 2)]
            if case["text_edition"] == "fluid-pt-br":
                chapter_a_blocks[1]["join_with_previous"] = True
            layout = {
                "schema_version": "1.0",
                "text_edition": case["text_edition"],
                "book_map_sha256": "map-sha",
                "text_ledger_sha256": "text-ledger-sha",
                case["ledger_key"]: case["ledger_sha"],
                "documents": [
                    {"id": "chapter-a", "blocks": chapter_a_blocks},
                    {
                        "id": "chapter-b",
                        "blocks": [block("chapter-b", 1), block("chapter-b", 2)],
                    },
                ],
            }
            validation_args = (
                book_root,
                "map-sha",
                "text-ledger-sha",
                {},
                ["chapter-a", "chapter-b"],
            )
            validation_kwargs = {
                "text_edition": case["text_edition"],
                "edition_ledger_sha256": case["ledger_sha"],
                "edition_outputs": outputs,
            }
            assert (
                validate_layout(layout, *validation_args, **validation_kwargs) == []
            )

            misplaced_layout = json.loads(json.dumps(layout))
            misplaced_block = misplaced_layout["documents"][1]["blocks"].pop(0)
            misplaced_layout["documents"][0]["blocks"].append(misplaced_block)
            errors = validate_layout(
                misplaced_layout,
                *validation_args,
                **validation_kwargs,
            )
            assert any(
                f"text_file must match the verified {case['label']} chapter "
                "for document chapter-a" in error
                for error in errors
            ), errors

            if case["text_edition"] == "translated-pt-br":
                non_fluid_layout = json.loads(json.dumps(layout))
                non_fluid_layout["documents"][0]["blocks"][1][
                    "join_with_previous"
                ] = True
                errors = validate_layout(
                    non_fluid_layout,
                    *validation_args,
                    **validation_kwargs,
                )
                assert any(
                    "join_with_previous is only allowed for fluid layouts" in error
                    for error in errors
                ), errors


def test_join_with_previous_is_rejected_for_source_layouts() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-source-layout-") as raw:
        book_root = Path(raw) / "book"
        page_path = book_root / "text" / "source" / "pages" / "page-0001.txt"
        page_path.parent.mkdir(parents=True)
        page_path.write_text("Linha um.\nLinha dois.", encoding="utf-8")
        page_hash = sha256_file(page_path)
        ledger = {
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": page_hash,
                }
            ]
        }
        blocks = [
            {
                "kind": "paragraph",
                "spans": [
                    {
                        "source_file": "text/source/pages/page-0001.txt",
                        "source_sha256": page_hash,
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
            },
            {
                "kind": "paragraph",
                "join_with_previous": True,
                "spans": [
                    {
                        "source_file": "text/source/pages/page-0001.txt",
                        "source_sha256": page_hash,
                        "start_line": 2,
                        "end_line": 2,
                    }
                ],
            },
        ]
        for text_edition in ("original", "revised-pt-br"):
            layout = {
                "schema_version": "1.0",
                "text_edition": text_edition,
                "book_map_sha256": "map-sha",
                "text_ledger_sha256": "text-ledger-sha",
                "documents": [{"id": "chapter-a", "blocks": blocks}],
            }
            errors = validate_layout(
                layout,
                book_root,
                "map-sha",
                "text-ledger-sha",
                ledger,
                ["chapter-a"],
                text_edition=text_edition,
            )
            assert any(
                "join_with_previous is only allowed for fluid layouts" in error
                for error in errors
            ), errors


def test_translated_reader_end_to_end() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-translated-reader-") as raw:
        public_root, assembly_root, map_path, ledger_path = prepare_translated_fixture(
            Path(raw)
        )
        text_root = assembly_root / "text"
        assets_manifest = assembly_root / "metadata" / "assets-manifest.json"
        translated_manifest = assembly_root / "metadata" / "epub-manifest.pt-br.json"
        canonical_layout = assembly_root / "metadata" / "epub-layout.pt-br.json"

        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--assets-manifest",
            str(assets_manifest),
            "--text-root",
            str(text_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(translated_manifest),
        )

        write_json(canonical_layout, translated_layout(assembly_root, map_path, ledger_path))
        run(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(public_root),
            "--text-edition",
            "translated-pt-br",
        )
        legacy = run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--assets-manifest",
            str(assets_manifest),
            "--text-root",
            str(text_root),
            "--text-edition",
            "translated-pt-br",
            "--layout",
            "legacy",
            "--output",
            str(translated_manifest),
        )
        assert "require a semantic EPUB layout" in legacy.stderr

        wrong_layout = assembly_root / "metadata" / "other-layout.json"
        write_json(wrong_layout, translated_layout(assembly_root, map_path, ledger_path))
        wrong_path = run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--assets-manifest",
            str(assets_manifest),
            "--text-root",
            str(text_root),
            "--text-edition",
            "translated-pt-br",
            "--epub-layout",
            str(wrong_layout),
            "--output",
            str(translated_manifest),
        )
        assert "EPUB layout must use the canonical path" in wrong_path.stderr

        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--assets-manifest",
            str(assets_manifest),
            "--text-root",
            str(text_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(translated_manifest),
        )
        manifest = json.loads(translated_manifest.read_text(encoding="utf-8"))
        assert manifest["layout"] == {
            "mode": "semantic",
            "path": "metadata/epub-layout.pt-br.json",
            "sha256": sha256_file(canonical_layout),
        }

        epub = assembly_root / "exports" / "epub" / "translated.epub"
        pdf = assembly_root / "exports" / "pdf" / "translated.pdf"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(public_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(public_root),
            "--epub",
            str(epub),
            "--text-edition",
            "translated-pt-br",
        )
        if importlib.util.find_spec("reportlab") is None:
            print(
                "SKIP translated PDF export/publish assertions: ReportLab is not installed"
            )
            return
        run(
            str(ROOT / "export_pdf.py"),
            "--book-root",
            str(public_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(pdf),
        )
        run(
            str(ROOT / "validate_pdf_export.py"),
            "--book-root",
            str(public_root),
            "--pdf",
            str(pdf),
            "--text-edition",
            "translated-pt-br",
        )
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(public_root),
            "--epub",
            str(epub),
            "--pdf",
            str(pdf),
        )
        publication_manifest = json.loads(
            (assembly_root / "metadata" / "publication-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert (public_root / f"{public_root.name}.epub").is_file()
        assert (public_root / f"{public_root.name}.pdf").is_file()
        assert (
            publication_manifest["artifacts"]["epub"]["text_edition"]
            == "translated-pt-br"
        )
        assert (
            publication_manifest["artifacts"]["pdf"]["text_edition"]
            == "translated-pt-br"
        )


def run_tests() -> None:
    tests = [
        test_edition_layout_confines_blocks_to_their_document,
        test_join_with_previous_is_rejected_for_source_layouts,
        test_translated_reader_end_to_end,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}", file=sys.stderr)
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    if failures:
        raise SystemExit(1)
    print(f"translated reader flow tests passed ({len(tests)} run)")


if __name__ == "__main__":
    run_tests()
