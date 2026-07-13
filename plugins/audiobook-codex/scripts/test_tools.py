from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
  </manifest>
  <spine><itemref idref="chapter-1"/><itemref idref="chapter-2"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter-1.xhtml", "<html><body><p>Um.</p></body></html>")
        archive.writestr("OEBPS/chapter-2.xhtml", "<html><body><p>Dois.</p></body></html>")


def main() -> None:
    from pypdf import PdfWriter

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

        narrator = text_root / "locutor.txt"
        narrator.write_text("Texto do locutor para teste.", encoding="utf-8")
        audio_root = book_root / "audio"
        run(
            str(ROOT / "render_kokoro.py"),
            "--input-file",
            str(narrator),
            "--output-dir",
            str(audio_root),
            "--format",
            "wav",
            "--mock",
        )
        manifest = json.loads((audio_root / "audio-manifest.json").read_text(encoding="utf-8"))
        assert manifest["mock"] is True
        assert manifest["segments"]
        assert (audio_root / "audiobook.wav").is_file()
        compressed_audio_root = book_root / "audio-m4a"
        run(
            str(ROOT / "render_kokoro.py"),
            "--input-file",
            str(narrator),
            "--output-dir",
            str(compressed_audio_root),
            "--format",
            "m4a",
            "--mock",
        )
        assert (compressed_audio_root / "audiobook.m4a").is_file()

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

        if os.environ.get("KOKORO_REAL_SMOKE") == "1":
            kokoro_python = os.environ.get("KOKORO_PYTHON")
            if not kokoro_python:
                raise AssertionError("KOKORO_REAL_SMOKE=1 requires KOKORO_PYTHON.")
            run_with_python(
                kokoro_python,
                str(ROOT / "render_kokoro.py"),
                "--input-file",
                str(narrator),
                "--output-dir",
                str(book_root / "audio-real"),
                "--format",
                "m4a",
            )

    print("Audiobook Codex script tests passed.")


if __name__ == "__main__":
    main()
