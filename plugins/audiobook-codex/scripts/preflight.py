from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\e-books")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_book_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-. ")
    if not normalized:
        raise RuntimeError("Could not derive a safe book folder name from the source filename.")
    return normalized[:120].rstrip(". ")


def stored_source_path(book_root: Path, source: Path) -> Path:
    return book_root / "source" / f"original{source.suffix.lower()}"


def select_book_root(
    source: Path,
    source_sha256: str,
    library_root: Path,
    requested_book_id: str,
    explicit_output_dir: Path | None,
) -> Path:
    if explicit_output_dir is not None:
        return explicit_output_dir.expanduser().resolve()

    library_root = library_root.expanduser().resolve()
    library_root.mkdir(parents=True, exist_ok=True)
    book_id = normalize_book_id(requested_book_id or source.stem)
    candidate = library_root / book_id
    if not candidate.exists():
        return candidate

    candidate_source = stored_source_path(candidate, source)
    if candidate_source.is_file() and sha256_file(candidate_source) == source_sha256:
        return candidate

    if requested_book_id:
        raise RuntimeError(
            f"Book id already belongs to another source: {candidate}. Choose a different --book-id."
        )

    hash_candidate = library_root / f"{book_id}-{source_sha256[:8]}"
    if not hash_candidate.exists():
        return hash_candidate

    stored_hash_candidate = stored_source_path(hash_candidate, source)
    if stored_hash_candidate.is_file() and sha256_file(stored_hash_candidate) == source_sha256:
        return hash_candidate
    raise RuntimeError(
        f"Refusing to reuse an existing book directory with a different source: {hash_candidate}"
    )


def stage_source(source: Path, book_root: Path, source_sha256: str) -> Path:
    target = stored_source_path(book_root, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.resolve() == source.resolve():
            return target
        if sha256_file(target) != source_sha256:
            raise RuntimeError(
                f"Refusing to replace the stored source with a different file: {target}"
            )
        return target

    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


def require_pypdf() -> object:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "pypdf is required for PDF preflight. Run this script with the Codex bundled Python."
        ) from error
    return PdfReader


def require_pillow() -> object:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required to rotate or split rendered PDF pages. "
            "Run this script with the Codex bundled Python."
        ) from error
    return Image


