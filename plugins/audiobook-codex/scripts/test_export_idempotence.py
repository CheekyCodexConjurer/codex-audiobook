from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import traceback
import time
from unittest import SkipTest
from unittest.mock import patch
import zipfile

import export_epub
import export_reader_pair
import export_pdf
import publish_artifacts
from book_transaction_lock import LOCK_RELATIVE_PATH
from book_transaction_lock import BookTransactionLock
from book_transaction_lock import book_lock_path
from export_epub import EPUB_MODIFIED, ZIP_TIMESTAMP
from export_epub import cached_export_is_current
from export_epub import export_fingerprint_payload, export_input_fingerprint
from test_pdf_export import ROOT, build_semantic_pdf_fixture, run, sha256_file


FIXED_MTIME_NS = 1_700_000_000_123_456_789


def set_fixed_mtime(path: Path) -> None:
    os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))


def mtimes(paths: list[Path]) -> dict[Path, int]:
    return {path: path.stat().st_mtime_ns for path in paths}


def bytes_snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def assert_no_reader_pair_residue(directory: Path) -> None:
    residue = [
        path.name
        for path in directory.iterdir()
        if ".pair-stage" in path.name
        or ".final-sidecar-stage" in path.name
        or ".backup" in path.name
        or path.name == LOCK_RELATIVE_PATH.name
    ]
    assert residue == []


def fake_reader_pair_export_command(command: list[str]) -> None:
    output = Path(command[command.index("--output") + 1])
    book_root = Path(command[command.index("--book-root") + 1])
    output_path = output.resolve().relative_to(book_root.resolve()).as_posix()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == ".epub":
        output.write_bytes(b"epub bytes")
        output.with_suffix(".epub.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "epub_path": output_path,
                    "epub_sha256": sha256_file(output),
                    "input_fingerprint": {"value": "e" * 64},
                    "image_edition": "original",
                    "text_edition": "original",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif output.suffix == ".pdf":
        output.write_bytes(b"pdf bytes")
        output.with_suffix(".pdf.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "pdf_path": output_path,
                    "pdf_sha256": sha256_file(output),
                    "input_fingerprint": {"value": "p" * 64},
                    "image_edition": "original",
                    "text_edition": "original",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def run_reader_pair_with_fakes(
    book_root: Path,
    epub: Path,
    pdf: Path,
    *,
    snapshots: list[str] | None = None,
    failing_label: str | None = None,
    fail_epub_export: bool = False,
    fail_pdf_export: bool = False,
) -> None:
    snapshot_iter = iter(snapshots or ["frozen", "frozen", "frozen", "frozen"])

    def fake_run_commands(commands: list[list[str]], label: str) -> None:
        if label == "export":
            for command in commands:
                is_epub = "export_epub.py" in command[1]
                is_pdf = "export_pdf.py" in command[1]
                if (is_epub and fail_epub_export) or (is_pdf and fail_pdf_export):
                    continue
                fake_reader_pair_export_command(command)
        if failing_label == label:
            raise RuntimeError(f"{label} failed")

    with (
        patch.object(
            export_reader_pair,
            "resolve_book_paths",
            lambda path: SimpleNamespace(public_root=book_root, assembly_root=book_root),
        ),
        patch.object(
            export_reader_pair,
            "input_snapshot",
            lambda _root: next(snapshot_iter),
        ),
        patch.object(export_reader_pair, "run_commands", fake_run_commands),
        patch.object(sys, "argv", [
            "export_reader_pair.py",
            "--book-root",
            str(book_root),
            "--epub-output",
            str(epub),
            "--pdf-output",
            str(pdf),
        ]),
    ):
        export_reader_pair.main()


def ensure_publication_metadata(assembly_root: Path, language: str = "pt-BR") -> dict[str, str]:
    metadata_root = assembly_root / "metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    for name in ("book-map.json", "text-ledger.json", "assets-manifest.json"):
        path = metadata_root / name
        if not path.exists():
            path.write_text("{}\n", encoding="utf-8")
    hashes = {
        "book_map_sha256": sha256_file(metadata_root / "book-map.json"),
        "text_ledger_sha256": sha256_file(metadata_root / "text-ledger.json"),
        "assets_manifest_sha256": sha256_file(metadata_root / "assets-manifest.json"),
    }
    manifest_path = metadata_root / "epub-manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "1.0", "language": language, **hashes}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return hashes


def write_reader_sidecar(source: Path, kind: str, **overrides: object) -> None:
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"{kind} bytes".encode("utf-8"))
    lineage = ensure_publication_metadata(source.parents[2])
    key = f"{kind}_path"
    hash_key = f"{kind}_sha256"
    data = {
        "schema_version": "1.0",
        key: source.relative_to(source.parents[2]).as_posix(),
        hash_key: sha256_file(source),
        "input_fingerprint": {"value": kind[0] * 64},
        "image_edition": "original",
        "text_edition": "original",
        "language": "pt-BR",
        **lineage,
    }
    data.update(overrides)
    source.with_suffix(f".{kind}.json").write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def run_publish(book_root: Path, *args: str, expect_failure: bool = False) -> None:
    with patch.object(sys, "argv", ["publish_artifacts.py", "--book-root", str(book_root), *args]):
        try:
            publish_artifacts.main()
        except SystemExit as error:
            if expect_failure:
                assert error.code == 1
                return
            raise
        if expect_failure:
            raise AssertionError("publish_artifacts.py unexpectedly succeeded")


def minimal_export_fingerprint(
    book_root: Path,
    *,
    visual_profile: dict | None = None,
) -> dict:
    metadata_root = book_root / "metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "book_map": metadata_root / "book-map.json",
        "text_ledger": metadata_root / "text-ledger.json",
        "assets_manifest": metadata_root / "assets-manifest.json",
        "epub_manifest": metadata_root / "epub-manifest.json",
    }
    for name, path in paths.items():
        if not path.exists():
            path.write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
    payload = export_fingerprint_payload(
        "epub",
        book_root,
        paths["epub_manifest"],
        paths["assets_manifest"],
        paths["book_map"],
        paths["text_ledger"],
        {"schema_version": "1.0"},
        {"title": "Book"},
        "pt-BR",
        "original",
        "original",
        [],
        {},
        visual_profile,
    )
    return export_input_fingerprint(payload)


