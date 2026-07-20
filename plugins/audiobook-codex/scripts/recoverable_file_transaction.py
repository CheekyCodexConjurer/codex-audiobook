from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Iterable


JOURNAL_PREFIX = "recoverable-file-transaction"
JOURNAL_VERSION = "1.0"


@dataclass(frozen=True)
class StagedReplacement:
    destination: Path
    staged: Path


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(_json_bytes(data))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _journal_dir(book_root: Path) -> Path:
    return book_root.resolve() / "metadata" / "work"


def journal_path(book_root: Path, transaction_name: str) -> Path:
    safe_name = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in transaction_name
    ).strip("-_")
    if not safe_name:
        raise RuntimeError("Recoverable transaction name is required")
    return _journal_dir(book_root) / f"{JOURNAL_PREFIX}.{safe_name}.json"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_path(path: Path, allowed_roots: tuple[Path, ...], label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not any(_is_under(resolved, root) for root in allowed_roots):
        roots = ", ".join(str(root) for root in allowed_roots)
        raise RuntimeError(f"Recoverable transaction {label} escapes allowed roots ({roots}): {resolved}")
    return resolved


def _validated_roots(book_root: Path, allowed_roots: Iterable[Path] | None) -> tuple[Path, ...]:
    roots = tuple(root.expanduser().resolve() for root in (allowed_roots or (book_root,)))
    if not roots:
        raise RuntimeError("Recoverable transaction requires at least one allowed root")
    return roots


def _load_journal(path: Path, allowed_roots: tuple[Path, ...]) -> dict:
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read recoverable transaction journal {path}: {error}") from error
    if not isinstance(journal, dict) or journal.get("version") != JOURNAL_VERSION:
        raise RuntimeError(f"Recoverable transaction journal is invalid: {path}")
    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError(f"Recoverable transaction journal entries are invalid: {path}")
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"Recoverable transaction journal entry is invalid: {path}")
        for key in ("destination", "staged", "backup"):
            raw = entry.get(key)
            if not isinstance(raw, str) or not raw:
                raise RuntimeError(f"Recoverable transaction journal {key} is invalid: {path}")
            entry[key] = _validate_path(Path(raw), allowed_roots, key)
        if not isinstance(entry.get("existed_before"), bool):
            raise RuntimeError(f"Recoverable transaction journal prior state is invalid: {path}")
    phase = journal.get("phase")
    if phase not in {"prepared", "promoting", "promoted"}:
        raise RuntimeError(f"Recoverable transaction journal phase is invalid: {path}")
    return journal


def _entry(destination: Path, staged: Path, backup: Path, existed_before: bool) -> dict:
    return {
        "destination": destination.as_posix(),
        "staged": staged.as_posix(),
        "backup": backup.as_posix(),
        "existed_before": existed_before,
    }


def _backup_path(destination: Path, transaction_name: str, index: int) -> Path:
    return destination.with_name(
        f".{destination.name}.{os.getpid()}.{index}.{transaction_name}.backup"
    )


def _cleanup_paths(paths: Iterable[Path]) -> list[Path]:
    residual: list[Path] = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                _fsync_directory(path.parent)
        except OSError:
            residual.append(path)
    return [path for path in residual if path.exists()]


def _finish_promoted(journal_path_value: Path, journal: dict) -> None:
    paths: list[Path] = []
    for entry in journal["entries"]:
        paths.append(entry["backup"])
        paths.append(entry["staged"])
    residual = _cleanup_paths(paths)
    if residual:
        raise RuntimeError(
            "Cannot clean up promoted transaction residue: "
            + ", ".join(str(path) for path in residual)
        )
    journal_path_value.unlink(missing_ok=True)
    _fsync_directory(journal_path_value.parent)


def _rollback(journal_path_value: Path, journal: dict) -> None:
    entries = list(journal["entries"])
    for entry in reversed(entries):
        destination: Path = entry["destination"]
        backup: Path = entry["backup"]
        if backup.exists():
            if destination.exists():
                destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
            _fsync_directory(destination.parent)
        elif not entry["existed_before"] and destination.exists():
            destination.unlink()
            _fsync_directory(destination.parent)
        elif entry["existed_before"] and not destination.exists():
            raise RuntimeError(
                f"Cannot recover interrupted transaction; missing backup for {destination}"
            )
    paths: list[Path] = []
    for entry in entries:
        paths.append(entry["staged"])
        paths.append(entry["backup"])
    residual = _cleanup_paths(paths)
    if residual:
        raise RuntimeError(
            "Cannot clean up rolled-back transaction residue: "
            + ", ".join(str(path) for path in residual)
        )
    journal_path_value.unlink(missing_ok=True)
    _fsync_directory(journal_path_value.parent)


def recover_pending_transactions(
    book_root: Path,
    *,
    allowed_roots: Iterable[Path] | None = None,
) -> None:
    roots = _validated_roots(book_root, allowed_roots)
    root = _journal_dir(book_root)
    if not root.is_dir():
        return
    for path in sorted(root.glob(f"{JOURNAL_PREFIX}.*.json")):
        if not _is_under(path, root):
            continue
        journal = _load_journal(path, roots)
        if journal["phase"] == "promoted":
            _finish_promoted(path, journal)
        else:
            _rollback(path, journal)


def commit_recoverable_transaction(
    book_root: Path,
    transaction_name: str,
    replacements: list[StagedReplacement],
    *,
    allowed_roots: Iterable[Path] | None = None,
) -> None:
    if not replacements:
        return
    roots = _validated_roots(book_root, allowed_roots)
    recover_pending_transactions(book_root, allowed_roots=roots)
    path = journal_path(book_root, transaction_name)
    if path.exists():
        recover_pending_transactions(book_root, allowed_roots=roots)
    normalized: list[StagedReplacement] = []
    entries: list[dict] = []
    for index, replacement in enumerate(replacements):
        destination = _validate_path(replacement.destination, roots, "destination")
        staged = _validate_path(replacement.staged, roots, "staged")
        if not staged.is_file():
            raise RuntimeError(f"Recoverable transaction stage is missing: {staged}")
        _fsync_file(staged)
        backup = _validate_path(
            _backup_path(destination, transaction_name, index), roots, "backup"
        )
        if backup.exists():
            raise RuntimeError(f"Recoverable transaction backup already exists: {backup}")
        normalized.append(StagedReplacement(destination, staged))
        entries.append(_entry(destination, staged, backup, destination.exists()))
    journal = {
        "version": JOURNAL_VERSION,
        "transaction": transaction_name,
        "phase": "prepared",
        "entries": entries,
    }
    _atomic_write_json(path, journal)
    journal["phase"] = "promoting"
    _atomic_write_json(path, journal)
    try:
        for replacement, entry in zip(normalized, entries):
            destination = replacement.destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = Path(str(entry["backup"])).resolve()
            if destination.exists():
                os.replace(destination, backup)
                _fsync_directory(destination.parent)
            os.replace(replacement.staged, destination)
            _fsync_directory(destination.parent)
        journal["phase"] = "promoted"
        _atomic_write_json(path, journal)
        journal = _load_journal(path, roots)
    except Exception:
        try:
            journal = _load_journal(path, roots)
            _rollback(path, journal)
        except Exception:
            pass
        raise
    _finish_promoted(path, journal)
