from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import publish_artifacts


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_manifest_name(text_edition: str) -> str:
    if text_edition == "fluid-pt-br":
        return "epub-manifest.fluid.json"
    if text_edition == "translated-pt-br":
        return "epub-manifest.pt-br.json"
    if text_edition == "revised-pt-br":
        return "epub-manifest.revised.json"
    return "epub-manifest.json"


def base_metadata(
    assembly_root: Path,
    *,
    text_edition: str = "original",
    language: str = "pt-BR",
    layout: dict | None = None,
) -> dict[str, str]:
    metadata = assembly_root / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    for name in ("book-map.json", "text-ledger.json", "assets-manifest.json"):
        path = metadata / name
        if not path.exists():
            write_json(path, {"name": name})
    hashes = {
        "book_map_sha256": sha256_file(metadata / "book-map.json"),
        "text_ledger_sha256": sha256_file(metadata / "text-ledger.json"),
        "assets_manifest_sha256": sha256_file(metadata / "assets-manifest.json"),
    }
    manifest = {"schema_version": "1.0", "language": language, **hashes}
    if text_edition != "original":
        manifest["text_edition"] = text_edition
    if layout is not None:
        manifest["layout"] = layout
    write_json(metadata / canonical_manifest_name(text_edition), manifest)
    return hashes


def add_layout(
    assembly_root: Path,
    *,
    text_edition: str = "original",
    relative_path: str | None = None,
    mode: str = "semantic",
) -> dict:
    if relative_path is None:
        relative_path = (
            "metadata/epub-layout.fluid.json"
            if text_edition == "fluid-pt-br"
            else (
                "metadata/epub-layout.pt-br.json"
                if text_edition == "translated-pt-br"
                else "metadata/epub-layout.json"
            )
        )
    layout_path = assembly_root / relative_path
    write_json(layout_path, {"documents": []})
    return {
        "mode": mode,
        "path": relative_path,
        "sha256": sha256_file(layout_path),
    }


def write_reader_sidecar(
    source: Path,
    kind: str,
    *,
    text_edition: str = "original",
    language: str = "pt-BR",
    layout: dict | None = None,
    sidecar_layout: object = None,
    sidecar_overrides: dict | None = None,
) -> None:
    assembly_root = source.parents[2]
    hashes = base_metadata(
        assembly_root,
        text_edition=text_edition,
        language=language,
        layout=layout,
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"{kind} bytes".encode("utf-8"))
    data = {
        "schema_version": "1.0",
        f"{kind}_path": source.relative_to(assembly_root).as_posix(),
        f"{kind}_sha256": sha256_file(source),
        "input_fingerprint": {"value": kind[0] * 64},
        "image_edition": "original",
        "text_edition": text_edition,
        "language": language,
        **hashes,
    }
    if sidecar_layout is not None:
        data["layout"] = sidecar_layout
    if sidecar_overrides:
        data.update(sidecar_overrides)
    write_json(source.with_suffix(f".{kind}.json"), data)


def run_publish(public_root: Path, *args: str, expect_failure: bool) -> None:
    with patch.object(sys, "argv", ["publish_artifacts.py", "--book-root", str(public_root), *args]):
        try:
            publish_artifacts.main()
        except SystemExit as error:
            if expect_failure:
                assert error.code == 1
                return
            raise
    if expect_failure:
        raise AssertionError("publish_artifacts.py unexpectedly succeeded")


