from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import zipfile

import pypdf

import validate_epub_export as epub_validator
import validate_pdf_export as pdf_validator


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_minimal_epub_fixture(book_root: Path) -> tuple[Path, Path, list[dict]]:
    epub_path = book_root / "exports" / "epub" / "book.epub"
    epub_path.parent.mkdir(parents=True)
    text_path = book_root / "text" / "source" / "chapters" / "chapter-01.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text("Título\n\nParágrafo validado.", encoding="utf-8")
    document_path = "OEBPS/text/001-doc.xhtml"
    with zipfile.ZipFile(epub_path, "w") as archive:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
            <package version="3.0" xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:language>pt-BR</dc:language>
              </metadata>
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="doc-1" href="text/001-doc.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine>
                <itemref idref="doc-1"/>
              </spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol>
            <li><a href="text/001-doc.xhtml">Título</a></li>
            </ol></nav></body></html>""",
        )
        archive.writestr(
            document_path,
            """<html xmlns="http://www.w3.org/1999/xhtml"><body>
            <section><h1>Título</h1><p>Parágrafo validado.</p></section>
            </body></html>""",
        )
    (epub_path.with_suffix(".epub.json")).write_text(
        json.dumps(
            {
                "epub_path": epub_path.resolve().relative_to(book_root.resolve()).as_posix(),
                "epub_sha256": sha256_file(epub_path),
                "image_edition": "original",
                "text_edition": "original",
                "language": "pt-BR",
                "assets": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    documents = [
        {
            "id": "doc",
            "title": "Título",
            "kind": "chapter",
            "_text_path": text_path,
            "asset_ids": [],
        }
    ]
    return epub_path, text_path, documents


def test_epub_validation_reuses_one_zip_and_cached_entries() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        epub_path, _text_path, documents = write_minimal_epub_fixture(book_root)
        original_zipfile = epub_validator.zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            opened = 0
            reads: dict[str, int] = {}

            def __init__(self, *args: object, **kwargs: object) -> None:
                type(self).opened += 1
                super().__init__(*args, **kwargs)

            def read(self, name: str, *args: object, **kwargs: object) -> bytes:
                type(self).reads[name] = type(self).reads.get(name, 0) + 1
                return super().read(name, *args, **kwargs)

        epub_validator.zipfile.ZipFile = CountingZipFile
        try:
            with epub_validator.EpubArchiveCache(epub_path) as archive:
                errors = epub_validator.validate_epub_archive(
                    epub_path,
                    None,
                    "pt-BR",
                    False,
                    ["doc-1"],
                    archive,
                )
                errors += epub_validator.validate_epub_document_texts(
                    epub_path,
                    book_root,
                    documents,
                    archive,
                )
                errors += epub_validator.validate_export_sidecar(
                    book_root,
                    epub_path,
                    "original",
                    "original",
                    {"language": "pt-BR"},
                    None,
                    archive,
                )
        finally:
            epub_validator.zipfile.ZipFile = original_zipfile

        assert errors == []
        assert CountingZipFile.opened == 1
        assert CountingZipFile.reads["OEBPS/text/001-doc.xhtml"] == 1


def test_epub_document_text_regression_still_reports_mismatch() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        epub_path, text_path, documents = write_minimal_epub_fixture(book_root)
        text_path.write_text("Título\n\nTexto divergente.", encoding="utf-8")

        with epub_validator.EpubArchiveCache(epub_path) as archive:
            errors = epub_validator.validate_epub_document_texts(
                epub_path,
                book_root,
                documents,
                archive,
            )

        assert errors == [
            "EPUB document text does not match its validated input: "
            "OEBPS/text/001-doc.xhtml"
        ]


class FakePage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.extract_calls = 0

    def extract_text(self) -> str:
        self.extract_calls += 1
        return self.text


class FakePdfReader:
    def __init__(self) -> None:
        self.pages = [
            FakePage("Capítulo Um\nPrimeira página preservada."),
            FakePage("Segunda página preservada."),
        ]
        self.metadata = SimpleNamespace(title="PDF de Teste")
        self.outline = [SimpleNamespace(title="Capítulo Um")]

    def get_destination_page_number(self, _outline_item: object) -> int:
        return 0


def test_pdf_main_reuses_one_reader_and_one_text_extraction_per_page() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        pdf_path = book_root / "exports" / "pdf" / "book.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF fake")
        text_path = book_root / "text" / "source" / "chapters" / "chapter-01.txt"
        text_path.parent.mkdir(parents=True)
        text_path.write_text(
            "Capítulo Um\n\nPrimeira página preservada.\n\nSegunda página preservada.",
            encoding="utf-8",
        )
        document = {
            "id": "chapter-001",
            "title": "Capítulo Um",
            "kind": "chapter",
            "_text_path": text_path,
            "asset_ids": [],
        }
        reader = FakePdfReader()
        reader_calls: list[str] = []
        original_reader = pypdf.PdfReader
        original_argv = sys.argv
        original_resolve = pdf_validator.resolve_book_paths
        original_load = pdf_validator.load_export_context
        original_validate_documents = pdf_validator.validate_documents
        original_validate_sidecar = pdf_validator.validate_sidecar

        def fake_reader(path: str) -> FakePdfReader:
            reader_calls.append(path)
            return reader

        pypdf.PdfReader = fake_reader
        sys.argv = [
            "validate_pdf_export.py",
            "--book-root",
            str(book_root),
            "--pdf",
            str(pdf_path),
        ]
        pdf_validator.resolve_book_paths = lambda path: SimpleNamespace(assembly_root=path)
        pdf_validator.load_export_context = lambda *args: (
            {},
            {},
            {"assets": []},
            {"book": {"title": "PDF de Teste"}, "language": "pt-BR"},
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        pdf_validator.validate_documents = lambda *args: ([document], {})
        pdf_validator.validate_sidecar = lambda *args: []
        try:
            pdf_validator.main()
        finally:
            pypdf.PdfReader = original_reader
            sys.argv = original_argv
            pdf_validator.resolve_book_paths = original_resolve
            pdf_validator.load_export_context = original_load
            pdf_validator.validate_documents = original_validate_documents
            pdf_validator.validate_sidecar = original_validate_sidecar

        assert len(reader_calls) == 1
        assert [page.extract_calls for page in reader.pages] == [1, 1]


def test_pdf_text_regression_still_reports_missing_fragment() -> None:
    reader = SimpleNamespace(pages=[FakePage("Texto presente.")])
    context = pdf_validator.PdfValidationContext(reader)

    errors = pdf_validator.validate_pdf_text(
        Path("unused.pdf"),
        ["Trecho ausente"],
        [],
        context,
    )

    assert errors == [
        "PDF text does not preserve a validated semantic fragment: Trecho ausente"
    ]
    assert reader.pages[0].extract_calls == 1


def run_tests() -> None:
    test_epub_validation_reuses_one_zip_and_cached_entries()
    test_epub_document_text_regression_still_reports_mismatch()
    test_pdf_main_reuses_one_reader_and_one_text_extraction_per_page()
    test_pdf_text_regression_still_reports_missing_fragment()


if __name__ == "__main__":
    run_tests()