def test_epub_rebuilds_are_byte_identical_and_deterministic() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-epub-idem-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        first = book_root / "exports" / "epub" / "first.epub"
        second = book_root / "exports" / "epub" / "second.epub"

        for output in (first, second):
            run(
                str(ROOT / "export_epub.py"),
                "--book-root",
                str(book_root),
                "--epub-manifest",
                str(manifest_path),
                "--output",
                str(output),
            )

        assert first.read_bytes() == second.read_bytes()
        with zipfile.ZipFile(first) as archive:
            assert {info.date_time for info in archive.infolist()} == {ZIP_TIMESTAMP}
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert f"<meta property=\"dcterms:modified\">{EPUB_MODIFIED}</meta>" in opf
        sidecar = json.loads(first.with_suffix(".epub.json").read_text(encoding="utf-8"))
        assert sidecar["epub_sha256"] == sha256_file(first)
        assert len(sidecar["input_fingerprint"]["value"]) == 64


def test_epub_export_noop_preserves_mtimes() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-export-noop-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        epub = book_root / "exports" / "epub" / "reader.epub"

        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--output",
            str(epub),
        )
        tracked = [epub, epub.with_suffix(".epub.json")]
        for path in tracked:
            set_fixed_mtime(path)
        before = mtimes(tracked)

        epub_noop = run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--output",
            str(epub),
        )

        assert "Up to date" in epub_noop.stdout
        assert mtimes(tracked) == before


