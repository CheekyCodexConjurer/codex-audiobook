from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from export_pdf import (
    DIALOGUE_FIRST_LINE_INDENT_MM,
    DIALOGUE_LEFT_INDENT_MM,
    FOOTNOTE_FONT_SIZE,
    FOOTNOTE_SEPARATOR_WIDTH_MM,
    _dialogue_paragraph_style,
    _footnote_paragraph_style,
    _require_reportlab,
    _verse_paragraph_style,
)


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
        env=env,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(args)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def run_fails(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
        env=env,
    )
    if completed.returncode == 0:
        raise AssertionError(
            f"Command unexpectedly succeeded: {' '.join(args)}\nstdout:\n{completed.stdout}"
        )
    return completed


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_source_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=360, height=540)
    with path.open("wb") as target:
        writer.write(target)


def span(page_sha256: str, start_line: int, end_line: int) -> dict:
    return {
        "source_file": "text/source/pages/page-0001.txt",
        "source_sha256": page_sha256,
        "start_line": start_line,
        "end_line": end_line,
    }


def build_semantic_pdf_fixture(book_root: Path) -> Path:
    source_pdf = book_root / "source" / "original.pdf"
    write_source_pdf(source_pdf)
    source_sha256 = sha256_file(source_pdf)

    metadata_root = book_root / "metadata"
    text_root = book_root / "text"
    page_path = text_root / "source" / "pages" / "page-0001.txt"
    chapter_path = text_root / "source" / "chapters" / "chapter-01-capitulo-de-teste.txt"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    page_text = "\n".join(
        [
            "CAPÍTULO DE TESTE",
            "Texto fiel preservado com acentos, orixás e coração para validação semântica do PDF.",
            "— Fala direta preservada no PDF sem achatamento indevido.",
            "Primeiro verso curto",
            "Segundo verso curto",
            "Referência anotada2",
            "2 Nota validada no PDF.",
            "Texto posterior à chamada continua acima do rodapé.",
        ]
    )
    page_path.write_text(page_text, encoding="utf-8")
    chapter_path.write_text(
        "CAPÍTULO DE TESTE\n\n"
        "Texto fiel preservado com acentos, orixás e coração para validação semântica do PDF.\n\n"
        "— Fala direta preservada no PDF sem achatamento indevido.\n\n"
        "Primeiro verso curto\nSegundo verso curto\n\n"
        "Referência anotada2\n2 Nota validada no PDF.\n"
        "Texto posterior à chamada continua acima do rodapé.\n",
        encoding="utf-8",
    )
    page_sha256 = sha256_file(page_path)
    chapter_sha256 = sha256_file(chapter_path)

    book_map_path = metadata_root / "book-map.json"
    book_map = {
        "schema_version": "1.0",
        "source": {
            "format": "pdf",
            "path": "source/original.pdf",
            "sha256": source_sha256,
            "page_count_logical": 1,
        },
        "analysis": {
            "status": "approved",
            "layout": "single",
            "rotation": "normal",
            "narration_language": "pt-BR",
            "source_language": "pt-BR",
        },
        "pages": [
            {
                "logical_page": 1,
                "side": "single",
                "source_page": 1,
                "status": "mapped",
                "blank": False,
                "chapter_id": "chapter-001",
                "evidence": ["fixture page mapped for PDF export validation"],
            }
        ],
        "page_number_alignment": {
            "segments": [
                {
                    "logical_start_page": 1,
                    "logical_end_page": 1,
                    "pdf_to_printed_page_offset": 0,
                    "evidence": ["single-page fixture"],
                }
            ]
        },
        "chapters": [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Capítulo de Teste",
                "start_logical_page": 1,
                "end_logical_page": 1,
            }
        ],
        "ranges": {"ignored": [], "narration_excluded": []},
        "book": {
            "title": "PDF de Teste",
            "subtitle": "Fixture Semântico",
            "author": "Codex",
            "original_publication_place": "São Paulo",
            "original_publication_year": 1933,
        },
    }
    write_json(book_map_path, book_map)

    ledger_path = metadata_root / "text-ledger.json"
    ledger = {
        "schema_version": "1.0",
        "book_map_sha256": sha256_file(book_map_path),
        "pages": [
            {
                "logical_page": 1,
                "status": "verified",
                "source_file": "source/pages/page-0001.txt",
                "source_sha256": page_sha256,
                "transcribed_by": "codex-test",
                "verified_by": "codex-test",
                "notes": "",
            }
        ],
        "chapter_outputs": [
            {
                "id": "chapter-001",
                "source_file": "source/chapters/chapter-01-capitulo-de-teste.txt",
                "source_sha256": chapter_sha256,
                "source_pages": [
                    {
                        "logical_page": 1,
                        "source_sha256": page_sha256,
                    }
                ],
                "verified_by": "codex-test",
            }
        ],
    }
    write_json(ledger_path, ledger)

    assets_manifest_path = metadata_root / "assets-manifest.json"
    write_json(
        assets_manifest_path,
        {
            "schema_version": "1.0",
            "source_sha256": source_sha256,
            "assets": [],
        },
    )

    layout_path = metadata_root / "epub-layout.json"
    layout = {
        "schema_version": "1.0",
        "text_edition": "original",
        "book_map_sha256": sha256_file(book_map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "documents": [
            {
                "id": "chapter-001",
                "blocks": [
                    {"kind": "heading", "level": 1, "spans": [span(page_sha256, 1, 1)]},
                    {"kind": "paragraph", "spans": [span(page_sha256, 2, 2)]},
                    {"kind": "dialogue", "spans": [span(page_sha256, 3, 3)]},
                    {"kind": "verse", "spans": [span(page_sha256, 4, 5)]},
                    {"kind": "paragraph", "spans": [span(page_sha256, 6, 6)]},
                    {
                        "kind": "note",
                        "id": "note-2",
                        "marker": "2",
                        "spans": [span(page_sha256, 7, 7)],
                    },
                    {"kind": "paragraph", "spans": [span(page_sha256, 8, 8)]},
                ],
            }
        ],
    }
    write_json(layout_path, layout)

    manifest_path = metadata_root / "epub-manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": "1.0",
            "book_map_sha256": sha256_file(book_map_path),
            "text_ledger_sha256": sha256_file(ledger_path),
            "assets_manifest_sha256": sha256_file(assets_manifest_path),
            "text_edition": "original",
            "language": "pt-BR",
            "book": {
                "title": "PDF de Teste",
                "subtitle": "Fixture Semântico",
                "author": "Codex",
                "publication_place": "São Paulo",
                "publication_year": 1933,
            },
            "layout": {
                "mode": "semantic",
                "path": "metadata/epub-layout.json",
                "sha256": sha256_file(layout_path),
            },
            "visual_profile": {
                "name": "antique-paper",
                "cover": {"mode": "editorial"},
            },
            "documents": [
                {
                    "id": "chapter-001",
                    "title": "Capítulo de Teste",
                    "source_file": "text/source/chapters/chapter-01-capitulo-de-teste.txt",
                    "source_sha256": chapter_sha256,
                    "asset_ids": [],
                }
            ],
        },
    )
    return manifest_path


