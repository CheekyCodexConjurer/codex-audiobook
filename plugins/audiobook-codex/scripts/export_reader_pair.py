from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from book_transaction_lock import BookTransactionLock, book_lock_path, file_generation
from book_layout import resolve_book_paths
from publication_selection import require_text_edition
from recoverable_file_transaction import StagedReplacement
from recoverable_file_transaction import commit_recoverable_transaction
from recoverable_file_transaction import recover_pending_transactions


ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIRS = ("metadata", "text", "assets")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_snapshot(book_root: Path) -> str:
    entries: list[dict[str, str]] = []
    lock_path = book_lock_path(book_root)
    for dirname in SNAPSHOT_DIRS:
        root = book_root / dirname
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if path.resolve() == lock_path:
                continue
            entries.append(
                {
                    "path": path.relative_to(book_root).as_posix(),
                    "sha256": sha256_file(path),
                }
            )
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_commands(commands: list[list[str]], label: str) -> None:
    if not commands:
        return

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, text=True, capture_output=True)

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        results = list(executor.map(run, commands))
    failures = [
        (commands[index], result)
        for index, result in enumerate(results)
        if result.returncode != 0
    ]
    if failures:
        messages = [f"{label} branch failed."]
        for command, result in failures:
            messages.append(f"$ {' '.join(command)}")
            if result.stdout:
                messages.append(result.stdout.rstrip())
            if result.stderr:
                messages.append(result.stderr.rstrip())
        raise RuntimeError("\n".join(messages))


def require_safe_outputs(epub_output: Path, pdf_output: Path) -> None:
    if epub_output.suffix.casefold() != ".epub":
        raise RuntimeError(f"EPUB output must use the .epub extension: {epub_output}")
    if pdf_output.suffix.casefold() != ".pdf":
        raise RuntimeError(f"PDF output must use the .pdf extension: {pdf_output}")
    outputs = {
        epub_output,
        pdf_output,
        epub_output.with_suffix(".epub.json"),
        pdf_output.with_suffix(".pdf.json"),
    }
    if len(outputs) != 4:
        raise RuntimeError("Reader pair output paths and sidecars must be distinct.")


def temporary_path(destination: Path, index: int, label: str) -> Path:
    return destination.with_name(
        f".{destination.stem}.{os.getpid()}.{index}.{label}{destination.suffix}"
    )


def cleanup_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def staged_json_replacement(
    destination: Path,
    value: object,
    index: int,
) -> StagedReplacement | None:
    data = json_bytes(value)
    if destination.exists() and destination.read_bytes() == data:
        return None
    staged = temporary_path(destination, index, "final-sidecar-stage")
    if staged.exists():
        raise RuntimeError(f"Reader pair temporary file already exists: {staged}")
    staged.write_bytes(data)
    return StagedReplacement(destination, staged)


def staged_file_replacement(
    destination: Path,
    staged: Path,
    expected_sha256: str,
) -> StagedReplacement | None:
    if sha256_file(staged) != expected_sha256:
        raise RuntimeError(f"Staged artifact hash changed before promotion: {staged}")
    if destination.exists() and sha256_file(destination) == expected_sha256:
        return None
    return StagedReplacement(destination, staged)


def final_sidecar_data(
    book_root: Path,
    staged_output: Path,
    final_output: Path,
    staged_sidecar: Path,
    path_key: str,
    hash_key: str,
) -> dict:
    try:
        data = json.loads(staged_sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read staged sidecar {staged_sidecar}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"Staged sidecar must be a JSON object: {staged_sidecar}")
    staged_hash = sha256_file(staged_output)
    try:
        staged_relative = staged_output.resolve().relative_to(book_root.resolve()).as_posix()
        final_relative = final_output.resolve().relative_to(book_root.resolve()).as_posix()
    except ValueError as error:
        raise RuntimeError("Reader pair outputs must remain under the book root.") from error
    if data.get(path_key) != staged_relative:
        raise RuntimeError(f"Staged sidecar path does not match staged output: {staged_sidecar}")
    if data.get(hash_key) != staged_hash:
        raise RuntimeError(f"Staged sidecar SHA-256 does not match staged output: {staged_sidecar}")
    data[path_key] = final_relative
    return data


def seed_staged_cache(
    book_root: Path,
    final_output: Path,
    staged_output: Path,
    path_key: str,
    hash_key: str,
) -> None:
    final_sidecar = final_output.with_suffix(final_output.suffix + ".json")
    staged_sidecar = staged_output.with_suffix(staged_output.suffix + ".json")
    if not final_output.is_file() or not final_sidecar.is_file():
        return
    try:
        data = json.loads(final_sidecar.read_text(encoding="utf-8"))
        final_relative = final_output.resolve().relative_to(book_root.resolve()).as_posix()
        staged_relative = staged_output.resolve().relative_to(book_root.resolve()).as_posix()
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if (
        not isinstance(data, dict)
        or data.get(path_key) != final_relative
        or data.get(hash_key) != sha256_file(final_output)
    ):
        return
    data[path_key] = staged_relative
    staged_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_output, staged_output)
    staged_sidecar.write_bytes(json_bytes(data))


def commit_transaction(
    book_root: Path,
    allowed_root: Path,
    replacements: list[StagedReplacement],
) -> None:
    if not replacements:
        return
    commit_recoverable_transaction(
        book_root,
        "reader-pair",
        replacements,
        allowed_roots=[allowed_root],
    )


