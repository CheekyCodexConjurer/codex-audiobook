from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from book_layout import (
    ASSEMBLY_SUBDIRECTORIES,
    BookPaths,
    canonical_book_folder_name,
    ensure_assembly_tree,
    resolve_book_paths,
)
from migrate_library_layout import CONFIRMATION
from preflight import select_book_root, sha256_file
from validate_book_layout import validate_layout


ROOT = Path(__file__).resolve().parent


def run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{script} failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def run_fails(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        raise AssertionError(f"{script} unexpectedly succeeded")
    return completed


def write_book_map(assembly: Path, title: str, year: int, author: str) -> None:
    metadata = assembly / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "book-map.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "book": {
                    "title": title,
                    "subtitle": "",
                    "author": author,
                    "original_publication_year": year,
                    "original_publication_place": "",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def make_new_book(root: Path, title: str, year: int, author: str) -> BookPaths:
    public = root / canonical_book_folder_name(title, year, author)
    paths = BookPaths(public, public / "assembly", "new")
    ensure_assembly_tree(paths)
    write_book_map(paths.assembly_root, title, year, author)
    return paths


def make_legacy_book(
    root: Path,
    folder_name: str,
    title: str,
    year: int,
    author: str,
) -> Path:
    legacy = root / folder_name
    for name in ASSEMBLY_SUBDIRECTORIES:
        (legacy / name).mkdir(parents=True, exist_ok=True)
    write_book_map(legacy, title, year, author)
    return legacy


def publication_record(path: str, source_path: str, content_hash: str) -> dict:
    return {
        "path": path,
        "sha256": content_hash,
        "source_path": source_path,
        "source_sha256": content_hash,
        "published_at": "2026-07-17T00:00:00+00:00",
    }


def create_directory_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        if os.name != "nt":
            return False
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Cannot create junction {link} -> {target}: "
            f"{completed.stderr or completed.stdout}"
        )
    return True


