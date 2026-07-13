from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import zipfile

from epub_presentation import (
    COVER_DOCUMENT_PATH,
    COVER_IMAGE_PATH,
    cover_alt_text,
    cover_image,
    normalize_visual_profile,
    profile_resources,
    profile_stylesheet,
    sha256_bytes,
)
from validate_assets_manifest import load_json as load_assets_json
from validate_assets_manifest import resolve_under, validate_assets_manifest
from validate_book_map import load_json as load_book_map_json
from validate_book_map import validate_book_map
from verify_text_ledger import chapter_output_records
from verify_text_ledger import verify as verify_text_ledger


IMAGE_EDITIONS = {"original", "approved-restored"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_segment(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.encode("ascii", "ignore").decode("ascii"))
    return normalized.strip(".-")[:100] or fallback


def escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def relative_to_book(book_root: Path, path: Path) -> str:
    return path.resolve().relative_to(book_root.resolve()).as_posix()


def export_directory(book_root: Path) -> Path:
    return (book_root / "exports" / "epub").resolve()


def resolve_export_output(book_root: Path, raw_output: Path | None, default_name: str) -> Path:
    output = raw_output.expanduser().resolve() if raw_output else export_directory(book_root) / default_name
    try:
        output.relative_to(export_directory(book_root))
    except ValueError as error:
        raise RuntimeError(
            f"EPUB output must remain under {export_directory(book_root)}: {output}"
        ) from error
    if output.suffix.lower() != ".epub":
        raise RuntimeError("EPUB output must use the .epub extension")
    return output


def load_export_context(
    book_root: Path,
    epub_manifest_path: Path,
    assets_manifest_path: Path,
) -> tuple[dict, dict, dict, dict, Path, Path]:
    map_path = book_root / "metadata" / "book-map.json"
    ledger_path = book_root / "metadata" / "text-ledger.json"
    text_root = book_root / "text"
    book_map = load_book_map_json(map_path)
    ledger = load_assets_json(ledger_path)
    assets_manifest = load_assets_json(assets_manifest_path)
    epub_manifest = load_assets_json(epub_manifest_path)
    if not all(isinstance(value, dict) for value in (book_map, ledger, assets_manifest, epub_manifest)):
        raise RuntimeError("Book map, ledger, assets manifest, and EPUB manifest must be JSON objects.")
    errors = validate_book_map(book_map, book_root, True, True)
    errors += verify_text_ledger(book_map, sha256_file(map_path), ledger, text_root, False, True)
    errors += validate_assets_manifest(assets_manifest, book_root, book_map, True)
    if epub_manifest.get("schema_version") != "1.0":
        errors.append("epub manifest schema_version must be '1.0'")
    expected_hashes = {
        "book_map_sha256": sha256_file(map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "assets_manifest_sha256": sha256_file(assets_manifest_path),
    }
    for key, expected in expected_hashes.items():
        if epub_manifest.get(key) != expected:
            errors.append(f"epub manifest {key} does not match current input")
    if not require_text(epub_manifest.get("language")):
        errors.append("epub manifest language must be non-empty")
    try:
        normalize_visual_profile(epub_manifest.get("visual_profile"))
    except RuntimeError as error:
        errors.append(str(error))
    if errors:
        raise RuntimeError("; ".join(errors))
    return book_map, ledger, assets_manifest, epub_manifest, map_path, ledger_path


def validate_documents(
    book_root: Path,
    epub_manifest: dict,
    assets_manifest: dict,
    ledger: dict,
) -> tuple[list[dict], dict[str, dict]]:
    documents = epub_manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("epub manifest documents must be a non-empty array")
    assets = assets_manifest.get("assets")
    asset_by_id = {
        asset.get("id"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    } if isinstance(assets, list) else {}
    ledger_outputs = chapter_output_records(ledger)
    text_root = book_root / "text"
    ids: set[str] = set()
    validated: list[dict] = []
    for index, document in enumerate(documents):
        label = f"documents[{index}]"
        if not isinstance(document, dict):
            raise RuntimeError(f"{label} must be an object")
        document_id = document.get("id")
        if not require_text(document_id) or document_id in ids:
            raise RuntimeError(f"{label}.id must be unique and non-empty")
        ids.add(document_id)
        if not require_text(document.get("title")):
            raise RuntimeError(f"{label}.title must be non-empty")
        source_file = document.get("source_file")
        source_path = None
        is_source_cover = document.get("kind") == "source_cover"
        if is_source_cover:
            if source_file is not None or document.get("source_sha256") is not None:
                raise RuntimeError(f"{label} source_cover must not declare source text")
        else:
            source_path = resolve_under(book_root, source_file)
            if source_path is None or not str(source_file).replace("\\", "/").startswith("text/source/"):
                raise RuntimeError(f"{label}.source_file must resolve under text/source/")
            if not source_path.is_file():
                raise RuntimeError(f"{label}.source_file is missing: {source_file}")
            if document.get("source_sha256") != sha256_file(source_path):
                raise RuntimeError(f"{label}.source_sha256 does not match source_file")
            ledger_output = ledger_outputs.get(document_id)
            if not isinstance(ledger_output, dict):
                raise RuntimeError(f"{label} has no verified chapter output in text-ledger.json")
            ledger_source = ledger_output.get("source_file")
            expected_source_path = resolve_under(text_root, ledger_source)
            if expected_source_path is None or expected_source_path != source_path:
                raise RuntimeError(f"{label}.source_file does not match its verified chapter output")
            if document.get("source_sha256") != ledger_output.get("source_sha256"):
                raise RuntimeError(f"{label}.source_sha256 does not match its verified chapter output")
        asset_ids = document.get("asset_ids", [])
        if not isinstance(asset_ids, list) or any(not isinstance(asset_id, str) for asset_id in asset_ids):
            raise RuntimeError(f"{label}.asset_ids must be an array of strings")
        unknown_assets = [asset_id for asset_id in asset_ids if asset_id not in asset_by_id]
        if unknown_assets:
            raise RuntimeError(f"{label}.asset_ids contain unknown assets: {unknown_assets}")
        if is_source_cover and not asset_ids:
            raise RuntimeError(f"{label} source_cover must reference at least one asset")
        validated.append({**document, "_source_path": source_path})
    return validated, asset_by_id


def selected_asset(asset: dict, book_root: Path, image_edition: str) -> dict:
    original = asset.get("original")
    if not isinstance(original, dict):
        raise RuntimeError(f"Asset {asset.get('id')} has no original record")
    selected = original
    if image_edition == "approved-restored":
        restoration = asset.get("restoration")
        approved = restoration.get("approved") if isinstance(restoration, dict) else None
        if not isinstance(restoration, dict) or restoration.get("status") != "approved" or not isinstance(approved, dict):
            raise RuntimeError(f"Asset {asset.get('id')} has no approved restoration")
        if approved.get("original_sha256") != original.get("sha256"):
            raise RuntimeError(f"Asset {asset.get('id')} approved restoration has invalid lineage")
        selected = {**original, **approved}
    selected_path = resolve_under(book_root, selected.get("path"))
    if selected_path is None or not selected_path.is_file():
        raise RuntimeError(f"Asset {asset.get('id')} selected path is missing")
    if selected.get("sha256") != sha256_file(selected_path):
        raise RuntimeError(f"Asset {asset.get('id')} selected file hash does not match")
    if not require_text(selected.get("media_type")) or not str(selected["media_type"]).startswith("image/"):
        raise RuntimeError(f"Asset {asset.get('id')} selected rendition has an invalid media type")
    return {
        "id": asset["id"],
        "path": selected_path,
        "sha256": selected["sha256"],
        "media_type": selected.get("media_type") or original.get("media_type"),
        "alt_text": str((asset.get("epub") or {}).get("alt_text") or ""),
        "role": str((asset.get("epub") or {}).get("role") or "illustration"),
        "placement": str((asset.get("epub") or {}).get("placement") or "after_title"),
    }


def paragraphs_from_text(text: str, title: str) -> tuple[str, list[str]]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", text.replace("\r\n", "\n"))]
    blocks = [block for block in blocks if block]
    heading = title
    if blocks:
        first = blocks[0]
        if normalize_space(title).casefold() in normalize_space(first).casefold():
            heading = normalize_space(first)
            blocks = blocks[1:]
    return heading, blocks


def preserves_short_line_breaks(block: str) -> bool:
    # Reflow ordinary PDF-wrapped prose, but retain compact verse-like source blocks.
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > 12:
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= 48 and sum(lengths) / len(lengths) <= 36


def paragraph_markup(block: str) -> str:
    if preserves_short_line_breaks(block):
        content = "<br/>".join(escape(line.strip()) for line in block.splitlines() if line.strip())
    else:
        content = escape(normalize_space(block))
    return f"    <p>{content}</p>"


def figure_markup(asset: dict, href: str) -> str:
    alt_text = escape(asset["alt_text"])
    return (
        '    <figure class="illustration">\n'
        f'      <img src="{escape(href)}" alt="{alt_text}"/>\n'
        "    </figure>"
    )


def source_cover_markup(document: dict, language: str, asset_hrefs: list[tuple[dict, str]]) -> str:
    figures = [figure_markup(asset, href) for asset, href in asset_hrefs]
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(document['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            "<body>",
            '  <section epub:type="titlepage" class="source-cover">',
            *figures,
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def document_markup(document: dict, language: str, asset_hrefs: list[tuple[dict, str]]) -> str:
    if document.get("kind") == "source_cover":
        return source_cover_markup(document, language, asset_hrefs)
    text = document["_source_path"].read_text(encoding="utf-8")
    heading, paragraphs = paragraphs_from_text(text, str(document["title"]))
    before = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] != "end"]
    after = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] == "end"]
    body_parts = [f"    <h1>{escape(heading)}</h1>", *before]
    body_parts.extend(paragraph_markup(paragraph) for paragraph in paragraphs)
    body_parts.extend(after)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(document['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            "<body>",
            "  <section>",
            *body_parts,
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def cover_markup(book: dict, language: str) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(book['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            '<body class="cover-page">',
            '  <section epub:type="cover" class="editorial-cover">',
            f'    <img src="../{COVER_IMAGE_PATH}" alt="{escape(cover_alt_text(book))}"/>',
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def nav_markup(
    title: str,
    language: str,
    document_hrefs: list[tuple[dict, str]],
    cover_href: str | None,
) -> str:
    items = [
        f'      <li><a href="{escape(href)}">{escape(document["title"])}</a></li>'
        for document, href in document_hrefs
        if document.get("kind") not in {"cover", "source_cover"}
    ]
    body_document = next(
        (
            (document, href)
            for document, href in document_hrefs
            if document.get("kind") not in {"cover", "source_cover"}
        ),
        document_hrefs[0] if document_hrefs else None,
    )
    landmarks: list[str] = []
    if cover_href:
        landmarks.append(f'      <li><a epub:type="cover" href="{escape(cover_href)}">Cover</a></li>')
    if body_document:
        landmarks.append(
            f'      <li><a epub:type="bodymatter" href="{escape(body_document[1])}">Body</a></li>'
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(title)}</title>",
            '  <link rel="stylesheet" type="text/css" href="styles/book.css"/>',
            "</head>",
            "<body>",
            '  <nav epub:type="toc" id="toc">',
            f"    <h1>{escape(title)}</h1>",
            "    <ol>",
            *items,
            "    </ol>",
            "  </nav>",
            '  <nav epub:type="landmarks" hidden="hidden">',
            "    <h2>Landmarks</h2>",
            "    <ol>",
            *landmarks,
            "    </ol>",
            "  </nav>",
            "</body>",
            "</html>",
        ]
    )


def stylesheet(visual_profile: dict | None) -> str:
    return profile_stylesheet(visual_profile)


def container_xml() -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">',
            "  <rootfiles>",
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>',
            "  </rootfiles>",
            "</container>",
        ]
    )


def opf_markup(
    book: dict,
    language: str,
    book_id: str,
    document_hrefs: list[tuple[dict, str]],
    image_hrefs: list[tuple[dict, str]],
    visual_profile: dict | None,
    presentation_resources: list[object],
) -> str:
    title = str(book.get("title") or "Untitled")
    author = str(book.get("author") or "").strip()
    modified = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="style" href="styles/book.css" media-type="text/css"/>',
    ]
    if visual_profile:
        manifest.extend(
            [
                f'    <item id="cover-page" href="{COVER_DOCUMENT_PATH}" media-type="application/xhtml+xml"/>',
                f'    <item id="editorial-cover" href="{COVER_IMAGE_PATH}" media-type="image/jpeg" properties="cover-image"/>',
            ]
        )
        for resource in presentation_resources:
            manifest.append(
                f'    <item id="{escape(resource.identifier)}" href="{escape(resource.epub_path)}" media-type="{escape(resource.media_type)}"/>'
            )
    for index, (_, href) in enumerate(document_hrefs, start=1):
        manifest.append(f'    <item id="doc-{index}" href="{escape(href)}" media-type="application/xhtml+xml"/>')
    cover_asset = (
        None
        if visual_profile
        else next((asset for asset, _ in image_hrefs if asset["role"] in {"cover", "cover_candidate"}), None)
    )
    for index, (asset, href) in enumerate(image_hrefs, start=1):
        cover_property = ' properties="cover-image"' if asset is cover_asset else ""
        manifest.append(
            f'    <item id="image-{index}" href="{escape(href)}" media-type="{escape(asset["media_type"])}"{cover_property}/>'
        )
    spine = []
    if visual_profile:
        spine.append('    <itemref idref="cover-page"/>')
    spine.extend(f'    <itemref idref="doc-{index}"/>' for index, _ in enumerate(document_hrefs, start=1))
    metadata = [
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
        f'    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>',
        f"    <dc:title>{escape(title)}</dc:title>",
        f"    <dc:language>{escape(language)}</dc:language>",
    ]
    if author:
        metadata.append(f"    <dc:creator>{escape(author)}</dc:creator>")
    if isinstance(book.get("publication_year"), int):
        metadata.append(f"    <dc:date>{book['publication_year']}</dc:date>")
    if visual_profile:
        metadata.append('    <meta name="cover" content="editorial-cover"/>')
    metadata.extend(
        [
            f'    <meta property="dcterms:modified">{modified}</meta>',
            "  </metadata>",
        ]
    )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">',
            *metadata,
            "  <manifest>",
            *manifest,
            "  </manifest>",
            "  <spine>",
            *spine,
            "  </spine>",
            "</package>",
        ]
    )


