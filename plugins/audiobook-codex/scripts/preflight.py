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
import xml.etree.ElementTree as ET
import zipfile

from asset_inventory import source_image_assets, write_assets_manifest
from book_layout import (
    DEFAULT_LIBRARY_ROOT,
    BookPaths,
    assert_no_reparse_ancestors,
    canonical_book_folder_name,
    ensure_assembly_tree,
    lexical_absolute,
    path_lexists,
    paths_for_new_book,
    resolve_book_paths,
)
from publication_selection import default_selection, selection_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stored_source_path(assembly_root: Path, source: Path) -> Path:
    return assembly_root / "source" / f"original{source.suffix.lower()}"


def select_book_root(
    source: Path,
    source_sha256: str,
    library_root: Path,
    title: str,
    publication_year: int,
    author: str,
    explicit_output_dir: Path | None,
) -> BookPaths:
    expected_name = canonical_book_folder_name(title, publication_year, author)
    if explicit_output_dir is not None:
        public_root = lexical_absolute(explicit_output_dir)
        if path_lexists(public_root):
            try:
                existing = resolve_book_paths(public_root, allow_legacy=True)
            except RuntimeError as error:
                raise RuntimeError(
                    f"Cannot reuse the explicit book folder: {error}"
                ) from error
            if existing.layout_kind == "new" and public_root.name != expected_name:
                raise RuntimeError(
                    f"Explicit new-layout book root must use the canonical folder name "
                    f"{expected_name!r}: {public_root}"
                )
            candidate_source = stored_source_path(existing.assembly_root, source)
            if (
                candidate_source.is_file()
                and sha256_file(candidate_source) == source_sha256
            ):
                return existing
            raise RuntimeError(
                f"Explicit book folder belongs to another source: {public_root}"
            )
        if public_root.name != expected_name:
            raise RuntimeError(
                f"Explicit book root must use the canonical folder name {expected_name!r}: "
                f"{public_root}"
            )
        paths = BookPaths(public_root, public_root / "assembly", "new")
    else:
        library_root = assert_no_reparse_ancestors(library_root, "Library root")
        library_root.mkdir(parents=True, exist_ok=True)
        assert_no_reparse_ancestors(library_root, "Library root")
        paths = paths_for_new_book(
            library_root,
            title,
            publication_year,
            author,
        )

    if not paths.public_root.exists():
        return paths

    try:
        existing = resolve_book_paths(paths.public_root, allow_legacy=True)
    except RuntimeError as error:
        raise RuntimeError(
            f"Cannot reuse the canonical book folder: {error}"
        ) from error
    candidate_source = stored_source_path(existing.assembly_root, source)
    if candidate_source.is_file() and sha256_file(candidate_source) == source_sha256:
        return existing
    raise RuntimeError(
        f"Canonical book folder already belongs to another source: {paths.public_root}"
    )


def stage_source(source: Path, assembly_root: Path, source_sha256: str) -> Path:
    target = stored_source_path(assembly_root, source)
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
        for relative_path in (
            Path("native") / "poppler" / "Library" / "bin" / "pdftoppm.exe",
            Path("native") / "poppler" / "bin" / "pdftoppm.exe",
        ):
            direct_executable = ancestor / relative_path
            if direct_executable.is_file():
                return str(direct_executable)
    return str(wrapper)


def spread_signal(image: object) -> bool:
    """Return whether one rendered landscape page visibly contains a central gutter."""
    width, height = image.size
    if height <= 0 or width / height < 1.15:
        return False

    sample_height = 400
    sample_width = max(1, round(width * sample_height / height))
    grayscale = image.convert("L").resize((sample_width, sample_height))
    start_y = round(sample_height * 0.05)
    end_y = round(sample_height * 0.95)
    column_height = max(1, end_y - start_y)
    start_x = round(sample_width * 0.43)
    end_x = round(sample_width * 0.57)
    column_means: list[float] = []
    dark_fractions: list[float] = []
    for x in range(sample_width):
        values = [grayscale.getpixel((x, y)) for y in range(start_y, end_y)]
        column_means.append(sum(values) / column_height)
        dark_fractions.append(sum(value < 130 for value in values) / column_height)

    for x in range(start_x, end_x):
        neighbor_columns = (
            column_means[max(0, x - 18) : max(0, x - 7)]
            + column_means[min(sample_width, x + 8) : min(sample_width, x + 19)]
        )
        if not neighbor_columns:
            continue
        if (
            dark_fractions[x] >= 0.2
            and sum(neighbor_columns) / len(neighbor_columns) - column_means[x] >= 10
        ):
            return True
    return False


