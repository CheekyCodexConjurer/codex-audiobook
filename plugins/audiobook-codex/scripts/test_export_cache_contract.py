from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import SkipTest
from unittest.mock import patch

import export_epub
import export_pdf
from export_epub import cached_export_is_current
from test_pdf_export import ROOT, build_semantic_pdf_fixture, run, sha256_file


FIXED_ARTIFACT_MTIME_NS = 1_710_000_000_987_654_321


def set_artifact_mtime(path: Path) -> None:
    os.utime(path, ns=(FIXED_ARTIFACT_MTIME_NS, FIXED_ARTIFACT_MTIME_NS))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assert_artifact_preserved(
    artifact: Path,
    before_bytes: bytes,
    before_mtime_ns: int,
) -> None:
    assert artifact.read_bytes() == before_bytes
    assert artifact.stat().st_mtime_ns == before_mtime_ns


def export_command(kind: str, book_root: Path, manifest_path: Path, output: Path) -> list[str]:
    script = "export_epub.py" if kind == "epub" else "export_pdf.py"
    return [
        str(ROOT / script),
        "--book-root",
        str(book_root),
        "--epub-manifest",
        str(manifest_path),
        "--output",
        str(output),
    ]


def repair_after_sidecar_mutation(
    kind: str,
    book_root: Path,
    manifest_path: Path,
    artifact: Path,
    key: str,
    replacement: object,
) -> None:
    sidecar = artifact.with_suffix(f".{kind}.json")
    expected_sidecar = read_json(sidecar)
    for missing in (True, False):
        mutated = dict(expected_sidecar)
        if missing:
            mutated.pop(key)
        else:
            mutated[key] = replacement
        write_json(sidecar, mutated)
        set_artifact_mtime(artifact)
        before_bytes = artifact.read_bytes()
        before_mtime_ns = artifact.stat().st_mtime_ns

        completed = run(*export_command(kind, book_root, manifest_path, artifact))

        assert "Up to date" in completed.stdout
        assert_artifact_preserved(artifact, before_bytes, before_mtime_ns)
        assert read_json(sidecar) == expected_sidecar


def test_epub_sidecar_contract_repairs_semantic_fields_without_rewriting_artifact() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-epub-cache-contract-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        artifact = book_root / "exports" / "epub" / "reader.epub"
        run(*export_command("epub", book_root, manifest_path, artifact))

        for key, replacement in (
            ("language", "en-US"),
            ("text_edition", "translated-pt-br"),
            ("book_map_sha256", "0" * 64),
            ("layout", {"mode": "semantic", "path": "metadata/other.json", "sha256": "0" * 64}),
        ):
            repair_after_sidecar_mutation(
                "epub",
                book_root,
                manifest_path,
                artifact,
                key,
                replacement,
            )