def manifest_args(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.epub_manifest is not None:
        values.extend(["--epub-manifest", str(args.epub_manifest)])
    if args.assets_manifest is not None:
        values.extend(["--assets-manifest", str(args.assets_manifest)])
    values.extend(["--image-edition", args.image_edition])
    values.extend(["--text-edition", args.text_edition])
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export and validate a paired EPUB/PDF reader edition."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument("--image-edition", default="original")
    parser.add_argument("--text-edition", default="original")
    parser.add_argument("--epub-output", required=True, type=Path)
    parser.add_argument("--pdf-output", required=True, type=Path)
    args = parser.parse_args()

    try:
        paths = resolve_book_paths(args.book_root)
        book_root = paths.assembly_root
        require_text_edition(book_root, args.text_edition)
        with BookTransactionLock(book_root):
            recover_pending_transactions(book_root, allowed_roots=[paths.public_root])
            snapshot = input_snapshot(book_root)
            common = ["--book-root", str(args.book_root), *manifest_args(args)]
            epub_output = args.epub_output.expanduser().resolve()
            pdf_output = args.pdf_output.expanduser().resolve()
            require_safe_outputs(epub_output, pdf_output)
            destination_paths = [
                epub_output,
                pdf_output,
                epub_output.with_suffix(".epub.json"),
                pdf_output.with_suffix(".pdf.json"),
            ]
            destination_generation = file_generation(destination_paths)
            epub_stage = temporary_path(epub_output, 0, "pair-stage")
            pdf_stage = temporary_path(pdf_output, 1, "pair-stage")
            epub_sidecar_stage = epub_stage.with_suffix(".epub.json")
            pdf_sidecar_stage = pdf_stage.with_suffix(".pdf.json")
            staging_paths = [epub_stage, pdf_stage, epub_sidecar_stage, pdf_sidecar_stage]
            cleanup_paths(staging_paths)
            seed_staged_cache(
                book_root,
                epub_output,
                epub_stage,
                "epub_path",
                "epub_sha256",
            )
            seed_staged_cache(
                book_root,
                pdf_output,
                pdf_stage,
                "pdf_path",
                "pdf_sha256",
            )
            run_commands(
                [
                    [
                        sys.executable,
                        str(ROOT / "export_epub.py"),
                        *common,
                        "--output",
                        str(epub_stage),
                    ],
                    [
                        sys.executable,
                        str(ROOT / "export_pdf.py"),
                        *common,
                        "--output",
                        str(pdf_stage),
                    ],
                ],
                "export",
            )
            for path in staging_paths:
                if not path.is_file():
                    raise RuntimeError(f"Reader pair staging output is missing: {path}")
            if input_snapshot(book_root) != snapshot:
                raise RuntimeError(
                    "Reader pair inputs drifted after export; refusing validation."
                )
            run_commands(
                [
                    [
                        sys.executable,
                        str(ROOT / "validate_epub_export.py"),
                        *common,
                        "--epub",
                        str(epub_stage),
                    ],
                    [
                        sys.executable,
                        str(ROOT / "validate_pdf_export.py"),
                        *common,
                        "--pdf",
                        str(pdf_stage),
                    ],
                ],
                "validation",
            )
            if input_snapshot(book_root) != snapshot:
                raise RuntimeError(
                    "Reader pair inputs drifted after validation."
                )
            epub_data = final_sidecar_data(
                book_root,
                epub_stage,
                epub_output,
                epub_sidecar_stage,
                "epub_path",
                "epub_sha256",
            )
            pdf_data = final_sidecar_data(
                book_root,
                pdf_stage,
                pdf_output,
                pdf_sidecar_stage,
                "pdf_path",
                "pdf_sha256",
            )
            replacements: list[StagedReplacement] = []
            epub_replacement = staged_file_replacement(
                epub_output, epub_stage, str(epub_data["epub_sha256"])
            )
            if epub_replacement is not None:
                replacements.append(epub_replacement)
            pdf_replacement = staged_file_replacement(
                pdf_output, pdf_stage, str(pdf_data["pdf_sha256"])
            )
            if pdf_replacement is not None:
                replacements.append(pdf_replacement)
            epub_sidecar_replacement = staged_json_replacement(
                epub_output.with_suffix(".epub.json"), epub_data, 2
            )
            if epub_sidecar_replacement is not None:
                replacements.append(epub_sidecar_replacement)
            pdf_sidecar_replacement = staged_json_replacement(
                pdf_output.with_suffix(".pdf.json"), pdf_data, 3
            )
            if pdf_sidecar_replacement is not None:
                replacements.append(pdf_sidecar_replacement)
            if input_snapshot(book_root) != snapshot:
                raise RuntimeError(
                    "Reader pair inputs drifted before promotion."
                )
            if file_generation(destination_paths) != destination_generation:
                raise RuntimeError(
                    "Reader pair destinations changed before promotion."
                )
            commit_transaction(book_root, paths.public_root, replacements)
    except (OSError, RuntimeError) as error:
        if "staging_paths" in locals():
            cleanup_paths(staging_paths)
        if "replacements" in locals():
            cleanup_paths([replacement.staged for replacement in replacements])
        print(f"Cannot export reader pair: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        if "staging_paths" in locals():
            cleanup_paths(staging_paths)
        if "replacements" in locals():
            cleanup_paths([replacement.staged for replacement in replacements])

    print(f"Created {epub_output}")
    print(f"Created {pdf_output}")


if __name__ == "__main__":
    main()