def test_rejects_stale_canonical_manifest_even_when_sidecar_hashes_are_current() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-lineage-stale-") as raw:
        public_root = Path(raw) / "Book"
        assembly_root = public_root / "assembly"
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")
        book_map = assembly_root / "metadata" / "book-map.json"
        write_json(book_map, {"name": "book-map.json", "changed": True})
        sidecar_path = epub.with_suffix(".epub.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["book_map_sha256"] = sha256_file(book_map)
        write_json(sidecar_path, sidecar)

        run_publish(public_root, "--epub", str(epub), expect_failure=True)
        assert not (public_root / "Book.epub").exists()


def test_rejects_epub_sidecar_missing_manifest_layout() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-lineage-epub-layout-missing-") as raw:
        public_root = Path(raw) / "Book"
        assembly_root = public_root / "assembly"
        layout = add_layout(assembly_root)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub", layout=layout)

        run_publish(public_root, "--epub", str(epub), expect_failure=True)
        assert not (public_root / "Book.epub").exists()


def test_rejects_epub_sidecar_divergent_manifest_layout() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-lineage-epub-layout-divergent-") as raw:
        public_root = Path(raw) / "Book"
        assembly_root = public_root / "assembly"
        layout = add_layout(assembly_root)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        divergent = {**layout, "sha256": "0" * 64}
        write_reader_sidecar(epub, "epub", layout=layout, sidecar_layout=divergent)

        run_publish(public_root, "--epub", str(epub), expect_failure=True)
        assert not (public_root / "Book.epub").exists()


def test_rejects_pdf_sidecar_missing_manifest_layout() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-lineage-pdf-layout-missing-") as raw:
        public_root = Path(raw) / "Book"
        assembly_root = public_root / "assembly"
        layout = add_layout(assembly_root)
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        write_reader_sidecar(pdf, "pdf", layout=layout)

        run_publish(public_root, "--pdf", str(pdf), expect_failure=True)
        assert not (public_root / "Book.pdf").exists()


def test_rejects_pdf_sidecar_divergent_manifest_layout() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-lineage-pdf-layout-divergent-") as raw:
        public_root = Path(raw) / "Book"
        assembly_root = public_root / "assembly"
        layout = add_layout(assembly_root)
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        divergent = {**layout, "path": "metadata/other-layout.json"}
        write_reader_sidecar(pdf, "pdf", layout=layout, sidecar_layout=divergent)

        run_publish(public_root, "--pdf", str(pdf), expect_failure=True)
        assert not (public_root / "Book.pdf").exists()


def test_rejects_ptbr_reader_sidecar_invalid_language() -> None:
    cases = (
        ("translated-pt-br", ("epub",)),
        ("revised-pt-br", ("pdf",)),
        ("fluid-pt-br", ("epub", "pdf")),
    )
    for text_edition, kinds in cases:
        with tempfile.TemporaryDirectory(
            prefix=f"audiobook-lineage-{text_edition}-language-"
        ) as raw:
            public_root = Path(raw) / "Book"
            assembly_root = public_root / "assembly"
            layout = add_layout(assembly_root, text_edition=text_edition)
            arguments: list[str] = []
            for kind in kinds:
                source = assembly_root / "exports" / kind / f"reader.{kind}"
                write_reader_sidecar(
                    source,
                    kind,
                    text_edition=text_edition,
                    language="en-US",
                    layout=layout,
                    sidecar_layout=layout,
                )
                arguments.extend((f"--{kind}", str(source)))

            run_publish(public_root, *arguments, expect_failure=True)


def test_rejects_required_ptbr_reader_layout_missing() -> None:
    for text_edition, kinds in (
        ("translated-pt-br", ("epub",)),
        ("revised-pt-br", ("pdf",)),
        ("fluid-pt-br", ("epub", "pdf")),
    ):
        with tempfile.TemporaryDirectory(
            prefix=f"audiobook-lineage-{text_edition}-layout-missing-"
        ) as raw:
            public_root = Path(raw) / "Book"
            assembly_root = public_root / "assembly"
            arguments: list[str] = []
            for kind in kinds:
                source = assembly_root / "exports" / kind / f"reader.{kind}"
                write_reader_sidecar(source, kind, text_edition=text_edition)
                arguments.extend((f"--{kind}", str(source)))

            run_publish(public_root, *arguments, expect_failure=True)


def test_rejects_canonical_layout_mode_and_path_by_edition() -> None:
    cases = (
        ("original", "epub", {"mode": "legacy"}),
        ("translated-pt-br", "epub", {"path": "metadata/other-layout.json"}),
        ("revised-pt-br", "pdf", {"path": "metadata/other-layout.json"}),
        ("fluid-pt-br", "pdf", {"mode": "legacy"}),
    )
    for text_edition, kind, overrides in cases:
        with tempfile.TemporaryDirectory(
            prefix=f"audiobook-lineage-{text_edition}-layout-invalid-"
        ) as raw:
            public_root = Path(raw) / "Book"
            assembly_root = public_root / "assembly"
            canonical = add_layout(assembly_root, text_edition=text_edition)
            layout = {**canonical, **overrides}
            if layout["path"] != canonical["path"]:
                layout = add_layout(
                    assembly_root,
                    text_edition=text_edition,
                    relative_path=str(layout["path"]),
                    mode=str(layout["mode"]),
                )
            arguments: list[str] = []
            kinds = ("epub", "pdf") if text_edition == "fluid-pt-br" else (kind,)
            for selected_kind in kinds:
                source = (
                    assembly_root
                    / "exports"
                    / selected_kind
                    / f"reader.{selected_kind}"
                )
                write_reader_sidecar(
                    source,
                    selected_kind,
                    text_edition=text_edition,
                    layout=layout,
                    sidecar_layout=layout,
                )
                arguments.extend((f"--{selected_kind}", str(source)))

            run_publish(public_root, *arguments, expect_failure=True)


def run_tests() -> None:
    tests = [
        test_rejects_stale_canonical_manifest_even_when_sidecar_hashes_are_current,
        test_rejects_epub_sidecar_missing_manifest_layout,
        test_rejects_epub_sidecar_divergent_manifest_layout,
        test_rejects_pdf_sidecar_missing_manifest_layout,
        test_rejects_pdf_sidecar_divergent_manifest_layout,
        test_rejects_ptbr_reader_sidecar_invalid_language,
        test_rejects_required_ptbr_reader_layout_missing,
        test_rejects_canonical_layout_mode_and_path_by_edition,
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
    print(f"publication lineage tests passed ({len(tests)} run)")


if __name__ == "__main__":
    run_tests()