def test_pdf_sidecar_contract_repairs_semantic_fields_without_rewriting_artifact() -> None:
    try:
        export_pdf.current_renderer()
    except RuntimeError as error:
        raise SkipTest(str(error)) from error
    with tempfile.TemporaryDirectory(prefix="audiobook-pdf-cache-contract-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        artifact = book_root / "exports" / "pdf" / "reader.pdf"
        run(*export_command("pdf", book_root, manifest_path, artifact))

        for key, replacement in (
            ("language", "en-US"),
            ("image_edition", "approved-restored"),
            ("book_map_sha256", "0" * 64),
            ("layout", {"mode": "semantic", "path": "metadata/other.json", "sha256": "0" * 64}),
            ("renderer", {"name": "other", "version": "0"}),
        ):
            repair_after_sidecar_mutation(
                "pdf",
                book_root,
                manifest_path,
                artifact,
                key,
                replacement,
            )


def test_epub_sidecar_absent_or_truncated_forces_cache_miss_and_reexport() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-epub-cache-sidecar-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        artifact = book_root / "exports" / "epub" / "reader.epub"
        sidecar = artifact.with_suffix(".epub.json")
        run(*export_command("epub", book_root, manifest_path, artifact))

        for raw_sidecar in (None, b'{"schema_version": "1.0"'):
            artifact.write_bytes(b"obsolete arbitrary epub")
            if raw_sidecar is None:
                if sidecar.exists():
                    sidecar.unlink()
            else:
                sidecar.write_bytes(raw_sidecar)
            set_artifact_mtime(artifact)
            before_bytes = artifact.read_bytes()
            before_mtime_ns = artifact.stat().st_mtime_ns

            completed = run(*export_command("epub", book_root, manifest_path, artifact))

            assert "Created" in completed.stdout
            assert artifact.read_bytes() != before_bytes
            assert artifact.stat().st_mtime_ns != before_mtime_ns
            repaired_sidecar = read_json(sidecar)
            assert repaired_sidecar["epub_path"] == artifact.relative_to(book_root).as_posix()
            assert repaired_sidecar["epub_sha256"] == sha256_file(artifact)


def test_epub_sidecar_missing_identity_forces_cache_miss_and_reexport() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-epub-cache-identity-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        manifest_path = build_semantic_pdf_fixture(book_root)
        artifact = book_root / "exports" / "epub" / "reader.epub"
        sidecar = artifact.with_suffix(".epub.json")
        run(*export_command("epub", book_root, manifest_path, artifact))
        original_sidecar = read_json(sidecar)

        for identity_key in ("epub_path", "epub_sha256", "input_fingerprint"):
            artifact.write_bytes(b"obsolete arbitrary epub")
            mutated = dict(original_sidecar)
            mutated.pop(identity_key)
            write_json(sidecar, mutated)
            set_artifact_mtime(artifact)
            before_bytes = artifact.read_bytes()
            before_mtime_ns = artifact.stat().st_mtime_ns

            completed = run(*export_command("epub", book_root, manifest_path, artifact))

            assert "Created" in completed.stdout
            assert artifact.read_bytes() != before_bytes
            assert artifact.stat().st_mtime_ns != before_mtime_ns
            repaired_sidecar = read_json(sidecar)
            assert repaired_sidecar["epub_path"] == artifact.relative_to(book_root).as_posix()
            assert repaired_sidecar["epub_sha256"] == sha256_file(artifact)
            original_sidecar = repaired_sidecar


def minimal_export_fingerprint(book_root: Path) -> dict:
    metadata_root = book_root / "metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "book_map": metadata_root / "book-map.json",
        "text_ledger": metadata_root / "text-ledger.json",
        "assets_manifest": metadata_root / "assets-manifest.json",
        "epub_manifest": metadata_root / "epub-manifest.json",
    }
    for name, path in paths.items():
        path.write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
    payload = export_epub.export_fingerprint_payload(
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
        None,
    )
    return export_epub.export_input_fingerprint(payload)


def fingerprint_with_changed_module_hash(book_root: Path, module_name: str) -> dict:
    real_sha256_file = export_epub.sha256_file

    def fake_sha256_file(path: Path) -> str:
        if Path(path).name == module_name:
            return "0" * 64
        return real_sha256_file(path)

    with patch.object(export_epub, "sha256_file", fake_sha256_file):
        return minimal_export_fingerprint(book_root)


def test_export_code_modules_are_explicit_and_hash_changes_invalidate_cache() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-code-fingerprint-") as raw_root:
        book_root = Path(raw_root) / "fixture-book"
        metadata_root = book_root / "metadata"
        metadata_root.mkdir(parents=True, exist_ok=True)
        paths = {
            "book_map": metadata_root / "book-map.json",
            "text_ledger": metadata_root / "text-ledger.json",
            "assets_manifest": metadata_root / "assets-manifest.json",
            "epub_manifest": metadata_root / "epub-manifest.json",
        }
        for name, path in paths.items():
            path.write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
        payload = export_epub.export_fingerprint_payload(
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
            None,
        )
        module_names = {entry["path"] for entry in payload["export_contract"]["code"]}
        assert {
            "epub_layout.py",
            "epub_presentation.py",
            "path_safety.py",
            "reader_export_contract.py",
            "verify_text_ledger.py",
            "verify_translation_ledger.py",
            "verify_revision_ledger.py",
            "verify_fluid_edition_ledger.py",
        } <= module_names

        old_fingerprint = export_epub.export_input_fingerprint(payload)
        for module_name in ("epub_layout.py", "verify_text_ledger.py", "path_safety.py"):
            new_fingerprint = fingerprint_with_changed_module_hash(book_root, module_name)
            assert new_fingerprint != old_fingerprint

            output = book_root / "exports" / "epub" / f"{module_name}.epub"
            output.parent.mkdir(parents=True, exist_ok=True)
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
                {
                    "epub_path": output.relative_to(book_root).as_posix(),
                    "epub_sha256": sha256_file(output),
                    "input_fingerprint": new_fingerprint,
                },
            )


def run_tests() -> None:
    tests = [
        test_epub_sidecar_contract_repairs_semantic_fields_without_rewriting_artifact,
        test_pdf_sidecar_contract_repairs_semantic_fields_without_rewriting_artifact,
        test_epub_sidecar_absent_or_truncated_forces_cache_miss_and_reexport,
        test_epub_sidecar_missing_identity_forces_cache_miss_and_reexport,
        test_export_code_modules_are_explicit_and_hash_changes_invalidate_cache,
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
    print(f"export cache contract tests passed ({len(tests) - skipped} run, {skipped} skipped)")


if __name__ == "__main__":
    run_tests()