def test_pdf_export_noop_preserves_mtimes() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pdf-noop-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        pdf = book_root / "exports" / "pdf" / "reader.pdf"
        assets_manifest_path = book_root / "metadata" / "assets-manifest.json"
        (
            book_map,
            ledger,
            assets_manifest,
            manifest,
            map_path,
            ledger_path,
            translation_ledger,
            revision_ledger,
            _fluid_style,
            fluid_ledger,
            layout,
        ) = export_pdf.load_export_context(
            book_root,
            manifest_path,
            assets_manifest_path,
            "original",
        )
        documents, asset_by_id = export_pdf.validate_documents(
            book_root,
            manifest,
            assets_manifest,
            ledger,
            "original",
            translation_ledger,
            revision_ledger,
            fluid_ledger,
            layout,
        )
        selected_assets_by_document = {
            document["id"]: [
                export_pdf.selected_asset(asset_by_id[asset_id], book_root, "original")
                for asset_id in document["asset_ids"]
            ]
            for document in documents
        }
        visual_profile = export_pdf.normalize_visual_profile(manifest.get("visual_profile"))
        book = export_pdf.book_metadata(book_map, manifest)
        try:
            renderer = export_pdf.current_renderer()
        except RuntimeError as error:
            raise SkipTest(str(error)) from error
        fingerprint = export_input_fingerprint(
            export_fingerprint_payload(
                "pdf",
                book_root,
                manifest_path,
                assets_manifest_path,
                map_path,
                ledger_path,
                manifest,
                book,
                str(manifest["language"]),
                "original",
                "original",
                documents,
                selected_assets_by_document,
                visual_profile,
                renderer,
            )
        )
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\n% cached fixture\n")
        export_pdf.write_json(
            pdf.with_suffix(".pdf.json"),
            {
                "schema_version": "1.0",
                "pdf_path": pdf.relative_to(book_root).as_posix(),
                "pdf_sha256": sha256_file(pdf),
                "input_fingerprint": fingerprint,
                "renderer": renderer,
            },
        )
        tracked = [pdf, pdf.with_suffix(".pdf.json")]
        for path in tracked:
            set_fixed_mtime(path)
        before = mtimes(tracked)

        pdf_noop = run(
            str(ROOT / "export_pdf.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--output",
            str(pdf),
        )

        assert "Up to date" in pdf_noop.stdout
        assert mtimes(tracked) == before


def test_export_contract_revision_changes_fingerprint_and_invalidates_cache() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-contract-fingerprint-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        old_fingerprint = minimal_export_fingerprint(book_root)
        with patch.object(export_epub, "EXPORT_RENDER_CONTRACT_REVISION", "reader-export-render-test"):
            new_fingerprint = minimal_export_fingerprint(book_root)
        assert old_fingerprint != new_fingerprint

        output = book_root / "exports" / "epub" / "reader.epub"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"epub")
        sidecar = output.with_suffix(".epub.json")
        export_epub.write_json(
            sidecar,
            {
                "epub_path": output.relative_to(book_root).as_posix(),
                "epub_sha256": sha256_file(output),
                "input_fingerprint": old_fingerprint,
            },
        )

        assert not cached_export_is_current(
            output,
            sidecar,
            book_root,
            "epub_path",
            "epub_sha256",
            new_fingerprint,
        )


def test_presentation_resource_changes_fingerprint_and_invalidates_cache() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-resource-fingerprint-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        resource = Path(raw_root) / "font.ttf"
        resource.write_bytes(b"font v1")

        def fake_profile_resources(_profile: dict | None) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    identifier="font",
                    source_path=resource,
                    epub_path="fonts/font.ttf",
                    media_type="font/ttf",
                    sha256=sha256_file(resource),
                )
            ]

        with patch.object(export_epub, "profile_resources", fake_profile_resources):
            old_fingerprint = minimal_export_fingerprint(book_root, visual_profile={"name": "test"})
            resource.write_bytes(b"font v2")
            new_fingerprint = minimal_export_fingerprint(book_root, visual_profile={"name": "test"})
        assert old_fingerprint != new_fingerprint

        output = book_root / "exports" / "epub" / "reader.epub"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"epub")
        sidecar = output.with_suffix(".epub.json")
        export_epub.write_json(
            sidecar,
            {
                "epub_path": output.relative_to(book_root).as_posix(),
                "epub_sha256": sha256_file(output),
                "input_fingerprint": old_fingerprint,
            },
        )
        assert not cached_export_is_current(
            output,
            sidecar,
            book_root,
            "epub_path",
            "epub_sha256",
            new_fingerprint,
        )


def test_publish_noop_preserves_artifact_sidecar_and_manifest_mtimes() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-noop-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        epub = book_root / "exports" / "epub" / "reader.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(book_root),
            "--epub-manifest",
            str(manifest_path),
            "--output",
            str(epub),
        )
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(book_root),
            "--epub",
            str(epub),
        )

        destination = book_root / f"{book_root.name}.epub"
        sidecar = epub.with_suffix(".epub.json")
        publication_manifest = book_root / "metadata" / "publication-manifest.json"
        tracked = [destination, sidecar, publication_manifest]
        for path in tracked:
            set_fixed_mtime(path)
        before = mtimes(tracked)
        before_manifest = json.loads(publication_manifest.read_text(encoding="utf-8"))
        before_sidecar = json.loads(sidecar.read_text(encoding="utf-8"))

        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(book_root),
            "--epub",
            str(epub),
        )

        assert mtimes(tracked) == before
        assert json.loads(publication_manifest.read_text(encoding="utf-8")) == before_manifest
        assert json.loads(sidecar.read_text(encoding="utf-8")) == before_sidecar