def assert_points_close(actual: float, expected: float) -> None:
    assert abs(actual - expected) < 0.01


def pdf_text_observations(reader: PdfReader) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for page_index, page in enumerate(reader.pages):
        def visitor(
            text: str,
            current_matrix: list[float],
            text_matrix: list[float],
            font_dict: dict | None,
            font_size: float,
        ) -> None:
            value = " ".join(str(text).split())
            if not value:
                return
            font_name = ""
            if font_dict:
                font_name = str(font_dict.get("/BaseFont") or "")
            observations.append(
                {
                    "text": value,
                    "page": page_index,
                    "x": float(current_matrix[4]) + float(text_matrix[4]),
                    "y": float(current_matrix[5]) + float(text_matrix[5]),
                    "font_size": float(font_size),
                    "font": font_name,
                }
            )

        page.extract_text(visitor_text=visitor)
    return observations


def observation_containing(
    observations: list[dict[str, object]],
    needle: str,
) -> dict[str, object]:
    for observation in observations:
        if needle in str(observation["text"]):
            return observation
    raise AssertionError(f"PDF text observation not found: {needle}")


def outline_titles(items: list[object]) -> list[str]:
    titles: list[str] = []
    for item in items:
        if isinstance(item, list):
            titles.extend(outline_titles(item))
        else:
            title = getattr(item, "title", None)
            if title:
                titles.append(str(title))
    return titles


