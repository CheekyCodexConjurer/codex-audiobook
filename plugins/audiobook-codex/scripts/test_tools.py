from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import zipfile


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> None:
    run_with_python(sys.executable, *args)


def run_with_python(python: str, *args: str) -> None:
    completed = subprocess.run([python, *args], text=True, capture_output=True)
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(args)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_fails(*args: str) -> None:
    completed = subprocess.run([sys.executable, *args], text=True, capture_output=True)
    if completed.returncode == 0:
        raise AssertionError(f"Command unexpectedly succeeded: {' '.join(args)}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_epub(path: Path) -> None:
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL4"
        "xAAAAABJRU5ErkJggg=="
    )
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    package = """<?xml version="1.0" encoding="utf-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="chapter-1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter-2" href="chapter-2.xhtml" media-type="application/xhtml+xml"/>
    <item id="image-illustration" href="images/a-illustration.png" media-type="image/png"/>
    <item id="image-cover" href="images/z-cover.png" media-type="image/png" properties="cover-image"/>
  </manifest>
  <spine><itemref idref="chapter-1"/><itemref idref="chapter-2"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr(
            "OEBPS/chapter-1.xhtml",
            '<html><body><p>Um.</p><img src="images/a-illustration.png" alt=""/></body></html>',
        )
        archive.writestr("OEBPS/chapter-2.xhtml", "<html><body><p>Dois.</p></body></html>")
        archive.writestr("OEBPS/images/a-illustration.png", image)
        archive.writestr("OEBPS/images/z-cover.png", image)


def write_pdf_with_image(path: Path, image_path: Path) -> None:
    from PIL import Image

    image = Image.new("RGB", (16, 12), color=(40, 80, 120))
    image.save(image_path, "JPEG")
    image.save(path, "PDF", resolution=72.0)


def create_junction(link: Path, target: Path) -> None:
    command = subprocess.list2cmdline(["mklink", "/J", str(link), str(target)])
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", command],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Could not create test junction {link}: {completed.stderr or completed.stdout}"
        )


def main() -> None:
    from audio_tools import SAMPLE_RATE, transcode, write_wav
    from path_safety import resolve_under
    from pypdf import PdfWriter
    from chatterbox_text import DEFAULT_MAX_CHARS, NarratorTextError, prepare_chatterbox_segments
    from render_chatterbox import (
        DEFAULT_MODEL_ROOT,
        DEFAULT_REFERENCE_VOICE,
        FEMININA_PROFILE,
        FEMININA_PROFILE_CALIBRATION,
        selected_profile,
    )
    from validate_book_map import validate_book_map

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    calibration_text = (
        "Na manhã de junho, a chuva fina cobria o jardim, enquanto a brisa movia "
        "lentamente as folhas. O relógio marcou oito e trinta. João abriu a janela "
        'e perguntou: "Quem deixou a pequena caixa azul junto à porta?" Após um breve '
        'silêncio, respirou devagar e disse: "Muito bem. Hoje começa uma nova história."'
    )
    calibration_segments = prepare_chatterbox_segments(calibration_text, DEFAULT_MAX_CHARS)
    assert len(calibration_text) == 302
    assert len(calibration_segments) == 1
    assert calibration_segments[0].text == calibration_text
    assert FEMININA_PROFILE["max_chars"] == DEFAULT_MAX_CHARS
    assert FEMININA_PROFILE["silence_seconds"] == 0.22
    assert FEMININA_PROFILE["min_p"] == 0.114
    assert FEMININA_PROFILE["seed"] == 20260713
    assert FEMININA_PROFILE_CALIBRATION["winner_id"] == "minp-0-114-temp-0-80"
    profile_args = SimpleNamespace(
        max_chars=FEMININA_PROFILE["max_chars"],
        silence_seconds=FEMININA_PROFILE["silence_seconds"],
        exaggeration=FEMININA_PROFILE["exaggeration"],
        cfg_weight=FEMININA_PROFILE["cfg_weight"],
        temperature=FEMININA_PROFILE["temperature"],
        repetition_penalty=FEMININA_PROFILE["repetition_penalty"],
        min_p=FEMININA_PROFILE["min_p"],
        top_p=FEMININA_PROFILE["top_p"],
        seed=FEMININA_PROFILE["seed"],
    )
    calibrated_model = FEMININA_PROFILE_CALIBRATION["model"]
    calibrated_version = FEMININA_PROFILE_CALIBRATION["chatterbox_tts_version"]
    assert (
        selected_profile(
            profile_args,
            DEFAULT_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            calibrated_model,
            calibrated_version,
        )
        == "feminina-v1"
    )
    profile_args.silence_seconds = 0.0
    assert (
        selected_profile(
            profile_args,
            DEFAULT_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            calibrated_model,
            calibrated_version,
        )
        == "custom"
    )
    profile_args.silence_seconds = FEMININA_PROFILE["silence_seconds"]
    assert (
        selected_profile(
            profile_args,
            DEFAULT_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cpu",
            calibrated_model,
            calibrated_version,
        )
        == "custom"
    )
    altered_model = dict(calibrated_model)
    altered_model["t3_sha256"] = "0" * 64
    assert (
        selected_profile(
            profile_args,
            DEFAULT_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            altered_model,
            calibrated_version,
        )
        == "custom"
    )
    line_segments = prepare_chatterbox_segments(
        "Primeira linha completa.\n\nSegunda linha completa.",
        DEFAULT_MAX_CHARS,
    )
    assert [(item.line_number, item.text) for item in line_segments] == [
        (1, "Primeira linha completa."),
        (3, "Segunda linha completa."),
    ]
    leading_line_segments = prepare_chatterbox_segments(
        "\n\nPrimeira linha completa.",
        DEFAULT_MAX_CHARS,
    )
    assert leading_line_segments[0].line_number == 3
    for invalid_text in (
        "[thoughtful] Texto.",
        "[thoughtful Texto.",
        "<emphasis>Texto</emphasis>.",
        "**Texto.**",
        "*Texto em itálico.*",
        "_Texto em itálico_.",
        "~~Texto riscado~~.",
        "`Texto em código`.",
        "# Título.",
        "> Citação.",
        "- Item de lista.",
        "+ Outro item.",
        "---",
        "A | B",
        "O relógio marcou 8 horas.",
        "Dr. João chegou.",
        "Visite https://example.com.",
        "Visite exemplo.com.",
        "Isso e etc.",
        "X" * (DEFAULT_MAX_CHARS + 1),
    ):
        try:
            prepare_chatterbox_segments(invalid_text, DEFAULT_MAX_CHARS)
        except NarratorTextError:
            pass
        else:
            raise AssertionError(f"Expected Chatterbox narrator text to fail: {invalid_text!r}")

    with tempfile.TemporaryDirectory(prefix="audiobook-codex-test-") as temporary:
        root = Path(temporary)
        pdf_path = root / "source.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=360, height=540)
        with pdf_path.open("wb") as target:
            writer.write(target)

        library_root = root / "library"
        book_root = library_root / "source"
        map_path = book_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(pdf_path),
            "--library-root",
            str(library_root),
            "--dpi",
            "72",
        )
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(map_path), "--check-files")
        initial_map = json.loads(map_path.read_text(encoding="utf-8"))
        assert initial_map["source"]["path"] == "source/original.pdf"
        assert initial_map["source"]["original_path"] == str(pdf_path.resolve())
        assert (book_root / "source" / "original.pdf").read_bytes() == pdf_path.read_bytes()

        same_name_dir = root / "different-source"
        same_name_dir.mkdir()
        same_name_pdf = same_name_dir / "source.pdf"
        second_writer = PdfWriter()
        second_writer.add_blank_page(width=360, height=540)
        second_writer.add_blank_page(width=360, height=540)
        with same_name_pdf.open("wb") as target:
            second_writer.write(target)
        collision_root = library_root / f"source-{sha256_file(same_name_pdf)[:8]}"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(same_name_pdf),
            "--library-root",
            str(library_root),
            "--dpi",
            "72",
        )
        assert (collision_root / "metadata" / "book-map.json").is_file()
        assert (collision_root / "source" / "original.pdf").read_bytes() == same_name_pdf.read_bytes()

        escaped_map = json.loads(map_path.read_text(encoding="utf-8"))
        escaped_map["pages"][0]["render_path"] = "../outside.png"
        escaped_map_path = book_root / "metadata" / "escaped-map.json"
        escaped_map_path.write_text(
            json.dumps(escaped_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(escaped_map_path),
            "--check-files",
        )
        escaped_source_map = json.loads(map_path.read_text(encoding="utf-8"))
        escaped_source_map["source"]["path"] = "../outside.pdf"
        escaped_source_map_path = book_root / "metadata" / "escaped-source-map.json"
        escaped_source_map_path.write_text(
            json.dumps(escaped_source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(escaped_source_map_path),
            "--check-files",
        )
        absolute_map = copy.deepcopy(escaped_map)
        absolute_map["pages"][0]["render_path"] = str(root / "outside.png")
        absolute_map_path = book_root / "metadata" / "absolute-map.json"
        absolute_map_path.write_text(
            json.dumps(absolute_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(absolute_map_path),
            "--check-files",
        )
        copied_source = book_root / "metadata" / "borrowed-source.pdf"
        copied_source.write_bytes((book_root / "source" / "original.pdf").read_bytes())
        inroot_source_map = json.loads(map_path.read_text(encoding="utf-8"))
        inroot_source_map["source"]["path"] = "metadata/borrowed-source.pdf"
        inroot_source_map_path = book_root / "metadata" / "inroot-source-map.json"
        inroot_source_map_path.write_text(
            json.dumps(inroot_source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(inroot_source_map_path),
            "--check-files",
        )
        invalid_source_format_map = copy.deepcopy(initial_map)
        invalid_source_format_map["source"]["format"] = []
        invalid_source_format_errors = validate_book_map(
            invalid_source_format_map,
            book_root,
            False,
            True,
        )
        assert "source.format must be pdf or epub" in invalid_source_format_errors
        junction_root = root / "junction-root"
        junction_target_root = root / "junction-targets"
        for index, subtree in enumerate(
            (
                Path("source"),
                Path("assets") / "images" / "original",
                Path("restoration") / "approved",
                Path("text") / "source" / "pages",
                Path("text") / "locutor",
            ),
            start=1,
        ):
            target = junction_target_root / f"target-{index}"
            target.mkdir(parents=True)
            (target / "probe.txt").write_text("redirected", encoding="utf-8")
            link = junction_root / subtree
            link.parent.mkdir(parents=True, exist_ok=True)
            create_junction(link, target)
            assert (
                resolve_under(
                    junction_root,
                    (subtree / "probe.txt").as_posix(),
                    (subtree,),
                )
                is None
            )

        spread_library = root / "spread-library"
        spread_root = spread_library / "spread-book"
        spread_map = spread_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(pdf_path),
            "--library-root",
            str(spread_library),
            "--book-id",
            "spread-book",
            "--layout",
            "spread",
            "--dpi",
            "72",
        )
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(spread_map), "--check-files")
        spread = json.loads(spread_map.read_text(encoding="utf-8"))
        assert spread["source"]["page_count_logical"] == 2
        assert [page["side"] for page in spread["pages"]] == ["left", "right"]

        epub_path = root / "source.epub"
        epub_library = root / "epub-library"
        epub_root = epub_library / "epub-book"
        write_epub(epub_path)
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(epub_path),
            "--library-root",
            str(epub_library),
            "--book-id",
            "epub-book",
        )
        epub_map = epub_root / "metadata" / "book-map.json"
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(epub_map))
        epub = json.loads(epub_map.read_text(encoding="utf-8"))
        assert epub["source"]["format"] == "epub"
        assert epub["source"]["page_count_logical"] == 2
        epub_assets_path = epub_root / "metadata" / "assets-manifest.json"
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(epub_assets_path),
            "--book-root",
            str(epub_root),
            "--book-map",
            str(epub_map),
            "--check-files",
        )
        epub_assets = json.loads(epub_assets_path.read_text(encoding="utf-8"))
        assert len(epub_assets["assets"]) == 2
        epub_asset_by_locator = {
            asset["source"]["source_locator"]: asset
            for asset in epub_assets["assets"]
        }
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["source"]["format"] == "epub"
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["epub"]["role"] == "unresolved"
        assert epub_asset_by_locator["OEBPS/images/z-cover.png"]["epub"]["role"] == "cover"
        assert epub_asset_by_locator["OEBPS/images/z-cover.png"]["classification"]["content"] == "cover"

        epub_export_map = json.loads(epub_map.read_text(encoding="utf-8"))
        epub_export_map["analysis"]["status"] = "approved"
        for page in epub_export_map["pages"]:
            page["status"] = "mapped"
            page["blank"] = False
            page["chapter_id"] = "chapter-001"
        epub_export_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "EPUB Source",
                "start_logical_page": 1,
                "end_logical_page": 2,
            }
        ]
        epub_export_map["book"] = {"title": "EPUB Source", "author": "Autor"}
        epub_map.write_text(
            json.dumps(epub_export_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        epub_text_root = epub_root / "text"
        epub_page_records = []
        for logical_page in (1, 2):
            epub_page = epub_text_root / "source" / "pages" / f"page-{logical_page:04d}.txt"
            epub_page.parent.mkdir(parents=True, exist_ok=True)
            epub_page.write_text(f"Pagina EPUB {logical_page}.", encoding="utf-8")
            epub_page_records.append(
                {
                    "logical_page": logical_page,
                    "status": "verified",
                    "source_file": f"source/pages/page-{logical_page:04d}.txt",
                    "source_sha256": sha256_file(epub_page),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            )
        epub_chapter = epub_text_root / "source" / "chapters" / "chapter-01-epub-source.txt"
        epub_chapter.parent.mkdir(parents=True, exist_ok=True)
        epub_chapter.write_text("EPUB SOURCE\n\nTexto da fonte EPUB.", encoding="utf-8")
        epub_ledger_path = epub_root / "metadata" / "text-ledger.json"
        epub_ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "book_map_sha256": sha256_file(epub_map),
                    "pages": epub_page_records,
                    "chapter_outputs": [
                        {
                            "id": "chapter-001",
                            "source_file": "source/chapters/chapter-01-epub-source.txt",
                            "source_sha256": sha256_file(epub_chapter),
                            "source_pages": [
                                {
                                    "logical_page": record["logical_page"],
                                    "source_sha256": record["source_sha256"],
                                }
                                for record in epub_page_records
                            ],
                            "verified_by": "codex",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--text-root",
            str(epub_text_root),
        )
        epub_export_manifest = epub_root / "metadata" / "epub-manifest-export.json"
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--assets-manifest",
            str(epub_assets_path),
            "--text-root",
            str(epub_text_root),
            "--output",
            str(epub_export_manifest),
        )
        epub_export_data = json.loads(epub_export_manifest.read_text(encoding="utf-8"))
        source_cover_document = epub_export_data["documents"][0]
        assert source_cover_document["kind"] == "source_cover"
        assert source_cover_document["source_file"] is None
        assert source_cover_document["asset_ids"] == [
            epub_asset_by_locator["OEBPS/images/z-cover.png"]["id"]
        ]
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["id"] not in source_cover_document["asset_ids"]
        epub_source_cover_export = epub_root / "exports" / "epub" / "source-cover.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--output",
            str(epub_source_cover_export),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--epub",
            str(epub_source_cover_export),
        )
        with zipfile.ZipFile(epub_source_cover_export) as archive:
            source_cover_xhtml = archive.read("OEBPS/text/001-source-cover.xhtml").decode("utf-8")
            assert 'epub:type="titlepage"' in source_cover_xhtml
            assert "z-cover" in source_cover_xhtml

        image_pdf = root / "image-source.pdf"
        image_jpeg = root / "image-source.jpg"
        write_pdf_with_image(image_pdf, image_jpeg)
        image_library = root / "image-library"
        image_book_root = image_library / "image-source"
        image_map_path = image_book_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(image_pdf),
            "--library-root",
            str(image_library),
            "--dpi",
            "72",
        )
        image_assets_path = image_book_root / "metadata" / "assets-manifest.json"
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        assert len(image_assets["assets"]) == 1
        image_asset = image_assets["assets"][0]
        assert image_asset["source"]["format"] == "pdf"
        assert (image_book_root / image_asset["original"]["path"]).is_file()
        escaped_original_assets = copy.deepcopy(image_assets)
        escaped_original = escaped_original_assets["assets"][0]["original"]
        escaped_original["path"] = "assets/images/original/../../../source/original.pdf"
        escaped_original["sha256"] = sha256_file(image_book_root / "source" / "original.pdf")
        escaped_original_assets_path = image_book_root / "metadata" / "escaped-original-assets.json"
        escaped_original_assets_path.write_text(
            json.dumps(escaped_original_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(escaped_original_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        image_assets["assets"][0]["classification"]["content"] = "illustration"
        image_assets["assets"][0]["classification"]["text_pixels"] = "none"
        image_assets["assets"][0]["classification"]["restoration_eligibility"] = "review_required"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(image_pdf),
            "--library-root",
            str(image_library),
            "--assets-only",
        )
        refreshed_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        assert refreshed_assets["assets"][0]["classification"]["content"] == "illustration"

        book_map = json.loads(map_path.read_text(encoding="utf-8"))
        book_map["analysis"]["status"] = "approved"
        book_map["pages"][0]["status"] = "mapped"
        book_map["pages"][0]["blank"] = False
        book_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Abertura",
                "start_logical_page": 1,
                "end_logical_page": 1,
            }
        ]
        map_path.write_text(json.dumps(book_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(map_path),
            "--require-ready",
            "--check-files",
        )

        text_root = book_root / "text"
        source_file = text_root / "source" / "pages" / "page-0001.txt"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("Texto fiel de teste.", encoding="utf-8")
        ledger_path = book_root / "metadata" / "text-ledger.json"
        ledger = {
            "schema_version": "1.0",
            "book_map_sha256": sha256_file(map_path),
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": sha256_file(source_file),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            ],
        }
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--text-root",
            str(text_root),
        )
        wrong_hash_ledger = copy.deepcopy(ledger)
        wrong_hash_ledger["book_map_sha256"] = "0" * 64
        wrong_hash_path = book_root / "metadata" / "wrong-hash-ledger.json"
        wrong_hash_path.write_text(
            json.dumps(wrong_hash_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(wrong_hash_path),
            "--text-root",
            str(text_root),
        )
        escaped_ledger = copy.deepcopy(ledger)
        escaped_ledger["pages"][0]["source_file"] = "../../outside.txt"
        escaped_ledger_path = book_root / "metadata" / "escaped-ledger.json"
        escaped_ledger_path.write_text(
            json.dumps(escaped_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(escaped_ledger_path),
            "--text-root",
            str(text_root),
        )
        absolute_ledger = copy.deepcopy(ledger)
        absolute_ledger["pages"][0]["source_file"] = str(root / "outside.txt")
        absolute_ledger_path = book_root / "metadata" / "absolute-ledger.json"
        absolute_ledger_path.write_text(
            json.dumps(absolute_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(absolute_ledger_path),
            "--text-root",
            str(text_root),
        )
        locutor_page = text_root / "locutor" / "pages" / "page-0001.txt"
        locutor_page.parent.mkdir(parents=True)
        locutor_page.write_text("Texto derivado do locutor.", encoding="utf-8")
        source_from_locutor = copy.deepcopy(ledger)
        source_from_locutor["pages"][0]["source_file"] = "locutor/pages/page-0001.txt"
        source_from_locutor["pages"][0]["source_sha256"] = sha256_file(locutor_page)
        source_from_locutor_path = book_root / "metadata" / "source-from-locutor-ledger.json"
        source_from_locutor_path.write_text(
            json.dumps(source_from_locutor, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(source_from_locutor_path),
            "--text-root",
            str(text_root),
        )
        locutor_ledger = copy.deepcopy(ledger)
        locutor_ledger["pages"][0]["locutor_file"] = "locutor/pages/page-0001.txt"
        locutor_ledger["pages"][0]["locutor_sha256"] = sha256_file(locutor_page)
        locutor_ledger_path = book_root / "metadata" / "locutor-ledger.json"
        locutor_ledger_path.write_text(
            json.dumps(locutor_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(locutor_ledger_path),
            "--text-root",
            str(text_root),
            "--require-locutor",
        )
        locutor_from_source = copy.deepcopy(locutor_ledger)
        locutor_from_source["pages"][0]["locutor_file"] = "source/pages/page-0001.txt"
        locutor_from_source["pages"][0]["locutor_sha256"] = sha256_file(source_file)
        locutor_from_source_path = book_root / "metadata" / "locutor-from-source-ledger.json"
        locutor_from_source_path.write_text(
            json.dumps(locutor_from_source, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(locutor_from_source_path),
            "--text-root",
            str(text_root),
            "--require-locutor",
        )

        export_map = json.loads(image_map_path.read_text(encoding="utf-8"))
        export_map["analysis"]["status"] = "approved"
        export_map["pages"][0]["status"] = "mapped"
        export_map["pages"][0]["blank"] = False
        export_map["pages"][0]["chapter_id"] = "chapter-001"
        export_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Livro com Imagem",
                "start_logical_page": 1,
                "end_logical_page": 1,
            }
        ]
        export_map["book"] = {
            "title": "Livro com Ação",
            "subtitle": "Coração e Orixás",
            "author": "Antônio de Teste",
            "original_publication_place": "São Paulo",
            "original_publication_year": 1933,
        }
        image_map_path.write_text(
            json.dumps(export_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        image_text_root = image_book_root / "text"
        image_page_file = image_text_root / "source" / "pages" / "page-0001.txt"
        image_page_file.parent.mkdir(parents=True)
        image_page_file.write_text("LIVRO COM IMAGEM\n\nTexto fiel de EPUB.", encoding="utf-8")
        image_chapter = image_text_root / "source" / "chapters" / "chapter-01-livro-com-imagem.txt"
        image_chapter.parent.mkdir(parents=True)
        image_chapter.write_text(
            "LIVRO COM IMAGEM\n\nPrimeiro verso\nSegundo verso\nTerceiro verso\n\nTexto fiel de EPUB.",
            encoding="utf-8",
        )
        image_ledger_path = image_book_root / "metadata" / "text-ledger.json"
        image_ledger = {
            "schema_version": "1.0",
            "book_map_sha256": sha256_file(image_map_path),
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": sha256_file(image_page_file),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-001",
                    "source_file": "source/chapters/chapter-01-livro-com-imagem.txt",
                    "source_sha256": sha256_file(image_chapter),
                    "source_pages": [
                        {
                            "logical_page": 1,
                            "source_sha256": sha256_file(image_page_file),
                        }
                    ],
                    "verified_by": "codex",
                }
            ],
        }
        image_ledger_path.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--text-root",
            str(image_text_root),
        )
        image_epub_manifest = image_book_root / "metadata" / "epub-manifest.json"
        missing_chapter_outputs = copy.deepcopy(image_ledger)
        missing_chapter_outputs.pop("chapter_outputs")
        image_ledger_path.write_text(
            json.dumps(missing_chapter_outputs, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        image_ledger_path.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        alternate_ledger = image_book_root / "metadata" / "alternate-text-ledger.json"
        alternate_ledger.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(alternate_ledger),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        unplaced_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        assert unplaced_manifest["documents"][0]["asset_ids"] == []

        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        image_assets["assets"][0]["classification"] = {
            "content": "illustration",
            "text_pixels": "none",
            "restoration_eligibility": "eligible",
            "evidence": ["The PDF source page contains this standalone non-text illustration."],
        }
        image_assets["assets"][0]["epub"] = {
            "role": "illustration",
            "placement": "end",
            "document_id": "chapter-001",
            "alt_text": "",
        }
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        unanchored_assets = copy.deepcopy(image_assets)
        unanchored_assets["assets"][0]["epub"]["document_id"] = None
        image_assets_path.write_text(
            json.dumps(unanchored_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        visual_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        assert visual_manifest["visual_profile"] == {
            "name": "antique-paper",
            "cover": {"mode": "editorial"},
        }
        assert visual_manifest["book"]["subtitle"] == "Coração e Orixás"
        assert visual_manifest["book"]["publication_place"] == "São Paulo"
        canonical_epub = image_book_root / "exports" / "epub" / "canonical.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--output",
            str(canonical_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub",
            str(canonical_epub),
        )
        escaped_epub = root / "escaped-output.epub"
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--output",
            str(escaped_epub),
        )
        assert not escaped_epub.exists()
        with zipfile.ZipFile(canonical_epub) as archive:
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
            assert "OEBPS/nav.xhtml" in archive.namelist()
            assert any(path.startswith("OEBPS/images/") for path in archive.namelist())
            assert "OEBPS/text/000-cover.xhtml" in archive.namelist()
            assert "OEBPS/images/editorial-cover.jpg" in archive.namelist()
            assert "OEBPS/fonts/IMFeENrm28P.ttf" in archive.namelist()
            assert "OEBPS/fonts/IMFeENit28P.ttf" in archive.namelist()
            assert "OEBPS/fonts/OFL.txt" in archive.namelist()
            stylesheet = archive.read("OEBPS/styles/book.css").decode("utf-8")
            for color in ("#F3E7C9", "#3B2A1F", "#6B5140", "#4A2F22", "#B89B72", "#8C5A2B"):
                assert color in stylesheet
            assert 'font-family: "IM FELL English";' in stylesheet
            assert "../fonts/IMFeENrm28P.ttf" in stylesheet
            assert "../fonts/IMFeENit28P.ttf" in stylesheet
            assert archive.read("OEBPS/fonts/IMFeENrm28P.ttf") == (
                ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENrm28P.ttf"
            ).read_bytes()
            assert archive.read("OEBPS/fonts/IMFeENit28P.ttf") == (
                ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENit28P.ttf"
            ).read_bytes()
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            assert 'id="editorial-cover"' in opf
            assert 'properties="cover-image"' in opf
            assert 'id="image-1" href="images/pdf-page-0001-image-01.jpg" media-type="image/jpeg"/>' in opf
            cover = archive.read("OEBPS/text/000-cover.xhtml").decode("utf-8")
            assert 'epub:type="cover"' in cover
            assert "../images/editorial-cover.jpg" in cover
            assert 'alt="Capa editorial: Livro com Ação, por Antônio de Teste."' in cover
            chapter_xhtml = archive.read("OEBPS/text/001-chapter-001.xhtml").decode("utf-8")
            assert "Primeiro verso<br/>Segundo verso<br/>Terceiro verso" in chapter_xhtml

        visual_sidecar = json.loads(canonical_epub.with_suffix(".epub.json").read_text(encoding="utf-8"))
        assert visual_sidecar["visual_profile"]["name"] == "antique-paper"
        assert visual_sidecar["visual_profile"]["cover"]["epub_path"] == "OEBPS/images/editorial-cover.jpg"
        assert len(visual_sidecar["visual_profile"]["resources"]) == 3

        from PIL import ImageFont
        from epub_presentation import cover_image

        font = ImageFont.truetype(
            str(ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENrm28P.ttf"),
            48,
        )
        missing_glyph = bytes(font.getmask("\uffff"))
        for character in ("\u00e1", "\u00e3", "\u00e7", "\u00e9", "\u00ed", "\u00f3", "\u00f5", "\u00fa"):
            assert bytes(font.getmask(character)) != missing_glyph
        long_cover = cover_image(
            {
                "title": "Uma História Editorial de Muitas Linhas para Validar o Layout da Capa",
                "subtitle": "Uma edição cuidadosamente organizada para leitura digital",
                "author": "Nome Composto do Autor de Uma Obra Muito Extensa",
                "publication_place": "São Paulo",
                "publication_year": 1933,
            }
        )
        assert long_cover.startswith(b"\xff\xd8")
        try:
            cover_image({"title": "X" * 2000})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected an oversized editorial cover title to fail clearly.")

        legacy_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        legacy_manifest.pop("visual_profile")
        legacy_manifest_path = image_book_root / "metadata" / "epub-manifest-legacy.json"
        legacy_manifest_path.write_text(
            json.dumps(legacy_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        legacy_epub = image_book_root / "exports" / "epub" / "legacy.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(legacy_manifest_path),
            "--output",
            str(legacy_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(legacy_manifest_path),
            "--epub",
            str(legacy_epub),
        )
        with zipfile.ZipFile(legacy_epub) as archive:
            assert "OEBPS/text/000-cover.xhtml" not in archive.namelist()
            assert "OEBPS/fonts/IMFeENrm28P.ttf" not in archive.namelist()

        invalid_visual_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        invalid_visual_manifest["visual_profile"]["name"] = "unknown"
        invalid_visual_manifest_path = image_book_root / "metadata" / "epub-manifest-invalid-visual.json"
        invalid_visual_manifest_path.write_text(
            json.dumps(invalid_visual_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(invalid_visual_manifest_path),
            "--output",
            str(image_book_root / "exports" / "epub" / "invalid-visual.epub"),
        )

        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        original_asset = image_assets["assets"][0]
        original_path = image_book_root / original_asset["original"]["path"]
        restored_path = image_book_root / "restoration" / "approved" / f"{original_path.stem}.png"
        restored_path.parent.mkdir(parents=True)
        from PIL import Image

        with Image.open(original_path) as original_image:
            original_image.save(restored_path, "PNG")
        original_asset["restoration"] = {
            "status": "approved",
            "approved": {
                "path": restored_path.relative_to(image_book_root).as_posix(),
                "sha256": sha256_file(restored_path),
                "original_sha256": original_asset["original"]["sha256"],
                "media_type": "image/jpeg",
                "tool": "codex-imagegen",
                "prompt": "Restore only visual defects; preserve all content.",
                "reviewed_by": "codex test",
                "approved_at": "2026-07-13T00:00:00Z",
            },
        }
        original_asset["classification"]["text_pixels"] = "mixed"
        original_asset["classification"]["restoration_eligibility"] = "review_required"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "none"
        original_asset["classification"]["restoration_eligibility"] = "eligible"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["restoration"]["approved"]["media_type"] = "image/png"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_asset["classification"]["restoration_eligibility"] = "prohibited"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "mixed"
        original_asset["classification"]["restoration_eligibility"] = "manual_exception"
        original_asset["restoration"]["approved"]["exception_reason"] = (
            "Approved visual cleanup of a source facsimile; original remains canonical evidence."
        )
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "none"
        original_asset["classification"]["restoration_eligibility"] = "eligible"
        original_asset["restoration"]["approved"].pop("exception_reason")
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        escaped_approved_assets = copy.deepcopy(image_assets)
        escaped_approved = escaped_approved_assets["assets"][0]["restoration"]["approved"]
        escaped_approved["path"] = (
            "restoration/approved/../../assets/images/original/" + original_path.name
        )
        escaped_approved["sha256"] = original_asset["original"]["sha256"]
        escaped_approved["media_type"] = original_asset["original"]["media_type"]
        escaped_approved_assets_path = image_book_root / "metadata" / "escaped-approved-assets.json"
        escaped_approved_assets_path.write_text(
            json.dumps(escaped_approved_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(escaped_approved_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--output",
            str(image_epub_manifest),
        )
        restored_epub = image_book_root / "exports" / "epub" / "restored.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--image-edition",
            "approved-restored",
            "--output",
            str(restored_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub",
            str(restored_epub),
            "--image-edition",
            "approved-restored",
        )
        with zipfile.ZipFile(restored_epub) as archive:
            restored_opf = archive.read("OEBPS/content.opf").decode("utf-8")
            assert 'media-type="image/png"' in restored_opf
            assert any(
                path.endswith(".png")
                for path in archive.namelist()
                if path.startswith("OEBPS/images/")
            )

        audio_root = image_book_root / "audio"
        mock_wav = audio_root / "mock" / "wav" / "audiobook.wav"
        mock_wav.parent.mkdir(parents=True)
        write_wav(mock_wav, b"\x00\x00" * SAMPLE_RATE)
        assert mock_wav.is_file()
        compressed_audio_root = audio_root / "mock" / "m4a"
        compressed_audio_root.mkdir(parents=True)
        compressed_audio = compressed_audio_root / "audiobook.m4a"
        transcode(mock_wav, compressed_audio, "m4a")
        assert compressed_audio.is_file()
        audio_manifest_path = image_book_root / "metadata" / "audio-manifest.json"
        audio_manifest = {
            "schema_version": "1.0",
            "mock": True,
            "render_mode": "mock",
            "final_audio": compressed_audio.relative_to(image_book_root).as_posix(),
            "final_audio_sha256": sha256_file(compressed_audio),
        }
        audio_manifest_path.write_text(
            json.dumps(audio_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assert audio_manifest["final_audio_sha256"] == sha256_file(compressed_audio)
        mp3_audio_root = audio_root / "mock" / "mp3"
        mp3_audio_root.mkdir(parents=True)
        mp3_audio = mp3_audio_root / "audiobook.mp3"
        transcode(mock_wav, mp3_audio, "mp3")
        assert mp3_audio.is_file()
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=sample_rate,channels,bit_rate",
                "-of",
                "json",
                str(mp3_audio),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["sample_rate"] == "44100"
        assert stream["channels"] == 1
        assert stream["bit_rate"] == "128000"
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(compressed_audio),
        )
        audio_manifest["mock"] = False
        audio_manifest["render_mode"] = "real"
        audio_manifest_path.write_text(
            json.dumps(audio_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(compressed_audio),
        )
        published_audio = image_book_root / "Livro-com-Acao-audiobook.m4a"
        published_epub = image_book_root / "restored.epub"
        assert not published_audio.exists()
        assert not published_epub.exists()

        real_audio = audio_root / "real" / "audiobook.m4a"
        real_audio.parent.mkdir(parents=True, exist_ok=True)
        real_audio.write_bytes(compressed_audio.read_bytes())
        real_manifest = {
            "schema_version": "1.0",
            "mock": False,
            "render_mode": "real",
            "engine": "chatterbox-multilingual-v3-pt-br",
            "final_audio": real_audio.relative_to(image_book_root).as_posix(),
            "final_audio_sha256": sha256_file(real_audio),
        }
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        real_audio.write_bytes(b"changed-audio")
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
        )
        real_audio.write_bytes(compressed_audio.read_bytes())
        real_manifest["final_audio_sha256"] = sha256_file(real_audio)
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        non_chatterbox_manifest = dict(real_manifest)
        non_chatterbox_manifest.pop("engine")
        audio_manifest_path.write_text(
            json.dumps(non_chatterbox_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
        )
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        invalid_epub = image_book_root / "exports" / "epub" / "invalid-no-sidecar.epub"
        invalid_epub.write_bytes(b"not an epub")
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
            "--epub",
            str(invalid_epub),
        )
        assert not published_audio.exists()
        assert not published_epub.exists()
        assert "publication" not in json.loads(audio_manifest_path.read_text(encoding="utf-8"))
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
            "--epub",
            str(restored_epub),
        )
        assert published_audio.read_bytes() == real_audio.read_bytes()
        assert published_epub.read_bytes() == restored_epub.read_bytes()
        publication_manifest = json.loads(
            (image_book_root / "metadata" / "publication-manifest.json").read_text(encoding="utf-8")
        )
        assert publication_manifest["artifacts"]["audio"]["path"] == published_audio.name
        assert publication_manifest["artifacts"]["epub"]["path"] == published_epub.name
        assert json.loads(audio_manifest_path.read_text(encoding="utf-8"))["publication"]["sha256"] == sha256_file(
            published_audio
        )
        assert json.loads(restored_epub.with_suffix(".epub.json").read_text(encoding="utf-8"))[
            "publication"
        ]["sha256"] == sha256_file(published_epub)

        run(str(ROOT / "render_chatterbox.py"), "--help")
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-max-chars"),
            "--standalone",
            "--max-chars",
            "321",
        )
        invalid_chatterbox_text = image_text_root / "locutor" / "invalid-chatterbox.txt"
        invalid_chatterbox_text.parent.mkdir(parents=True, exist_ok=True)
        invalid_chatterbox_text.write_text("[thoughtful] Texto.", encoding="utf-8")
        invalid_chatterbox_output = audio_root / "chatterbox-invalid-text"
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(invalid_chatterbox_text),
            "--output-dir",
            str(invalid_chatterbox_output),
            "--standalone",
        )
        assert not invalid_chatterbox_output.exists()
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-min-p"),
            "--standalone",
            "--min-p",
            "1.1",
        )
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-silence"),
            "--standalone",
            "--silence-seconds",
            "-0.1",
        )
        chatterbox_invalid_output = audio_root / "chatterbox-invalid"
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(chatterbox_invalid_output),
            "--book-root",
            str(image_book_root),
            "--format",
            "wav",
        )
        assert not chatterbox_invalid_output.exists()

        plugin_root = ROOT.parent
        marketplace = {
            "name": "test",
            "plugins": [
                {
                    "name": "audiobook-codex",
                    "source": {"source": "local", "path": "./wrong-path"},
                    "policy": {"installation": "NOT_AVAILABLE", "authentication": "ON_USE"},
                    "category": "Other",
                }
            ],
        }
        bad_marketplace = root / "bad-marketplace.json"
        bad_marketplace.write_text(
            json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_plugin_local.py"),
            "--plugin-root",
            str(plugin_root),
            "--marketplace",
            str(bad_marketplace),
        )

        if os.environ.get("CHATTERBOX_REAL_SMOKE") == "1":
            chatterbox_python = os.environ.get("CHATTERBOX_PYTHON")
            if not chatterbox_python:
                raise AssertionError(
                    "CHATTERBOX_REAL_SMOKE=1 requires CHATTERBOX_PYTHON."
                )
            chatterbox_smoke_text = calibration_text
            chatterbox_smoke_input = root / "chatterbox-cuda-smoke.txt"
            chatterbox_smoke_input.write_text(chatterbox_smoke_text, encoding="utf-8")
            chatterbox_smoke_output = root / "chatterbox-cuda-smoke"
            run_with_python(
                chatterbox_python,
                str(ROOT / "render_chatterbox.py"),
                "--input-file",
                str(chatterbox_smoke_input),
                "--output-dir",
                str(chatterbox_smoke_output),
                "--standalone",
                "--device",
                "cuda",
                "--format",
                "wav",
            )
            chatterbox_manifest = json.loads(
                (chatterbox_smoke_output / "audio-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            chatterbox_segment = (
                chatterbox_smoke_output / "segments" / "segment-0001.wav"
            )
            chatterbox_final_wav = chatterbox_smoke_output / "raw" / "audiobook.wav"
            chatterbox_final_audio = chatterbox_smoke_output / "audiobook.wav"
            assert chatterbox_manifest["mock"] is False
            assert chatterbox_manifest["render_mode"] == "real"
            assert chatterbox_manifest["engine"] == "chatterbox-multilingual-v3-pt-br"
            assert chatterbox_manifest["profile"] == "feminina-v1"
            assert chatterbox_manifest["device"] == "cuda"
            assert chatterbox_manifest["sample_rate"] == 24000
            assert len(chatterbox_manifest["segments"]) == 1
            assert chatterbox_manifest["segments"][0]["locutor_line"] == 1
            assert (
                chatterbox_manifest["segments"][0]["character_count"]
                == len(chatterbox_smoke_text)
            )
            assert chatterbox_manifest["segments"][0]["warnings"] == [
                "uses punctuation normalized by Chatterbox"
            ]
            assert chatterbox_manifest["duration_seconds"] > 0
            assert chatterbox_segment.is_file() and chatterbox_segment.stat().st_size > 44
            assert chatterbox_final_wav.is_file()
            assert chatterbox_final_audio.is_file()
            assert (
                chatterbox_manifest["final_wav_sha256"]
                == sha256_file(chatterbox_final_wav)
            )
            assert (
                chatterbox_manifest["final_wav_sha256"]
                == FEMININA_PROFILE_CALIBRATION["main_prompt_wav_sha256"]
            )
            assert (
                chatterbox_manifest["final_audio_sha256"]
                == sha256_file(chatterbox_final_audio)
            )

    print("Audiobook Codex script tests passed.")


if __name__ == "__main__":
    main()