def test_reader_pair_fans_out_exports_then_validators() -> None:
    calls: list[tuple[str, list[list[str]]]] = []
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-fanout-") as raw_root:
        snapshots = iter(["frozen", "frozen", "frozen", "frozen"])
        book_root = Path(raw_root) / "book"
        book_root.mkdir()

        def fake_run_commands(commands: list[list[str]], label: str) -> None:
            calls.append((label, commands))
            if label == "export":
                for command in commands:
                    fake_reader_pair_export_command(command)

        with (
            patch.object(
                export_reader_pair,
                "resolve_book_paths",
                lambda path: SimpleNamespace(public_root=book_root, assembly_root=book_root),
            ),
            patch.object(
                export_reader_pair,
                "input_snapshot",
                lambda _root: next(snapshots),
            ),
            patch.object(export_reader_pair, "run_commands", fake_run_commands),
            patch.object(sys, "argv", [
                "export_reader_pair.py",
                "--book-root",
                str(book_root),
                "--epub-output",
                str(book_root / "out.epub"),
                "--pdf-output",
                str(book_root / "out.pdf"),
            ]),
        ):
            export_reader_pair.main()

    assert [label for label, _commands in calls] == ["export", "validation"]
    assert [len(commands) for _label, commands in calls] == [2, 2]
    assert "export_epub.py" in calls[0][1][0][1]
    assert "export_pdf.py" in calls[0][1][1][1]
    assert "validate_epub_export.py" in calls[1][1][0][1]
    assert "validate_pdf_export.py" in calls[1][1][1][1]


def test_reader_pair_fails_when_one_parallel_branch_fails() -> None:
    def fake_run(command: list[str], text: bool, capture_output: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1 if command[0] == "bad" else 0,
            stdout="",
            stderr="boom" if command[0] == "bad" else "",
        )

    with patch.object(export_reader_pair.subprocess, "run", fake_run):
        try:
            export_reader_pair.run_commands([["ok"], ["bad"]], "export")
        except RuntimeError as error:
            assert "export branch failed" in str(error)
            assert "boom" in str(error)
        else:
            raise AssertionError("parallel branch failure did not fail the reader pair")


def test_reader_pair_detects_drift_after_validation() -> None:
    calls: list[str] = []
    snapshots = iter(["frozen", "frozen", "changed"])
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-validation-drift-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()

        def fake_run_commands(commands: list[list[str]], label: str) -> None:
            calls.append(label)
            if label == "export":
                for command in commands:
                    fake_reader_pair_export_command(command)

        with (
            patch.object(
                export_reader_pair,
                "resolve_book_paths",
                lambda path: SimpleNamespace(public_root=book_root, assembly_root=book_root),
            ),
            patch.object(
                export_reader_pair,
                "input_snapshot",
                lambda _root: next(snapshots),
            ),
            patch.object(export_reader_pair, "run_commands", fake_run_commands),
            patch.object(sys, "argv", [
                "export_reader_pair.py",
                "--book-root",
                str(book_root),
                "--epub-output",
                str(book_root / "out.epub"),
                "--pdf-output",
                str(book_root / "out.pdf"),
            ]),
        ):
            try:
                export_reader_pair.main()
            except SystemExit as error:
                assert error.code == 1
            else:
                raise AssertionError("reader pair did not fail after validation drift")
    assert calls == ["export", "validation"]