def run(command: list[str]) -> None:
    executable_suffix = Path(command[0]).suffix.lower()
    invocation = (
        ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(command)]
        if executable_suffix in {".cmd", ".bat"}
        else command
    )
    completed = subprocess.run(invocation, text=True, capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown command failure"
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{message}")


def resolve_pdftoppm() -> str:
    located = shutil.which("pdftoppm")
    if not located:
        raise RuntimeError("pdftoppm was not found on PATH.")
    wrapper = Path(located)
    if wrapper.suffix.lower() not in {".cmd", ".bat"}:
        return str(wrapper)

    for ancestor in wrapper.parents:
        direct_executable = ancestor / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
        if direct_executable.is_file():
            return str(direct_executable)
    return str(wrapper)


def rendered_pdf_pages(source: Path, output_root: Path, page_count: int, dpi: int, rotation: str, layout: str) -> list[dict]:
    executable = resolve_pdftoppm()

    image_module = require_pillow()
    physical_dir = output_root / "pages" / "physical"
    logical_dir = output_root / "pages" / "logical"
    physical_dir.mkdir(parents=True, exist_ok=True)
    logical_dir.mkdir(parents=True, exist_ok=True)

    prefix = physical_dir / "render"
    run([executable, "-r", str(dpi), "-png", str(source), str(prefix)])

    raw_pages: dict[int, Path] = {}
    for candidate in physical_dir.glob("render-*.png"):
        suffix = candidate.stem.rsplit("-", 1)[-1]
        if suffix.isdigit():
            raw_pages[int(suffix)] = candidate

    if len(raw_pages) != page_count:
        raise RuntimeError(
            f"pdftoppm rendered {len(raw_pages)} page(s), but pypdf reported {page_count}."
        )

    rotation_degrees = {
        "normal": 0,
        "cw90": -90,
        "ccw90": 90,
        "180": 180,
    }[rotation]
    records: list[dict] = []
    logical_page = 1

    for source_page in range(1, page_count + 1):
        physical_target = physical_dir / f"page-{source_page:04d}.png"
        raw_path = raw_pages[source_page]
        with image_module.open(raw_path) as opened:
            image = opened.convert("RGB")
            if rotation_degrees:
                image = image.rotate(rotation_degrees, expand=True)
            image.save(physical_target)
        raw_path.unlink()

        if layout == "single":
            logical_target = logical_dir / f"page-{logical_page:04d}.png"
            shutil.copy2(physical_target, logical_target)
            records.append(
                {
                    "logical_page": logical_page,
                    "source_page": source_page,
                    "side": "single",
                    "render_path": logical_target.relative_to(output_root).as_posix(),
                    "printed_page": None,
                    "blank": None,
                    "kind": "unknown",
                    "status": "needs_analysis",
                    "chapter_id": None,
                    "evidence": [],
                }
            )
            logical_page += 1
            continue

        with image_module.open(physical_target) as opened:
            image = opened.convert("RGB")
            split_x = image.width // 2
            sides = [
                ("left", image.crop((0, 0, split_x, image.height))),
                ("right", image.crop((split_x, 0, image.width, image.height))),
            ]
            for side, crop in sides:
                logical_target = logical_dir / f"page-{logical_page:04d}.png"
                crop.save(logical_target)
                records.append(
                    {
                        "logical_page": logical_page,
                        "source_page": source_page,
                        "side": side,
                        "render_path": logical_target.relative_to(output_root).as_posix(),
                        "printed_page": None,
                        "blank": None,
                        "kind": "unknown",
                        "status": "needs_analysis",
                        "chapter_id": None,
                        "evidence": [],
                    }
                )
                logical_page += 1

    return records


def pdf_map(source: Path, output_root: Path, dpi: int, layout: str, rotation: str, skip_render: bool) -> tuple[dict, list[dict]]:
    reader_type = require_pypdf()
    reader = reader_type(str(source))
    page_count = len(reader.pages)
    if page_count <= 0:
        raise RuntimeError("The PDF has no pages.")

    text_lengths: list[int] = []
    for page in reader.pages:
        try:
            text_lengths.append(len((page.extract_text() or "").strip()))
        except Exception:
            text_lengths.append(0)

    text_pages = sum(length >= 24 for length in text_lengths)
    if text_pages == 0:
        extraction_mode = "visual"
    elif text_pages == page_count:
        extraction_mode = "text_layer"
    else:
        extraction_mode = "mixed"

    if skip_render:
        logical_count = page_count * (2 if layout == "spread" else 1)
        pages = []
        for logical_page in range(1, logical_count + 1):
            source_page = (logical_page + 1) // 2 if layout == "spread" else logical_page
            side = "left" if layout == "spread" and logical_page % 2 else "right" if layout == "spread" else "single"
            pages.append(
                {
                    "logical_page": logical_page,
                    "source_page": source_page,
                    "side": side,
                    "render_path": "",
                    "printed_page": None,
                    "blank": None,
                    "kind": "unknown",
                    "status": "needs_analysis",
                    "chapter_id": None,
                    "evidence": [],
                }
            )
    else:
        pages = rendered_pdf_pages(source, output_root, page_count, dpi, rotation, layout)
        logical_count = len(pages)

    source_data = {
        "path": str(source.resolve()),
        "sha256": sha256_file(source),
        "format": "pdf",
        "page_count_physical": page_count,
        "page_count_logical": logical_count,
        "text_layer_pages": text_pages,
    }
    return {"source": source_data, "extraction_mode": extraction_mode}, pages


def epub_spine_documents(source: Path) -> list[str]:
    with zipfile.ZipFile(source) as archive:
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(element for element in container.iter() if element.tag.endswith("rootfile"))
            opf_path = rootfile.attrib["full-path"]
            package = ET.fromstring(archive.read(opf_path))
            manifest = {
                item.attrib["id"]: item.attrib["href"]
                for item in package.iter()
                if item.tag.endswith("item") and item.attrib.get("id") and item.attrib.get("href")
            }
            opf_parent = str(PurePosixPath(opf_path).parent)
            documents = []
            for itemref in package.iter():
                if not itemref.tag.endswith("itemref"):
                    continue
                href = manifest.get(itemref.attrib.get("idref", ""))
                if href:
                    documents.append(posixpath.normpath(posixpath.join(opf_parent, href)))
            if documents:
                return documents
        except (KeyError, StopIteration, ET.ParseError):
            pass

        return sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith((".xhtml", ".html", ".htm"))
        )