def resolve_rendered_layout(
    physical_paths: list[Path],
    image_module: object,
    requested_layout: str,
) -> tuple[str, dict]:
    if requested_layout != "auto":
        return requested_layout, {
            "requested": requested_layout,
            "resolved": requested_layout,
            "method": "explicit",
        }

    inspected = min(len(physical_paths), 12)
    signals = 0
    for path in physical_paths[:inspected]:
        with image_module.open(path) as opened:
            signals += int(spread_signal(opened))

    threshold = max(1, (inspected + 1) // 2)
    resolved_layout = "spread" if signals >= threshold else "single"
    return resolved_layout, {
        "requested": "auto",
        "resolved": resolved_layout,
        "method": "rendered-central-gutter",
        "inspected_pages": inspected,
        "spread_signals": signals,
        "threshold": threshold,
    }


def rendered_pdf_pages(
    source: Path,
    output_root: Path,
    page_count: int,
    dpi: int,
    rotation: str,
    layout: str,
) -> tuple[list[dict], str, dict]:
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
    physical_paths: list[Path] = []
    for source_page in range(1, page_count + 1):
        physical_target = physical_dir / f"page-{source_page:04d}.png"
        raw_path = raw_pages[source_page]
        with image_module.open(raw_path) as opened:
            image = opened.convert("RGB")
            if rotation_degrees:
                image = image.rotate(rotation_degrees, expand=True)
            image.save(physical_target)
        raw_path.unlink()
        physical_paths.append(physical_target)

    resolved_layout, detection = resolve_rendered_layout(
        physical_paths,
        image_module,
        layout,
    )
    records: list[dict] = []
    logical_page = 1

    for source_page, physical_target in enumerate(physical_paths, start=1):
        if resolved_layout == "single":
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

    return records, resolved_layout, detection


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
        resolved_layout = "single" if layout == "auto" else layout
        detection = {
            "requested": layout,
            "resolved": resolved_layout,
            "method": "skip-render-safe-default" if layout == "auto" else "explicit",
        }
        logical_count = page_count * (2 if resolved_layout == "spread" else 1)
        pages = []
        for logical_page in range(1, logical_count + 1):
            source_page = (logical_page + 1) // 2 if resolved_layout == "spread" else logical_page
            side = "left" if resolved_layout == "spread" and logical_page % 2 else "right" if resolved_layout == "spread" else "single"
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
        pages, resolved_layout, detection = rendered_pdf_pages(
            source,
            output_root,
            page_count,
            dpi,
            rotation,
            layout,
        )
        logical_count = len(pages)

    source_data = {
        "path": str(source.resolve()),
        "sha256": sha256_file(source),
        "format": "pdf",
        "page_count_physical": page_count,
        "page_count_logical": logical_count,
        "text_layer_pages": text_pages,
    }
    return {
        "source": source_data,
        "extraction_mode": extraction_mode,
        "layout": resolved_layout,
        "layout_detection": detection,
    }, pages


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
        help=r"Library directory for automatic book folders. Defaults to E:\Pessoal\Library.",
    )
    parser.add_argument("--title", required=True, help="Verified canonical book title.")
    parser.add_argument("--publication-year", required=True, type=int)
    parser.add_argument("--author", required=True, help="Verified canonical book author.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Explicit public book root. Its name must match title, year, and author.",
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "single", "spread"),
        default="auto",
        help=(
            "Detect two-page scans from rendered central gutters by default. "
            "Use single or spread only to override that detection."
        ),
    )
    parser.add_argument("--rotation", choices=("normal", "cw90", "ccw90", "180"), default="normal")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--source-language", default="")
    parser.add_argument("--narration-language", default="pt-BR")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--assets-only",
        action="store_true",
        help="Extract or refresh original image assets without replacing an existing book map.",
    )
    args = parser.parse_args()

    original_source = args.source.expanduser().resolve()
    if not original_source.is_file():
        raise SystemExit(f"Source file not found: {original_source}")
    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72.")
    if args.publication_year <= 0:
        raise SystemExit("--publication-year must be positive.")

    source_sha256 = sha256_file(original_source)
    inferred_output_dir = args.output_dir
    if (
        args.assets_only
        and inferred_output_dir is None
        and original_source.parent.name.lower() == "source"
        and original_source.stem.lower() == "original"
    ):
        inferred_assembly = original_source.parent.parent
        inferred_output_dir = (
            inferred_assembly.parent
            if inferred_assembly.name.casefold() == "assembly"
            else inferred_assembly
        )
    try:
        paths = select_book_root(
            original_source,
            source_sha256,
            args.library_root,
            str(args.title).strip(),
            args.publication_year,
            str(args.author).strip(),
            inferred_output_dir,
        )
        if paths.layout_kind == "new":
            ensure_assembly_tree(paths)
        output_root = paths.assembly_root
        source = stage_source(original_source, output_root, source_sha256)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error

    map_path = output_root / "metadata" / "book-map.json"
    if args.assets_only:
        if not map_path.is_file():
            raise SystemExit(f"Book map is required for --assets-only: {map_path}")
        try:
            book_map = load_json(map_path)
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        if not isinstance(book_map, dict) or not isinstance(book_map.get("pages"), list):
            raise SystemExit(f"Book map has no pages: {map_path}")
        map_source = book_map.get("source")
        if not isinstance(map_source, dict) or map_source.get("sha256") != source_sha256:
            raise SystemExit("Book map source hash does not match --source.")
        assets_path = write_assets_manifest(
            output_root,
            source_sha256,
            source_image_assets(source, output_root, book_map["pages"]),
        )
        print(f"Book root: {paths.public_root}")
        print(f"Assembly root: {output_root}")
        print(f"Refreshed {assets_path}")
        return

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
        resolved_layout = preflight["layout"]
    elif suffix == ".epub":
        preflight, pages = epub_map(source)
        resolved_layout = "reflow"
        args.rotation = "normal"
    else:
        raise SystemExit("Only .pdf and .epub source files are supported.")

    book_map = {
        "schema_version": "1.0",
        "book": {
            "title": str(args.title).strip(),
            "subtitle": "",
            "author": str(args.author).strip(),
            "original_publication_year": args.publication_year,
            "original_publication_place": "",
        },
        "source": preflight["source"],
        "analysis": {
            "status": "needs_analysis",
            "layout": resolved_layout,
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
    if suffix == ".pdf":
        book_map["analysis"]["layout_detection"] = preflight["layout_detection"]
    book_map["source"].update(
        {
            "path": source.relative_to(output_root).as_posix(),
            "original_path": str(original_source),
            "original_file_name": original_source.name,
        }
    )
    write_json(map_path, book_map)
    publication_selection_path = selection_path(output_root)
    if not publication_selection_path.exists():
        write_json(publication_selection_path, default_selection())
    assets_path = write_assets_manifest(
        output_root,
        source_sha256,
        source_image_assets(source, output_root, pages),
    )
    print(f"Book root: {paths.public_root}")
    print(f"Assembly root: {output_root}")
    print(f"Stored source: {source}")
    print(f"Created {map_path}")
    print(f"Created {publication_selection_path}")
    print(f"Created {assets_path}")
    print(
        f"Source: {book_map['source']['format']}, logical units: {book_map['source']['page_count_logical']}, "
        f"extraction mode: {book_map['analysis']['extraction_mode']}"
    )


if __name__ == "__main__":
    main()