def write_epub(
    output: Path,
    book: dict,
    language: str,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    visual_profile: dict | None,
) -> tuple[list[dict], dict | None]:
    document_hrefs: list[tuple[dict, str]] = []
    for index, document in enumerate(documents, start=1):
        href = f"text/{index:03d}-{safe_segment(str(document['id']), f'document-{index:03d}')}.xhtml"
        document_hrefs.append((document, href))
    image_hrefs: list[tuple[dict, str]] = []
    seen_asset_ids: set[str] = set()
    for document in documents:
        for asset in selected_assets_by_document[document["id"]]:
            if asset["id"] in seen_asset_ids:
                continue
            seen_asset_ids.add(asset["id"])
            suffix = asset["path"].suffix.lower() or ".img"
            image_hrefs.append((asset, f"images/{safe_segment(asset['id'], 'asset')}{suffix}"))
    image_href_by_id = {asset["id"]: href for asset, href in image_hrefs}
    book_id = f"urn:uuid:{hashlib.sha256(json.dumps(book, sort_keys=True).encode('utf-8')).hexdigest()[:32]}"
    presentation_resources = profile_resources(visual_profile)
    cover_bytes = cover_image(book) if visual_profile else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container_xml(), compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "OEBPS/styles/book.css",
            stylesheet(visual_profile),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        if cover_bytes is not None:
            archive.writestr(
                f"OEBPS/{COVER_DOCUMENT_PATH}",
                cover_markup(book, language),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            archive.writestr(
                f"OEBPS/{COVER_IMAGE_PATH}",
                cover_bytes,
                compress_type=zipfile.ZIP_DEFLATED,
            )
        for resource in presentation_resources:
            archive.writestr(
                f"OEBPS/{resource.epub_path}",
                resource.source_path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        for document, href in document_hrefs:
            assets = selected_assets_by_document[document["id"]]
            references = [(asset, f"../{image_href_by_id[asset['id']]}") for asset in assets]
            archive.writestr(
                f"OEBPS/{href}",
                document_markup(document, language, references),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        archive.writestr(
            "OEBPS/nav.xhtml",
            nav_markup(
                str(book.get("title") or "Untitled"),
                language,
                document_hrefs,
                COVER_DOCUMENT_PATH if visual_profile else None,
            ),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            opf_markup(
                book,
                language,
                book_id,
                document_hrefs,
                image_hrefs,
                visual_profile,
                presentation_resources,
            ),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for asset, href in image_hrefs:
            archive.writestr(f"OEBPS/{href}", asset["path"].read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    assets = [
        {
            "id": asset["id"],
            "sha256": asset["sha256"],
            "media_type": asset["media_type"],
            "epub_path": f"OEBPS/{href}",
        }
        for asset, href in image_hrefs
    ]
    presentation = None
    if cover_bytes is not None:
        presentation = {
            "name": visual_profile["name"],
            "cover": {
                "epub_path": f"OEBPS/{COVER_IMAGE_PATH}",
                "sha256": sha256_bytes(cover_bytes),
                "media_type": "image/jpeg",
            },
            "resources": [
                {
                    "id": resource.identifier,
                    "epub_path": f"OEBPS/{resource.epub_path}",
                    "sha256": resource.sha256,
                    "media_type": resource.media_type,
                }
                for resource in presentation_resources
            ],
        }
    return assets, presentation


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a semantic EPUB from verified Audiobook Codex source artifacts.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument("--image-edition", choices=sorted(IMAGE_EDITIONS), default="original")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        book_root = args.book_root.expanduser().resolve()
        epub_manifest_path = (
            args.epub_manifest.expanduser().resolve()
            if args.epub_manifest
            else book_root / "metadata" / "epub-manifest.json"
        )
        assets_manifest_path = (
            args.assets_manifest.expanduser().resolve()
            if args.assets_manifest
            else book_root / "metadata" / "assets-manifest.json"
        )
        book_map, ledger, assets_manifest, epub_manifest, map_path, ledger_path = load_export_context(
            book_root, epub_manifest_path, assets_manifest_path
        )
        documents, asset_by_id = validate_documents(book_root, epub_manifest, assets_manifest, ledger)
        selected_assets_by_document = {
            document["id"]: [
                selected_asset(asset_by_id[asset_id], book_root, args.image_edition)
                for asset_id in document["asset_ids"]
            ]
            for document in documents
        }
        visual_profile = normalize_visual_profile(epub_manifest.get("visual_profile"))
        book = epub_manifest.get("book") if isinstance(epub_manifest.get("book"), dict) else {}
        if not require_text(book.get("title")):
            source_book = book_map.get("book") if isinstance(book_map.get("book"), dict) else {}
            book = {**source_book, **book}
        book = {
            "title": str(book.get("title") or "Untitled"),
            "subtitle": str(book.get("subtitle") or ""),
            "author": str(book.get("author") or ""),
            "publication_year": book.get("publication_year"),
            "publication_place": str(book.get("publication_place") or ""),
        }
        edition_label = "fiel" if args.image_edition == "original" else "restaurada"
        if visual_profile:
            edition_label = f"{edition_label}-classico"
        output = resolve_export_output(
            book_root,
            args.output,
            f"{safe_segment(book['title'], 'book')}-{edition_label}.epub",
        )
        assets, presentation = write_epub(
            output,
            book,
            str(epub_manifest["language"]),
            documents,
            selected_assets_by_document,
            visual_profile,
        )
        sidecar = output.with_suffix(".epub.json")
        sidecar_data = {
            "schema_version": "1.0",
            "epub_path": relative_to_book(book_root, output),
            "epub_sha256": sha256_file(output),
            "image_edition": args.image_edition,
            "book_map_sha256": sha256_file(map_path),
            "text_ledger_sha256": sha256_file(ledger_path),
            "assets_manifest_sha256": sha256_file(assets_manifest_path),
            "assets": assets,
        }
        if presentation:
            sidecar_data["visual_profile"] = presentation
        write_json(sidecar, sidecar_data)
    except RuntimeError as error:
        print(f"Cannot export EPUB: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Created {output}")
    print(f"Created {sidecar}")


if __name__ == "__main__":
    main()