def epub_map(source: Path) -> tuple[dict, list[dict]]:
    documents = epub_spine_documents(source)
    if not documents:
        raise RuntimeError("No EPUB XHTML or HTML spine documents were found.")

    pages = [
        {
            "logical_page": index,
            "source_locator": document,
            "side": "reflow",
            "render_path": "",
            "printed_page": None,
            "blank": None,
            "kind": "unknown",
            "status": "needs_analysis",
            "chapter_id": None,
            "evidence": [],
        }
        for index, document in enumerate(documents, start=1)
    ]
    return (
        {
            "source": {
                "path": str(source.resolve()),
                "sha256": sha256_file(source),
                "format": "epub",
                "page_count_physical": len(documents),
                "page_count_logical": len(documents),
                "unit_kind": "spine_document",
            },
            "extraction_mode": "epub_source",
        },
        pages,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a Codex-only audiobook preflight map without OCR."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--library-root",
        type=Path,
        default=DEFAULT_LIBRARY_ROOT,
        help=r"Library directory for automatic book folders. Defaults to E:\Pessoal\e-books.",
    )
    parser.add_argument(
        "--book-id",
        default="",
        help="Optional folder name below --library-root. A safe name is derived from the source by default.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Explicit book root for advanced use. It bypasses automatic --library-root placement.",
    )
    parser.add_argument("--layout", choices=("single", "spread"), default="single")
    parser.add_argument("--rotation", choices=("normal", "cw90", "ccw90", "180"), default="normal")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--source-language", default="")
    parser.add_argument("--narration-language", default="pt-BR")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    original_source = args.source.expanduser().resolve()
    if not original_source.is_file():
        raise SystemExit(f"Source file not found: {original_source}")
    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72.")

    source_sha256 = sha256_file(original_source)
    try:
        output_root = select_book_root(
            original_source,
            source_sha256,
            args.library_root,
            str(args.book_id).strip(),
            args.output_dir,
        )
        source = stage_source(original_source, output_root, source_sha256)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error

    map_path = output_root / "metadata" / "book-map.json"
    if map_path.exists() and not args.overwrite:
        raise SystemExit(f"Map already exists: {map_path}. Use a new output directory or --overwrite.")

    suffix = source.suffix.lower()
    output_root.mkdir(parents=True, exist_ok=True)
    if suffix == ".pdf":
        preflight, pages = pdf_map(
            source,
            output_root,
            args.dpi,
            args.layout,
            args.rotation,
            args.skip_render,
        )
    elif suffix == ".epub":
        preflight, pages = epub_map(source)
        args.layout = "reflow"
        args.rotation = "normal"
    else:
        raise SystemExit("Only .pdf and .epub source files are supported.")

    book_map = {
        "schema_version": "1.0",
        "source": preflight["source"],
        "analysis": {
            "status": "needs_analysis",
            "layout": args.layout,
            "rotation": args.rotation,
            "extraction_mode": preflight["extraction_mode"],
            "source_language": args.source_language,
            "narration_language": args.narration_language,
            "created_at": iso_now(),
        },
        "page_number_alignment": {"segments": []},
        "toc_chapters": [],
        "chapters": [],
        "ranges": {"ignored": [], "narration_excluded": []},
        "pages": pages,
        "warnings": [
            "Preflight establishes physical and logical coverage only. Codex must complete structural analysis before transcription."
        ],
    }
    book_map["source"].update(
        {
            "path": source.relative_to(output_root).as_posix(),
            "original_path": str(original_source),
            "original_file_name": original_source.name,
        }
    )
    write_json(map_path, book_map)
    print(f"Book root: {output_root}")
    print(f"Stored source: {source}")
    print(f"Created {map_path}")
    print(
        f"Source: {book_map['source']['format']}, logical units: {book_map['source']['page_count_logical']}, "
        f"extraction mode: {book_map['analysis']['extraction_mode']}"
    )


if __name__ == "__main__":
    main()
