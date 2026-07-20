from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

from book_transaction_lock import BookTransactionLock
from recoverable_file_transaction import StagedReplacement
from recoverable_file_transaction import commit_recoverable_transaction
from recoverable_file_transaction import journal_path
from recoverable_file_transaction import recover_pending_transactions
import recoverable_file_transaction as transaction


def snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def assert_no_transaction_residue(root: Path) -> None:
    work = root / "metadata" / "work"
    journals = list(work.glob("recoverable-file-transaction.*.json")) if work.exists() else []
    backups = [path for path in root.rglob("*") if path.is_file() and path.name.endswith(".backup")]
    stages = [path for path in root.rglob("*.stage")]
    assert journals == []
    assert backups == []
    assert stages == []


def make_replacements(root: Path, count: int) -> tuple[list[Path], list[StagedReplacement]]:
    destinations: list[Path] = []
    replacements: list[StagedReplacement] = []
    for index in range(count):
        destination = root / f"artifact-{index}.bin"
        staged = root / f"artifact-{index}.stage"
        destination.write_bytes(f"old-{index}".encode("utf-8"))
        staged.write_bytes(f"new-{index}".encode("utf-8"))
        destinations.append(destination)
        replacements.append(StagedReplacement(destination, staged))
    return destinations, replacements


class SimulatedCrash(BaseException):
    pass


def interrupted_commit(root: Path, name: str, count: int, fail_after_replace: int) -> list[Path]:
    destinations, replacements = make_replacements(root, count)
    stages = {replacement.staged.resolve() for replacement in replacements}
    destinations_set = {replacement.destination.resolve() for replacement in replacements}
    calls = 0
    real_replace = transaction.os.replace

    def crashing_replace(source: object, destination: object) -> None:
        nonlocal calls
        source_path = Path(source).resolve()
        destination_path = Path(destination).resolve()
        is_commit_replace = source_path in stages or source_path in destinations_set or destination_path in destinations_set
        real_replace(source, destination)
        if is_commit_replace:
            calls += 1
            if calls == fail_after_replace:
                raise SimulatedCrash(f"crash after replace {calls}")

    with patch.object(transaction.os, "replace", crashing_replace):
        try:
            commit_recoverable_transaction(root, name, replacements, allowed_roots=[root])
        except SimulatedCrash:
            pass
        else:
            raise AssertionError("transaction did not simulate a crash")
    assert journal_path(root, name).is_file()
    return destinations


def assert_interrupted_transaction_recovers_to_previous_state(name: str, count: int) -> None:
    # Existing destinations produce two commit replaces per artifact: destination->backup and stage->destination.
    for fail_after_replace in range(1, count * 2 + 1):
        with tempfile.TemporaryDirectory(prefix=f"audiobook-{name}-recover-") as raw:
            root = Path(raw) / "book"
            (root / "metadata" / "work").mkdir(parents=True)
            destinations = interrupted_commit(root, name, count, fail_after_replace)
            before = {path: f"old-{index}".encode("utf-8") for index, path in enumerate(destinations)}

            with BookTransactionLock(root):
                recover_pending_transactions(root, allowed_roots=[root])

            assert snapshot(destinations) == before
            assert_no_transaction_residue(root)

            _, replacements = make_replacements(root, count)
            commit_recoverable_transaction(root, name, replacements, allowed_roots=[root])
            assert [path.read_bytes() for path in destinations] == [
                f"new-{index}".encode("utf-8") for index in range(count)
            ]
            assert_no_transaction_residue(root)


def test_reader_pair_interrupted_after_each_replace_recovers_and_retries() -> None:
    assert_interrupted_transaction_recovers_to_previous_state("reader-pair", 4)


def test_publication_interrupted_after_each_replace_recovers_and_retries() -> None:
    assert_interrupted_transaction_recovers_to_previous_state("publication", 5)


def test_promoted_journal_completion_cleans_residue_without_rollback() -> None:
    with tempfile.TemporaryDirectory(prefix="audiobook-promoted-cleanup-") as raw:
        root = Path(raw) / "book"
        (root / "metadata" / "work").mkdir(parents=True)
        destinations, replacements = make_replacements(root, 3)
        real_finish = transaction._finish_promoted
        calls = 0

        def crashing_finish(path: Path, journal: dict) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise SimulatedCrash("crash before promoted cleanup")
            real_finish(path, journal)

        with patch.object(transaction, "_finish_promoted", crashing_finish):
            try:
                commit_recoverable_transaction(root, "publication", replacements, allowed_roots=[root])
            except SimulatedCrash:
                pass
            else:
                raise AssertionError("transaction did not crash before cleanup")
        assert json.loads(journal_path(root, "publication").read_text(encoding="utf-8"))["phase"] == "promoted"

        with BookTransactionLock(root):
            recover_pending_transactions(root, allowed_roots=[root])

        assert [path.read_bytes() for path in destinations] == [
            f"new-{index}".encode("utf-8") for index in range(3)
        ]
        assert_no_transaction_residue(root)


def run_tests() -> None:
    tests = [
        test_reader_pair_interrupted_after_each_replace_recovers_and_retries,
        test_publication_interrupted_after_each_replace_recovers_and_retries,
        test_promoted_journal_completion_cleans_residue_without_rollback,
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
    print(f"transaction recovery tests passed ({len(tests)} run)")


if __name__ == "__main__":
    run_tests()