def test_reader_pair_detects_drift_immediately_before_promotion() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-promotion-drift-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"

        try:
            run_reader_pair_with_fakes(
                book_root,
                epub,
                pdf,
                snapshots=["frozen", "frozen", "frozen", "changed"],
            )
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("reader pair did not fail after pre-promotion drift")

        assert not epub.exists()
        assert not pdf.exists()
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_branch_failure_keeps_existing_outputs_epub_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-epub-fail-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        for path, data in (
            (epub, b"old epub"),
            (pdf, b"old pdf"),
            (epub.with_suffix(".epub.json"), b"old epub sidecar"),
            (pdf.with_suffix(".pdf.json"), b"old pdf sidecar"),
        ):
            path.write_bytes(data)
        before = bytes_snapshot([epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")])

        try:
            run_reader_pair_with_fakes(
                book_root,
                epub,
                pdf,
                failing_label="export",
                fail_epub_export=True,
            )
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("reader pair succeeded after EPUB export failure")

        assert bytes_snapshot(list(before)) == before
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_branch_failure_keeps_existing_outputs_pdf_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-pdf-fail-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        for path, data in (
            (epub, b"old epub"),
            (pdf, b"old pdf"),
            (epub.with_suffix(".epub.json"), b"old epub sidecar"),
            (pdf.with_suffix(".pdf.json"), b"old pdf sidecar"),
        ):
            path.write_bytes(data)
        before = bytes_snapshot([epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")])

        try:
            run_reader_pair_with_fakes(
                book_root,
                epub,
                pdf,
                failing_label="export",
                fail_pdf_export=True,
            )
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("reader pair succeeded after PDF export failure")

        assert bytes_snapshot(list(before)) == before
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_validator_failure_keeps_existing_outputs() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-validator-fail-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        for path, data in (
            (epub, b"old epub"),
            (pdf, b"old pdf"),
            (epub.with_suffix(".epub.json"), b"old epub sidecar"),
            (pdf.with_suffix(".pdf.json"), b"old pdf sidecar"),
        ):
            path.write_bytes(data)
        before = bytes_snapshot([epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")])

        try:
            run_reader_pair_with_fakes(book_root, epub, pdf, failing_label="validation")
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("reader pair succeeded after validation failure")

        assert bytes_snapshot(list(before)) == before
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_export_drift_keeps_existing_outputs() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-drift-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        for path, data in (
            (epub, b"old epub"),
            (pdf, b"old pdf"),
            (epub.with_suffix(".epub.json"), b"old epub sidecar"),
            (pdf.with_suffix(".pdf.json"), b"old pdf sidecar"),
        ):
            path.write_bytes(data)
        before = bytes_snapshot([epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")])

        try:
            run_reader_pair_with_fakes(
                book_root,
                epub,
                pdf,
                snapshots=["frozen", "changed"],
            )
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("reader pair succeeded after input drift")

        assert bytes_snapshot(list(before)) == before
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_promotion_rollback_restores_existing_outputs() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-rollback-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        tracked = [epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")]
        for path, data in zip(tracked, [b"old epub", b"old pdf", b"old epub sidecar", b"old pdf sidecar"]):
            path.write_bytes(data)
        before = bytes_snapshot(tracked)
        real_replace = export_reader_pair.os.replace
        calls = 0

        def flaky_replace(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("simulated replace failure")
            real_replace(source, destination)

        with patch.object(export_reader_pair.os, "replace", flaky_replace):
            try:
                run_reader_pair_with_fakes(book_root, epub, pdf)
            except SystemExit as error:
                assert error.code == 1
            else:
                raise AssertionError("reader pair succeeded after promotion failure")

        assert bytes_snapshot(tracked) == before
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_valid_matched_noop_preserves_existing_outputs() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-noop-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        run_reader_pair_with_fakes(book_root, epub, pdf)
        tracked = [epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")]
        for path in tracked:
            set_fixed_mtime(path)
        before_bytes = bytes_snapshot(tracked)
        before_mtimes = mtimes(tracked)

        run_reader_pair_with_fakes(book_root, epub, pdf)

        assert bytes_snapshot(tracked) == before_bytes
        assert mtimes(tracked) == before_mtimes
        assert json.loads(epub.with_suffix(".epub.json").read_text(encoding="utf-8"))["epub_path"] == "out.epub"
        assert json.loads(pdf.with_suffix(".pdf.json").read_text(encoding="utf-8"))["pdf_path"] == "out.pdf"
        assert_no_reader_pair_residue(book_root)


def test_reader_pair_seeds_staged_cache_so_export_branches_can_noop() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-pair-seeded-cache-") as raw_root:
        book_root = Path(raw_root) / "book"
        book_root.mkdir()
        epub = book_root / "out.epub"
        pdf = book_root / "out.pdf"
        epub.write_bytes(b"stable epub")
        pdf.write_bytes(b"stable pdf")
        epub.with_suffix(".epub.json").write_bytes(
            export_reader_pair.json_bytes(
                {
                    "schema_version": "1.0",
                    "epub_path": "out.epub",
                    "epub_sha256": sha256_file(epub),
                    "input_fingerprint": {"value": "e" * 64},
                    "image_edition": "original",
                    "text_edition": "original",
                }
            )
        )
        pdf.with_suffix(".pdf.json").write_bytes(
            export_reader_pair.json_bytes(
                {
                    "schema_version": "1.0",
                    "pdf_path": "out.pdf",
                    "pdf_sha256": sha256_file(pdf),
                    "input_fingerprint": {"value": "p" * 64},
                    "image_edition": "original",
                    "text_edition": "original",
                }
            )
        )
        tracked = [epub, pdf, epub.with_suffix(".epub.json"), pdf.with_suffix(".pdf.json")]
        for path in tracked:
            set_fixed_mtime(path)
        before_bytes = bytes_snapshot(tracked)
        before_mtimes = mtimes(tracked)
        observed_seeded_paths: list[Path] = []
        snapshots = iter(["frozen", "frozen", "frozen", "frozen"])

        def fake_run_commands(commands: list[list[str]], label: str) -> None:
            if label != "export":
                return
            for command in commands:
                output = Path(command[command.index("--output") + 1])
                path_key = "epub_path" if output.suffix == ".epub" else "pdf_path"
                hash_key = "epub_sha256" if output.suffix == ".epub" else "pdf_sha256"
                sidecar = output.with_suffix(f"{output.suffix}.json")
                assert output.is_file(), "export branch did not receive seeded artifact"
                assert sidecar.is_file(), "export branch did not receive seeded sidecar"
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                assert data[path_key] == output.resolve().relative_to(book_root.resolve()).as_posix()
                assert data[hash_key] == sha256_file(output)
                observed_seeded_paths.append(output)
                # Deliberately do not write: if seeding is absent, final staging checks fail.

        with (
            patch.object(
                export_reader_pair,
                "resolve_book_paths",
                lambda path: SimpleNamespace(public_root=book_root, assembly_root=book_root),
            ),
            patch.object(
                export_reader_pair,
                "input_snapshot",
                lambda _root: next(snapshots),
            ),
            patch.object(export_reader_pair, "run_commands", fake_run_commands),
            patch.object(sys, "argv", [
                "export_reader_pair.py",
                "--book-root",
                str(book_root),
                "--epub-output",
                str(epub),
                "--pdf-output",
                str(pdf),
            ]),
        ):
            export_reader_pair.main()

        assert len(observed_seeded_paths) == 2
        assert all(".pair-stage" in path.name for path in observed_seeded_paths)
        assert bytes_snapshot(tracked) == before_bytes
        assert mtimes(tracked) == before_mtimes
        assert_no_reader_pair_residue(book_root)


def test_book_transaction_lock_serializes_competing_processes_and_releases_killed_holder() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-book-lock-") as raw_root:
        book_root = Path(raw_root) / "book"
        (book_root / "metadata").mkdir(parents=True)
        (book_root / "metadata" / "book-map.json").write_text("{}\n", encoding="utf-8")
        scripts_root = Path(__file__).resolve().parent
        assert export_reader_pair.BookTransactionLock(book_root).path == publish_artifacts.BookTransactionLock(book_root).path
        assert export_reader_pair.BookTransactionLock(book_root).path == book_root.resolve() / LOCK_RELATIVE_PATH

        blocker_ready = book_root / "blocker-ready"
        contender_ready = book_root / "contender-ready"
        blocker_code = f"""
import sys
import time
from pathlib import Path
sys.path.insert(0, {str(scripts_root)!r})
from book_transaction_lock import BookTransactionLock
book = Path(sys.argv[1])
with BookTransactionLock(book):
    (book / "blocker-ready").write_text("ready", encoding="utf-8")
    while True:
        time.sleep(0.1)
"""
        contender_code = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(scripts_root)!r})
from book_transaction_lock import BookTransactionLock
book = Path(sys.argv[1])
with BookTransactionLock(book):
    (book / "contender-ready").write_text("ready", encoding="utf-8")
"""
        blocker = subprocess.Popen([sys.executable, "-c", blocker_code, str(book_root)])
        try:
            deadline = time.time() + 5
            while not blocker_ready.exists() and time.time() < deadline:
                time.sleep(0.02)
            assert blocker_ready.exists(), "blocking process did not acquire the book lock"
            contender = subprocess.Popen([sys.executable, "-c", contender_code, str(book_root)])
            try:
                time.sleep(0.25)
                assert not contender_ready.exists(), "contender acquired the lock before release"
                blocker.kill()
                assert blocker.wait(timeout=5) != 0
                assert contender.wait(timeout=5) == 0
            finally:
                if contender.poll() is None:
                    contender.kill()
                    contender.wait(timeout=5)
        finally:
            if blocker.poll() is None:
                blocker.kill()
                blocker.wait(timeout=5)
        assert contender_ready.exists()
        assert book_lock_path(book_root).is_file()
        before = export_reader_pair.input_snapshot(book_root)
        book_lock_path(book_root).write_bytes(b"changed lock file content")
        assert export_reader_pair.input_snapshot(book_root) == before


def test_publish_rejects_mismatched_text_editions_for_reader_pair() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-mismatch-text-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        metadata_root = assembly_root / "metadata"
        metadata_root.mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        write_reader_sidecar(epub, "epub")
        write_reader_sidecar(pdf, "pdf", text_edition="revised-pt-br")

        run_publish(public_root, "--epub", str(epub), "--pdf", str(pdf), expect_failure=True)


def test_publish_rejects_mismatched_image_editions_for_reader_pair() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-mismatch-image-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        (assembly_root / "metadata").mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        write_reader_sidecar(epub, "epub")
        write_reader_sidecar(pdf, "pdf", image_edition="approved-restored")

        run_publish(public_root, "--epub", str(epub), "--pdf", str(pdf), expect_failure=True)


def test_publish_rejects_mismatched_lineage_for_reader_pair() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-mismatch-lineage-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        (assembly_root / "metadata").mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        write_reader_sidecar(epub, "epub")
        write_reader_sidecar(pdf, "pdf", book_map_sha256="c" * 64)

        run_publish(public_root, "--epub", str(epub), "--pdf", str(pdf), expect_failure=True)


def test_publish_rejects_new_sidecar_missing_common_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-missing-lineage-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")
        sidecar_path = epub.with_suffix(".epub.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar.pop("language")
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

        run_publish(public_root, "--epub", str(epub), expect_failure=True)


def test_publish_rejects_new_sidecar_stale_common_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-stale-lineage-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub", text_ledger_sha256="0" * 64)

        run_publish(public_root, "--epub", str(epub), expect_failure=True)


def test_publish_rejects_new_sidecar_language_different_from_manifest() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-wrong-language-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        metadata_root = assembly_root / "metadata"
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")
        manifest_path = metadata_root / "epub-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["language"] = "en-US"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        run_publish(public_root, "--epub", str(epub), expect_failure=True)


def test_publish_rejects_new_sidecar_missing_edition_specific_lineage() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-missing-edition-lineage-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        metadata_root = assembly_root / "metadata"
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub", text_edition="revised-pt-br")
        (metadata_root / "revision-ledger.json").write_text("{}\n", encoding="utf-8")
        hashes = ensure_publication_metadata(assembly_root)
        (metadata_root / "epub-manifest.revised.json").write_text(
            json.dumps({"schema_version": "1.0", "language": "pt-BR", **hashes}, indent=2)
            + "\n",
            encoding="utf-8",
        )

        run_publish(public_root, "--epub", str(epub), expect_failure=True)


def test_publish_rejects_unsafe_one_sided_new_layout_update() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-one-sided-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        metadata_root = assembly_root / "metadata"
        metadata_root.mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")
        (public_root / "Book.pdf").write_bytes(b"old pdf")
        (metadata_root / "publication-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "published_at": "2000-01-01T00:00:00+00:00",
                    "artifacts": {
                        "pdf": {
                            "path": "Book.pdf",
                            "path_root": "book",
                            "sha256": sha256_file(public_root / "Book.pdf"),
                            "source_path": "exports/pdf/old.pdf",
                            "source_path_root": "assembly",
                            "source_sha256": sha256_file(public_root / "Book.pdf"),
                            "text_edition": "original",
                            "image_edition": "approved-restored",
                            "reader_pair_identity": {
                                "text_edition": "original",
                                "image_edition": "approved-restored",
                                "language": "pt-BR",
                            },
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        run_publish(public_root, "--epub", str(epub), expect_failure=True)


def test_publish_preserves_legacy_one_sided_new_layout_without_counterpart_record() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-one-sided-missing-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        (assembly_root / "metadata").mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")

        run_publish(public_root, "--epub", str(epub))
        assert (public_root / "Book.epub").read_bytes() == epub.read_bytes()


def test_publish_accepts_one_sided_update_with_matching_legacy_counterpart_identity() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-one-sided-legacy-match-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        metadata_root = assembly_root / "metadata"
        metadata_root.mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        write_reader_sidecar(epub, "epub")
        pdf_destination = public_root / "Book.pdf"
        pdf_destination.write_bytes(b"legacy pdf")
        (metadata_root / "publication-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "published_at": "2000-01-01T00:00:00+00:00",
                    "artifacts": {
                        "pdf": {
                            "path": "Book.pdf",
                            "path_root": "book",
                            "sha256": sha256_file(pdf_destination),
                            "source_path": "exports/pdf/legacy.pdf",
                            "source_path_root": "assembly",
                            "source_sha256": sha256_file(pdf_destination),
                            "text_edition": "original",
                            "image_edition": "original",
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        run_publish(public_root, "--epub", str(epub))
        assert (public_root / "Book.epub").read_bytes() == epub.read_bytes()


def test_publish_accepts_valid_matched_reader_pair_and_noop() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-publish-valid-pair-") as raw_root:
        public_root = Path(raw_root) / "Book"
        assembly_root = public_root / "assembly"
        (assembly_root / "metadata").mkdir(parents=True)
        epub = assembly_root / "exports" / "epub" / "reader.epub"
        pdf = assembly_root / "exports" / "pdf" / "reader.pdf"
        write_reader_sidecar(epub, "epub")
        write_reader_sidecar(pdf, "pdf")

        run_publish(public_root, "--epub", str(epub), "--pdf", str(pdf))
        tracked = [
            public_root / "Book.epub",
            public_root / "Book.pdf",
            epub.with_suffix(".epub.json"),
            pdf.with_suffix(".pdf.json"),
            assembly_root / "metadata" / "publication-manifest.json",
        ]
        for path in tracked:
            set_fixed_mtime(path)
        before_mtimes = mtimes(tracked)
        before_bytes = bytes_snapshot(tracked)

        run_publish(public_root, "--epub", str(epub), "--pdf", str(pdf))

        assert mtimes(tracked) == before_mtimes
        assert bytes_snapshot(tracked) == before_bytes
        manifest = json.loads(tracked[-1].read_text(encoding="utf-8"))
        assert manifest["artifacts"]["epub"]["reader_pair_identity"] == manifest["artifacts"]["pdf"]["reader_pair_identity"]


def run_tests() -> None:
    tests = [
        test_epub_rebuilds_are_byte_identical_and_deterministic,
        test_epub_export_noop_preserves_mtimes,
        test_pdf_export_noop_preserves_mtimes,
        test_export_contract_revision_changes_fingerprint_and_invalidates_cache,
        test_presentation_resource_changes_fingerprint_and_invalidates_cache,
        test_publish_noop_preserves_artifact_sidecar_and_manifest_mtimes,
        test_reader_pair_fans_out_exports_then_validators,
        test_reader_pair_fails_when_one_parallel_branch_fails,
        test_reader_pair_detects_drift_after_validation,
        test_reader_pair_detects_drift_immediately_before_promotion,
        test_reader_pair_branch_failure_keeps_existing_outputs_epub_failed,
        test_reader_pair_branch_failure_keeps_existing_outputs_pdf_failed,
        test_reader_pair_validator_failure_keeps_existing_outputs,
        test_reader_pair_export_drift_keeps_existing_outputs,
        test_reader_pair_promotion_rollback_restores_existing_outputs,
        test_reader_pair_valid_matched_noop_preserves_existing_outputs,
        test_reader_pair_seeds_staged_cache_so_export_branches_can_noop,
        test_book_transaction_lock_serializes_competing_processes_and_releases_killed_holder,
        test_publish_rejects_mismatched_text_editions_for_reader_pair,
        test_publish_rejects_mismatched_image_editions_for_reader_pair,
        test_publish_rejects_mismatched_lineage_for_reader_pair,
        test_publish_rejects_new_sidecar_missing_common_lineage,
        test_publish_rejects_new_sidecar_stale_common_lineage,
        test_publish_rejects_new_sidecar_language_different_from_manifest,
        test_publish_rejects_new_sidecar_missing_edition_specific_lineage,
        test_publish_rejects_unsafe_one_sided_new_layout_update,
        test_publish_preserves_legacy_one_sided_new_layout_without_counterpart_record,
        test_publish_accepts_one_sided_update_with_matching_legacy_counterpart_identity,
        test_publish_accepts_valid_matched_reader_pair_and_noop,
    ]
    failures = 0
    skipped = 0
    for test in tests:
        try:
            test()
        except SkipTest as error:
            skipped += 1
            print(f"SKIP {test.__name__}: {error}")
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}", file=sys.stderr)
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    if failures:
        raise SystemExit(1)
    print(f"export idempotence tests passed ({len(tests) - skipped} run, {skipped} skipped)")


if __name__ == "__main__":
    run_tests()