def test_pdf_dialogue_style_contract() -> None:
    rl = _require_reportlab()
    ParagraphStyle = rl["ParagraphStyle"]
    TA_RIGHT = rl["TA_RIGHT"]
    TA_JUSTIFY = rl["TA_JUSTIFY"]
    getSampleStyleSheet = rl["getSampleStyleSheet"]
    mm = rl["mm"]

    body = ParagraphStyle(
        "ProbeBody",
        parent=getSampleStyleSheet()["BodyText"],
        fontName="RegularProbe",
        alignment=TA_JUSTIFY,
        leftIndent=0,
        rightIndent=11 * mm,
        firstLineIndent=7 * mm,
    )
    dialogue = _dialogue_paragraph_style(
        ParagraphStyle,
        body,
        "ItalicProbe",
        mm,
        TA_RIGHT,
    )

    assert dialogue.fontName == "ItalicProbe"
    assert dialogue.alignment == TA_RIGHT
    assert dialogue.rightIndent == 0
    assert_points_close(dialogue.leftIndent, DIALOGUE_LEFT_INDENT_MM * mm)
    assert_points_close(
        dialogue.firstLineIndent,
        DIALOGUE_FIRST_LINE_INDENT_MM * mm,
    )


def test_pdf_verse_style_contract() -> None:
    rl = _require_reportlab()
    ParagraphStyle = rl["ParagraphStyle"]
    TA_CENTER = rl["TA_CENTER"]
    getSampleStyleSheet = rl["getSampleStyleSheet"]

    verse = _verse_paragraph_style(
        ParagraphStyle,
        getSampleStyleSheet()["BodyText"],
        TA_CENTER,
    )

    assert verse.alignment == TA_CENTER
    assert verse.leftIndent == 0
    assert verse.rightIndent == 0
    assert verse.firstLineIndent == 0


def test_pdf_footnote_style_contract() -> None:
    rl = _require_reportlab()
    ParagraphStyle = rl["ParagraphStyle"]
    TA_LEFT = rl["TA_LEFT"]
    getSampleStyleSheet = rl["getSampleStyleSheet"]

    note = _footnote_paragraph_style(
        ParagraphStyle,
        getSampleStyleSheet()["BodyText"],
        "RegularProbe",
        TA_LEFT,
    )

    assert note.fontName == "RegularProbe"
    assert note.fontSize == FOOTNOTE_FONT_SIZE
    assert note.alignment == TA_LEFT
    assert note.firstLineIndent == 0
    assert note.borderWidth == 0


