from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile

from calibration_workspace import (
    PROMPT_IDS,
    WorkspaceError,
    relative_path,
    sha256_file,
    validate_corpus,
    write_json,
)


def parse_target(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("target must use PROMPT_ID=PATH.")
    prompt_id, raw_path = value.split("=", 1)
    if prompt_id not in PROMPT_IDS:
        raise argparse.ArgumentTypeError(f"target id must be one of: {', '.join(PROMPT_IDS)}")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise argparse.ArgumentTypeError(f"target file is missing or empty: {path}")
    return prompt_id, path


def imported_path(root: Path, stem: str, source: Path) -> Path:
    if not source.suffix:
        raise WorkspaceError(f"Imported audio must have a filename extension: {source}")
    return root / "references" / "original" / f"{stem}{source.suffix.lower()}"


def import_targets(root: Path, reference: Path, target_map: dict[str, Path]) -> None:
    corpus = validate_corpus(root)
    if corpus["status"] != "draft":
        raise WorkspaceError("Refusing to replace imported audio in a non-draft corpus.")
    if not reference.is_file() or reference.stat().st_size == 0:
        raise WorkspaceError(f"Voice reference is missing or empty: {reference}")
    if not reference.suffix:
        raise WorkspaceError(f"Voice reference must have a filename extension: {reference}")
    if set(target_map) != set(PROMPT_IDS):
        raise WorkspaceError(f"Exactly these targets are required: {', '.join(PROMPT_IDS)}")

    reference_destination = imported_path(root, "voice-reference", reference)
    destinations = {
        prompt_id: imported_path(root, prompt_id, target_map[prompt_id])
        for prompt_id in PROMPT_IDS
    }
    occupied = [path for path in (reference_destination, *destinations.values()) if path.exists()]
    if occupied:
        raise WorkspaceError(
            "Refusing to overwrite imported audio: " + ", ".join(str(path) for path in occupied)
        )

    original_corpus = copy.deepcopy(corpus)
    import_parent = reference_destination.parent
    import_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".voice-import-", dir=import_parent))
    promoted: list[Path] = []
    corpus_path = root / "validation-corpus" / "corpus.json"
    try:
        staged_reference = staging / reference_destination.name
        staged_targets = {
            prompt_id: staging / destinations[prompt_id].name
            for prompt_id in PROMPT_IDS
        }
        shutil.copy2(reference, staged_reference)
        for prompt_id in PROMPT_IDS:
            shutil.copy2(target_map[prompt_id], staged_targets[prompt_id])

        next_corpus = copy.deepcopy(corpus)
        next_corpus["voice_reference"] = {
            "path": relative_path(root, reference_destination),
            "sha256": sha256_file(staged_reference),
        }
        for prompt in next_corpus["prompts"]:
            prompt_id = str(prompt["id"])
            target = destinations[prompt_id]
            prompt["target_audio_path"] = relative_path(root, target)
            prompt["target_audio_sha256"] = sha256_file(staged_targets[prompt_id])
        next_corpus["status"] = "ready"

        for staged, destination in (
            (staged_reference, reference_destination),
            *((staged_targets[prompt_id], destinations[prompt_id]) for prompt_id in PROMPT_IDS),
        ):
            os.replace(staged, destination)
            promoted.append(destination)
        write_json(corpus_path, next_corpus)
        validate_corpus(root, require_ready=True, check_files=True)
    except Exception:
        for destination in reversed(promoted):
            destination.unlink(missing_ok=True)
        write_json(corpus_path, original_corpus)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy local calibration reference and target audio into an initialized workspace."
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--voice-reference", type=Path, required=True)
    parser.add_argument("--target", action="append", type=parse_target, required=True)
    args = parser.parse_args()

    try:
        root = args.workspace_root.expanduser().resolve()
        reference = args.voice_reference.expanduser().resolve()
        target_map: dict[str, Path] = {}
        for prompt_id, path in args.target:
            if prompt_id in target_map:
                raise WorkspaceError(f"Duplicate target: {prompt_id}")
            target_map[prompt_id] = path
        import_targets(root, reference, target_map)
        print(f"Imported immutable calibration targets: {root}")
    except (WorkspaceError, OSError) as error:
        print(f"Cannot import calibration targets: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