def remove_directory_link(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    else:
        link.rmdir()


def main() -> None:
    assert (
        canonical_book_folder_name("Título: teste", 1933, "Autor/Editor")
        == "Título teste - 1933 - Autor Editor"
    )
    with tempfile.TemporaryDirectory(prefix="book-layout-") as temporary:
        root = Path(temporary)
        paths = make_new_book(root, "Livro de Teste", 1933, "Autora Exemplo")
        resolved = resolve_book_paths(paths.public_root)
        assert resolved == paths
        assert set(entry.name for entry in paths.assembly_root.iterdir()) == set(
            ASSEMBLY_SUBDIRECTORIES
        )
        assert validate_layout(paths.public_root, "working") == []

        canonical_records: dict[str, dict] = {}
        for suffix in (".epub", ".pdf", ".mp3"):
            public_file = paths.public_root / f"{paths.public_root.name}{suffix}"
            public_file.write_bytes(suffix.encode("ascii"))
            canonical_records[suffix] = publication_record(
                public_file.name,
                f"exports/{suffix.removeprefix('.')}/canonical{suffix}",
                sha256_file(public_file),
            )
        fluid_records: dict[str, dict] = {}
        for suffix in (".epub", ".pdf"):
            fluid_file = paths.public_root / f"livro-de-teste-fluida{suffix}"
            fluid_file.write_bytes(f"fluid{suffix}".encode("ascii"))
            fluid_records[suffix] = publication_record(
                fluid_file.name,
                f"exports/{suffix.removeprefix('.')}/{fluid_file.name}",
                sha256_file(fluid_file),
            )
            fluid_records[suffix].update(
                {
                    "text_edition": "fluid-pt-br",
                    "image_edition": "original",
                    "path_root": "book",
                }
            )
        publication_manifest_path = (
            paths.assembly_root / "metadata" / "publication-manifest.json"
        )
        publication_manifest = {
            "schema_version": "1.1",
            "artifacts": {
                "audio": canonical_records[".mp3"],
                "epub": canonical_records[".epub"],
                "pdf": canonical_records[".pdf"],
                "epub_editions": {
                    "original:original": canonical_records[".epub"],
                    "fluid-pt-br:original": fluid_records[".epub"],
                },
                "pdf_editions": {
                    "original:original": canonical_records[".pdf"],
                    "fluid-pt-br:original": fluid_records[".pdf"],
                },
            },
        }

        def write_publication_manifest(value: dict) -> None:
            publication_manifest_path.write_text(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        write_publication_manifest(publication_manifest)
        assert validate_layout(paths.public_root, "published") == []

        missing_pair = json.loads(json.dumps(publication_manifest))
        missing_pair["artifacts"]["pdf_editions"].pop("fluid-pt-br:original")
        write_publication_manifest(missing_pair)
        assert any(
            "edition keys must match exactly" in error
            for error in validate_layout(paths.public_root, "published")
        )

        invalid_hash = json.loads(json.dumps(publication_manifest))
        invalid_hash["artifacts"]["epub_editions"]["fluid-pt-br:original"].pop(
            "sha256"
        )
        write_publication_manifest(invalid_hash)
        assert any(
            "sha256 must contain 64 hexadecimal characters" in error
            for error in validate_layout(paths.public_root, "published")
        )

        wrong_collection = json.loads(json.dumps(publication_manifest))
        wrong_collection["artifacts"]["epub_editions"].pop(
            "fluid-pt-br:original"
        )
        wrong_collection["artifacts"]["pdf_editions"][
            "fluid-pt-br:original"
        ] = fluid_records[".epub"]
        write_publication_manifest(wrong_collection)
        wrong_collection_errors = validate_layout(
            paths.public_root,
            "published",
        )
        assert any(
            "root-level .pdf filename" in error
            for error in wrong_collection_errors
        )
        assert any(
            "not a valid fluid publication" in error
            for error in wrong_collection_errors
        )
        write_publication_manifest(publication_manifest)

        extra = paths.public_root / "notes.txt"
        extra.write_text("not allowed", encoding="utf-8")
        assert any(
            "unsupported entries" in error
            for error in validate_layout(paths.public_root, "published")
        )
        extra.unlink()

        untracked_epub = paths.public_root / "untracked.epub"
        untracked_epub.write_bytes(b"untracked")
        assert any(
            "not a valid fluid publication" in error
            for error in validate_layout(paths.public_root, "published")
        )
        untracked_epub.unlink()

        legacy = root / "legacy"
        (legacy / "metadata").mkdir(parents=True)
        assert resolve_book_paths(legacy).layout_kind == "legacy"
        (legacy / "assembly").mkdir()
        try:
            resolve_book_paths(legacy)
        except RuntimeError as error:
            assert "both new and legacy" in str(error)
        else:
            raise AssertionError("Ambiguous layout unexpectedly resolved.")
        (legacy / "assembly").rmdir()

        split = root / "split"
        (split / "assembly").mkdir(parents=True)
        (split / "source").mkdir()
        try:
            resolve_book_paths(split)
        except RuntimeError as error:
            assert "both new and legacy layout directories" in str(error)
        else:
            raise AssertionError("Partially split layout unexpectedly resolved.")

        external_public = root / "external-public"
        (external_public / "assembly").mkdir(parents=True)
        public_link = root / "public-link"
        if create_directory_link(public_link, external_public):
            try:
                resolve_book_paths(public_link)
            except RuntimeError as error:
                assert "Book root must not traverse a reparse point" in str(error)
            else:
                raise AssertionError("Reparse book root unexpectedly resolved.")
            remove_directory_link(public_link)

        assembly_link_root = root / "assembly-link-root"
        assembly_link_root.mkdir()
        external_assembly = root / "external-assembly"
        external_assembly.mkdir()
        assembly_link = assembly_link_root / "assembly"
        if create_directory_link(assembly_link, external_assembly):
            try:
                resolve_book_paths(assembly_link_root)
            except RuntimeError as error:
                assert "reserved entry must not be a reparse point" in str(error)
            else:
                raise AssertionError("Reparse assembly unexpectedly resolved.")
            remove_directory_link(assembly_link)

        dangling_assembly_root = root / "dangling-assembly-root"
        dangling_assembly_root.mkdir()
        dangling_assembly_target = root / "dangling-assembly-target"
        dangling_assembly_target.mkdir()
        dangling_assembly = dangling_assembly_root / "assembly"
        if create_directory_link(dangling_assembly, dangling_assembly_target):
            dangling_assembly_target.rmdir()
            try:
                resolve_book_paths(dangling_assembly_root)
            except RuntimeError as error:
                assert "reserved entry must not be a reparse point" in str(error)
            else:
                raise AssertionError("Dangling assembly unexpectedly resolved.")
            remove_directory_link(dangling_assembly)

        legacy_link_root = root / "legacy-link-root"
        (legacy_link_root / "assembly").mkdir(parents=True)
        external_legacy_source = root / "external-legacy-source"
        external_legacy_source.mkdir()
        legacy_source_link = legacy_link_root / "source"
        if create_directory_link(legacy_source_link, external_legacy_source):
            try:
                resolve_book_paths(legacy_link_root)
            except RuntimeError as error:
                assert "reserved entry must not be a reparse point" in str(error)
            else:
                raise AssertionError("Reparse legacy source unexpectedly resolved.")
            remove_directory_link(legacy_source_link)

        for name in ASSEMBLY_SUBDIRECTORIES:
            child_root = root / f"assembly-child-link-{name}"
            child_assembly = child_root / "assembly"
            child_assembly.mkdir(parents=True)
            for sibling in ASSEMBLY_SUBDIRECTORIES:
                if sibling != name:
                    (child_assembly / sibling).mkdir()
            external_child = root / f"external-assembly-child-{name}"
            external_child.mkdir()
            child_link = child_assembly / name
            if create_directory_link(child_link, external_child):
                try:
                    resolve_book_paths(child_root)
                except RuntimeError as error:
                    assert "Assembly entry must not be a reparse point" in str(error)
                else:
                    raise AssertionError(
                        f"Reparse assembly/{name} unexpectedly resolved."
                    )
                remove_directory_link(child_link)

        dangling_child_root = root / "dangling-assembly-child-root"
        dangling_child_assembly = dangling_child_root / "assembly"
        dangling_child_assembly.mkdir(parents=True)
        for name in ASSEMBLY_SUBDIRECTORIES:
            if name != "source":
                (dangling_child_assembly / name).mkdir()
        dangling_child_target = root / "dangling-assembly-child-target"
        dangling_child_target.mkdir()
        dangling_child_link = dangling_child_assembly / "source"
        if create_directory_link(dangling_child_link, dangling_child_target):
            dangling_child_target.rmdir()
            try:
                resolve_book_paths(dangling_child_root)
            except RuntimeError as error:
                assert "Assembly entry must not be a reparse point" in str(error)
            else:
                raise AssertionError("Dangling assembly/source unexpectedly resolved.")
            remove_directory_link(dangling_child_link)

        legacy_refresh = root / "legacy-refresh-slug"
        (legacy_refresh / "metadata").mkdir(parents=True)
        legacy_source = legacy_refresh / "source" / "original.pdf"
        legacy_source.parent.mkdir()
        legacy_source.write_bytes(b"legacy-source")
        selected = select_book_root(
            legacy_source,
            sha256_file(legacy_source),
            root / "unused-library",
            "Canonical Legacy Title",
            1933,
            "Legacy Author",
            legacy_refresh,
        )
        assert selected.public_root == legacy_refresh.resolve()
        assert selected.layout_kind == "legacy"
        try:
            select_book_root(
                legacy_source,
                sha256_file(legacy_source),
                root / "unused-library",
                "Canonical Legacy Title",
                1933,
                "Legacy Author",
                root / "wrong-new-name",
            )
        except RuntimeError as error:
            assert "canonical folder name" in str(error)
        else:
            raise AssertionError("Noncanonical new explicit root unexpectedly accepted.")

        preflight_external_library = root / "preflight-external-library"
        preflight_external_library.mkdir()
        preflight_library_link = root / "preflight-library-link"
        if create_directory_link(
            preflight_library_link,
            preflight_external_library,
        ):
            try:
                select_book_root(
                    legacy_source,
                    sha256_file(legacy_source),
                    preflight_library_link / "Library",
                    "Canonical Legacy Title",
                    1933,
                    "Legacy Author",
                    None,
                )
            except RuntimeError as error:
                assert "Library root must not traverse a reparse point" in str(error)
            else:
                raise AssertionError("Reparse library ancestor unexpectedly accepted.")
            assert not (preflight_external_library / "Library").exists()
            remove_directory_link(preflight_library_link)

        source_library = root / "old-library"
        target_library = root / "new-library"
        source_library.mkdir()
        legacy_book = make_legacy_book(
            source_library,
            "legacy-book",
            "Livro Migrado",
            2024,
            "Autor Migrante",
        )
        (legacy_book / "source" / "original.pdf").write_bytes(b"source")
        final_artifacts = {
            ".mp3": ("legacy-audiobook.mp3", b"audio", "audio/audiobook.mp3"),
            ".epub": (
                "legacy-reader.epub",
                b"epub",
                "exports/epub/legacy-reader.epub",
            ),
            ".pdf": (
                "legacy-reader.pdf",
                b"pdf",
                "exports/pdf/legacy-reader.pdf",
            ),
        }
        records: dict[str, dict] = {}
        for suffix, (public_name, content, source_path) in final_artifacts.items():
            (legacy_book / public_name).write_bytes(content)
            internal = legacy_book / source_path
            internal.parent.mkdir(parents=True, exist_ok=True)
            internal.write_bytes(content)
            records[suffix] = publication_record(
                public_name,
                source_path,
                sha256_file(internal),
            )
        (legacy_book / "metadata" / "audio-manifest.json").write_text(
            json.dumps({"schema_version": "1.0", "publication": records[".mp3"]})
            + "\n",
            encoding="utf-8",
        )
        (legacy_book / "exports" / "epub" / "legacy-reader.epub.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "epub_path": "exports/epub/legacy-reader.epub",
                    "publication": records[".epub"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (legacy_book / "exports" / "pdf" / "legacy-reader.pdf.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "pdf_path": "exports/pdf/legacy-reader.pdf",
                    "publication": records[".pdf"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        nested_epub_sidecar = (
            legacy_book
            / "exports"
            / "epub"
            / "editions"
            / "legacy-reader.epub.json"
        )
        nested_epub_sidecar.parent.mkdir(parents=True)
        nested_epub_sidecar.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publication": records[".epub"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        nested_pdf_sidecar = (
            legacy_book
            / "exports"
            / "pdf"
            / "editions"
            / "legacy-reader.pdf.json"
        )
        nested_pdf_sidecar.parent.mkdir(parents=True)
        nested_pdf_sidecar.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publication": records[".pdf"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (legacy_book / "metadata" / "publication-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "artifacts": {
                        "audio": records[".mp3"],
                        "epub": records[".epub"],
                        "pdf": records[".pdf"],
                        "epub_editions": {"original:original": records[".epub"]},
                        "pdf_editions": {"original:original": records[".pdf"]},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        restoration_file = legacy_book / "restoration" / "approved" / "restored.png"
        restoration_file.parent.mkdir(parents=True)
        restoration_file.write_bytes(b"restored")
        (legacy_book / "metadata" / "assets-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "assets": [
                        {
                            "restoration": {
                                "approved": {
                                    "path": "restoration/approved/restored.png",
                                    "sha256": sha256_file(restoration_file),
                                }
                            }
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        historical = source_library / "_voice-calibration-approved"
        historical.mkdir()
        unknown = source_library / "unknown"
        unknown.mkdir()

        dry_run = run(
            "migrate_library_layout.py",
            "--source-library",
            str(source_library),
            "--target-library",
            str(target_library),
        )
        assert "DRY RUN: 1 book(s) ready" in dry_run.stdout
        assert "historical calibration evidence" in dry_run.stdout
        assert legacy_book.is_dir()

        run_fails(
            "migrate_library_layout.py",
            "--source-library",
            str(source_library),
            "--target-library",
            str(target_library),
            "--execute",
        )
        run(
            "migrate_library_layout.py",
            "--source-library",
            str(source_library),
            "--target-library",
            str(target_library),
            "--execute",
            "--confirm",
            CONFIRMATION,
        )
        migrated = (
            target_library
            / canonical_book_folder_name("Livro Migrado", 2024, "Autor Migrante")
        )
        assert (migrated / "assembly" / "source" / "original.pdf").read_bytes() == b"source"
        assert (migrated / f"{migrated.name}.mp3").read_bytes() == b"audio"
        assert (migrated / f"{migrated.name}.epub").read_bytes() == b"epub"
        assert (migrated / f"{migrated.name}.pdf").read_bytes() == b"pdf"
        assert validate_layout(migrated, "published") == []
        migrated_assets = json.loads(
            (migrated / "assembly" / "metadata" / "assets-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        approved_path = migrated_assets["assets"][0]["restoration"]["approved"]["path"]
        assert approved_path == "assets/restoration/approved/restored.png"
        assert (migrated / "assembly" / approved_path).read_bytes() == b"restored"
        migrated_publication = json.loads(
            (
                migrated
                / "assembly"
                / "metadata"
                / "publication-manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert migrated_publication["schema_version"] == "1.1"
        for kind, suffix in (("audio", ".mp3"), ("epub", ".epub"), ("pdf", ".pdf")):
            record = migrated_publication["artifacts"][kind]
            assert record["path"] == f"{migrated.name}{suffix}"
            assert record["path_root"] == "book"
            assert record["source_path_root"] == "assembly"
            assert (migrated / record["path"]).is_file()
        for relative_sidecar, suffix in (
            (Path("exports/epub/editions/legacy-reader.epub.json"), ".epub"),
            (Path("exports/pdf/editions/legacy-reader.pdf.json"), ".pdf"),
        ):
            nested_record = json.loads(
                (migrated / "assembly" / relative_sidecar).read_text(encoding="utf-8")
            )["publication"]
            assert nested_record["path"] == f"{migrated.name}{suffix}"
            assert nested_record["path_root"] == "book"
            assert nested_record["source_path_root"] == "assembly"
        assert not legacy_book.exists()
        assert historical.exists()

        collision_source = root / "collision-source"
        collision_target = root / "collision-target"
        collision_source.mkdir()
        duplicate_a = make_legacy_book(
            collision_source,
            "duplicate-a",
            "Same Book",
            2024,
            "Same Author",
        )
        duplicate_b = make_legacy_book(
            collision_source,
            "duplicate-b",
            "Same Book",
            2024,
            "Same Author",
        )
        collision = run_fails(
            "migrate_library_layout.py",
            "--source-library",
            str(collision_source),
            "--target-library",
            str(collision_target),
        )
        assert "duplicate canonical migration target" in collision.stderr
        run_fails(
            "migrate_library_layout.py",
            "--source-library",
            str(collision_source),
            "--target-library",
            str(collision_target),
            "--execute",
            "--confirm",
            CONFIRMATION,
        )
        assert duplicate_a.exists()
        assert duplicate_b.exists()
        assert not collision_target.exists()

        overlap_source = root / "overlap-source"
        overlap_source.mkdir()
        overlap_a = make_legacy_book(
            overlap_source,
            "overlap-a",
            "Overlap A",
            2024,
            "Safe Author",
        )
        overlap_b = make_legacy_book(
            overlap_source,
            "overlap-b",
            "Overlap B",
            2024,
            "Safe Author",
        )
        (overlap_a / "source" / "original.pdf").write_bytes(b"a")
        (overlap_b / "source" / "original.pdf").write_bytes(b"b")
        overlap_target = overlap_b / "assets" / "nested-library"
        overlap_dry_run = run_fails(
            "migrate_library_layout.py",
            "--source-library",
            str(overlap_source),
            "--target-library",
            str(overlap_target),
        )
        assert "Source and target libraries must not overlap" in overlap_dry_run.stderr
        run_fails(
            "migrate_library_layout.py",
            "--source-library",
            str(overlap_source),
            "--target-library",
            str(overlap_target),
            "--execute",
            "--confirm",
            CONFIRMATION,
        )
        assert overlap_a.is_dir()
        assert overlap_b.is_dir()
        assert not overlap_target.exists()

        case_source = root / "case-source"
        case_target = root / "case-target"
        case_source.mkdir()
        case_book = make_legacy_book(
            case_source,
            "case-book",
            "Case Book",
            2024,
            "Case Author",
        )
        (case_book / "source" / "original.pdf").write_bytes(b"source")
        for suffix in (".epub", ".pdf", ".mp3"):
            (
                case_book
                / f"case book - 2024 - case author{suffix}"
            ).write_bytes(suffix.encode("ascii"))
        run(
            "migrate_library_layout.py",
            "--source-library",
            str(case_source),
            "--target-library",
            str(case_target),
            "--execute",
            "--confirm",
            CONFIRMATION,
        )
        canonical_case_book = (
            case_target
            / canonical_book_folder_name("Case Book", 2024, "Case Author")
        )
        public_names = {
            entry.name
            for entry in canonical_case_book.iterdir()
            if entry.name != "assembly"
        }
        assert public_names == {
            f"{canonical_case_book.name}.epub",
            f"{canonical_case_book.name}.pdf",
            f"{canonical_case_book.name}.mp3",
        }
        assert validate_layout(canonical_case_book, "published") == []

        external = root / "external-book"
        external.mkdir()
        sentinel = external / "sentinel.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        linked_library = root / "linked-library"
        linked_library.mkdir()
        linked_book = linked_library / "linked-book"
        if create_directory_link(linked_book, external):
            linked = run(
                "migrate_library_layout.py",
                "--source-library",
                str(linked_library),
                "--target-library",
                str(root / "linked-target"),
            )
            assert "direct non-reparse child" in linked.stdout
            assert sentinel.read_text(encoding="utf-8") == "untouched"
            assert not (root / "linked-target").exists()
            remove_directory_link(linked_book)

        restoration_library = root / "restoration-library"
        restoration_target = root / "restoration-target"
        restoration_library.mkdir()
        safe_book = make_legacy_book(
            restoration_library,
            "safe-book",
            "Safe Book",
            2024,
            "Safe Author",
        )
        (safe_book / "source" / "original.pdf").write_bytes(b"safe")
        conflicting_book = make_legacy_book(
            restoration_library,
            "conflicting-book",
            "Conflicting Book",
            2024,
            "Conflict Author",
        )
        (conflicting_book / "source" / "original.pdf").write_bytes(b"conflict")
        (conflicting_book / "restoration").mkdir()
        (conflicting_book / "assets" / "restoration").mkdir()
        conflict_dry_run = run(
            "migrate_library_layout.py",
            "--source-library",
            str(restoration_library),
            "--target-library",
            str(restoration_target),
        )
        assert "READY" in conflict_dry_run.stdout
        assert (
            "legacy restoration conflicts with assets/restoration"
            in conflict_dry_run.stdout
        )
        run(
            "migrate_library_layout.py",
            "--source-library",
            str(restoration_library),
            "--target-library",
            str(restoration_target),
            "--execute",
            "--confirm",
            CONFIRMATION,
        )
        assert (
            restoration_target
            / canonical_book_folder_name("Safe Book", 2024, "Safe Author")
        ).is_dir()
        assert conflicting_book.is_dir()
        assert (conflicting_book / "restoration").is_dir()
        assert (conflicting_book / "assets" / "restoration").is_dir()

        reparse_restoration_library = root / "reparse-restoration-library"
        reparse_restoration_library.mkdir()
        reparse_book = make_legacy_book(
            reparse_restoration_library,
            "reparse-restoration-book",
            "Reparse Restoration",
            2024,
            "Safe Author",
        )
        (reparse_book / "source" / "original.pdf").write_bytes(b"source")
        external_restoration = root / "external-restoration"
        external_restoration.mkdir()
        external_restoration_sentinel = external_restoration / "sentinel.txt"
        external_restoration_sentinel.write_text("untouched", encoding="utf-8")
        restoration_link = reparse_book / "restoration"
        if create_directory_link(restoration_link, external_restoration):
            reparse_dry_run = run(
                "migrate_library_layout.py",
                "--source-library",
                str(reparse_restoration_library),
                "--target-library",
                str(root / "reparse-restoration-target"),
            )
            assert "book contents must not contain reparse points" in reparse_dry_run.stdout
            assert (
                external_restoration_sentinel.read_text(encoding="utf-8")
                == "untouched"
            )
            assert reparse_book.is_dir()
            assert not (root / "reparse-restoration-target").exists()
            remove_directory_link(restoration_link)

        dangling_source_library = root / "dangling-target-source"
        dangling_target_library = root / "dangling-target-library"
        dangling_source_library.mkdir()
        dangling_target_library.mkdir()
        dangling_book = make_legacy_book(
            dangling_source_library,
            "dangling-book",
            "Dangling Target",
            2024,
            "Safe Author",
        )
        (dangling_book / "source" / "original.pdf").write_bytes(b"source")
        outside_target = root / "outside-target"
        outside_target.mkdir()
        canonical_target = (
            dangling_target_library
            / canonical_book_folder_name("Dangling Target", 2024, "Safe Author")
        )
        if create_directory_link(canonical_target, outside_target):
            outside_target.rmdir()
            dangling = run(
                "migrate_library_layout.py",
                "--source-library",
                str(dangling_source_library),
                "--target-library",
                str(dangling_target_library),
            )
            assert "target already exists" in dangling.stdout
            run(
                "migrate_library_layout.py",
                "--source-library",
                str(dangling_source_library),
                "--target-library",
                str(dangling_target_library),
                "--execute",
                "--confirm",
                CONFIRMATION,
            )
            assert dangling_book.is_dir()
            assert not outside_target.exists()
            remove_directory_link(canonical_target)

        dangling_restoration_library = root / "dangling-restoration-library"
        dangling_restoration_library.mkdir()
        dangling_restoration_book = make_legacy_book(
            dangling_restoration_library,
            "dangling-restoration-book",
            "Dangling Restoration",
            2024,
            "Safe Author",
        )
        (dangling_restoration_book / "source" / "original.pdf").write_bytes(b"source")
        (dangling_restoration_book / "restoration").mkdir()
        dangling_assets_target = root / "dangling-assets-target"
        dangling_assets_target.mkdir()
        dangling_assets_link = (
            dangling_restoration_book / "assets" / "restoration"
        )
        if create_directory_link(dangling_assets_link, dangling_assets_target):
            dangling_assets_target.rmdir()
            dangling_assets = run(
                "migrate_library_layout.py",
                "--source-library",
                str(dangling_restoration_library),
                "--target-library",
                str(root / "dangling-restoration-target"),
            )
            assert "book contents must not contain reparse points" in dangling_assets.stdout
            assert dangling_restoration_book.is_dir()
            assert not (root / "dangling-restoration-target").exists()
            remove_directory_link(dangling_assets_link)

        ancestor_source_library = root / "ancestor-source-library"
        ancestor_source_library.mkdir()
        ancestor_book = make_legacy_book(
            ancestor_source_library,
            "ancestor-book",
            "Ancestor Target",
            2024,
            "Safe Author",
        )
        (ancestor_book / "source" / "original.pdf").write_bytes(b"source")
        external_target_parent = root / "external-target-parent"
        external_target_parent.mkdir()
        target_parent_link = root / "target-parent-link"
        if create_directory_link(target_parent_link, external_target_parent):
            ancestor_result = run_fails(
                "migrate_library_layout.py",
                "--source-library",
                str(ancestor_source_library),
                "--target-library",
                str(target_parent_link / "Library"),
            )
            assert "Target library must not traverse a reparse point" in ancestor_result.stderr
            assert ancestor_book.is_dir()
            assert not (external_target_parent / "Library").exists()
            remove_directory_link(target_parent_link)

        dangling_target_parent = root / "dangling-target-parent"
        dangling_target_parent.mkdir()
        dangling_parent_link = root / "dangling-parent-link"
        if create_directory_link(dangling_parent_link, dangling_target_parent):
            dangling_target_parent.rmdir()
            dangling_ancestor_result = run_fails(
                "migrate_library_layout.py",
                "--source-library",
                str(ancestor_source_library),
                "--target-library",
                str(dangling_parent_link / "Library"),
            )
            assert (
                "Target library must not traverse a reparse point"
                in dangling_ancestor_result.stderr
            )
            assert ancestor_book.is_dir()
            remove_directory_link(dangling_parent_link)

        nested_reparse_library = root / "nested-reparse-library"
        nested_reparse_library.mkdir()
        nested_reparse_book = make_legacy_book(
            nested_reparse_library,
            "nested-reparse-book",
            "Nested Reparse",
            2024,
            "Safe Author",
        )
        (nested_reparse_book / "source" / "original.pdf").write_bytes(b"source")
        external_exports = root / "external-exports"
        external_exports.mkdir()
        external_sidecar = external_exports / "outside.epub.json"
        external_sidecar.write_text('{"unchanged": true}\n', encoding="utf-8")
        nested_exports_link = nested_reparse_book / "exports" / "epub"
        if create_directory_link(nested_exports_link, external_exports):
            nested_dry_run = run(
                "migrate_library_layout.py",
                "--source-library",
                str(nested_reparse_library),
                "--target-library",
                str(root / "nested-reparse-target"),
            )
            assert "book contents must not contain reparse points" in nested_dry_run.stdout
            assert (
                external_sidecar.read_text(encoding="utf-8")
                == '{"unchanged": true}\n'
            )
            assert nested_reparse_book.is_dir()
            assert not (root / "nested-reparse-target").exists()
            remove_directory_link(nested_exports_link)

    print("VALID book layout tests")


if __name__ == "__main__":
    main()