def test_original_semantic_pdf_export_and_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pdf-export-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        pdf_path = book_root / "exports" / "pdf" / "original-semantic.pdf"

        export = run(
            str(ROOT / "export_pdf.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--output",
            str(pdf_path),
        )
        assert f"Created {pdf_path}" in export.stdout
        assert pdf_path.is_file()

        validate = run(
            str(ROOT / "validate_pdf_export.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--pdf",
            str(pdf_path),
        )
        assert "VALID PDF:" in validate.stdout

        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
        assert page_count > 0
        extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "CAPÍTULO DE TESTE" in extracted_text
        assert "Texto fiel preservado" in extracted_text
        assert "Primeiro verso curto" in extracted_text
        assert "Nota validada no PDF" in extracted_text
        assert "Texto posterior à chamada" in extracted_text
        assert str(reader.metadata.title) == "PDF de Teste"
        titles = outline_titles(reader.outline)
        assert "Sumário" in titles
        assert "CAPÍTULO DE TESTE" in titles
        observations = pdf_text_observations(reader)
        paragraph = observation_containing(observations, "Texto fiel preservado")
        dialogue = observation_containing(observations, "Fala direta preservada")
        note_reference = observation_containing(observations, "Referência anotada")
        footnote = observation_containing(observations, "Nota validada no PDF")
        following_text = observation_containing(
            observations,
            "Texto posterior à chamada",
        )
        assert "Italic" in str(dialogue["font"])
        assert float(dialogue["x"]) - float(paragraph["x"]) > 5 * _require_reportlab()["mm"]
        assert footnote["page"] == note_reference["page"]
        assert float(footnote["font_size"]) < float(paragraph["font_size"])
        assert float(footnote["y"]) < 30 * _require_reportlab()["mm"]
        assert float(note_reference["y"]) > float(footnote["y"])
        assert following_text["page"] == footnote["page"]
        assert float(following_text["y"]) > float(footnote["y"])
        footnote_page = reader.pages[int(footnote["page"])]
        content_stream = footnote_page.get_contents().get_data().decode("latin-1")
        horizontal_lines = re.findall(
            r"n\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+m\s+"
            r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+l\s+S",
            content_stream,
        )
        expected_separator_width = FOOTNOTE_SEPARATOR_WIDTH_MM * _require_reportlab()["mm"]
        assert any(
            abs(float(y1) - float(y2)) < 0.1
            and abs(abs(float(x2) - float(x1)) - expected_separator_width) < 0.2
            for x1, y1, x2, y2 in horizontal_lines
        )

        sidecar_path = pdf_path.with_suffix(".pdf.json")
        assert sidecar_path.is_file()
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["schema_version"] == "1.0"
        assert sidecar["pdf_path"] == "exports/pdf/original-semantic.pdf"
        assert sidecar["pdf_sha256"] == sha256_file(pdf_path)
        assert sidecar["page_count"] == page_count
        assert sidecar["text_edition"] == "original"
        assert sidecar["image_edition"] == "original"
        assert sidecar["layout"] == json.loads(manifest_path.read_text(encoding="utf-8"))["layout"]
        assert sidecar["assets"] == []
        assert sidecar["visual_profile"]["name"] == "antique-paper"
        assert sidecar["visual_profile"]["cover"]["format_label"] == "PDF"
        assert len(sidecar["visual_profile"]["cover"]["sha256"]) == 64

        tampered = copy.deepcopy(sidecar)
        tampered["page_count"] = page_count + 1
        write_json(sidecar_path, tampered)
        failure = run_fails(
            str(ROOT / "validate_pdf_export.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--pdf",
            str(pdf_path),
        )
        assert "PDF export sidecar page_count does not match" in failure.stderr

        write_json(sidecar_path, sidecar)
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(book_root),
            "--pdf",
            str(pdf_path),
        )
        published_pdf = book_root / f"{book_root.name}.pdf"
        assert published_pdf.read_bytes() == pdf_path.read_bytes()
        published_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert published_sidecar["publication"]["path"] == published_pdf.name
        assert published_sidecar["publication"]["path_root"] == "book"
        assert published_sidecar["publication"]["source_path_root"] == "assembly"
        publication_manifest = json.loads(
            (book_root / "metadata" / "publication-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert publication_manifest["schema_version"] == "1.1"
        assert publication_manifest["artifacts"]["pdf"]["path"] == published_pdf.name
        assert (
            publication_manifest["artifacts"]["pdf_editions"]["original:original"][
                "sha256"
            ]
            == sha256_file(pdf_path)
        )


def run_tests() -> None:
    test_pdf_dialogue_style_contract()
    test_pdf_verse_style_contract()
    test_pdf_footnote_style_contract()
    test_original_semantic_pdf_export_and_validation()


if __name__ == "__main__":
    run_tests()
