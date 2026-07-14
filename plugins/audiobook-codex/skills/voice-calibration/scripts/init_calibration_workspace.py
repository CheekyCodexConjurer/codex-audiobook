from __future__ import annotations

import argparse
from pathlib import Path
import sys

from calibration_workspace import PROMPTS, WorkspaceError, draft_corpus, validate_profile_id, write_json


DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\e-books")


def initialize_workspace(profile_id: str, target_library_root: Path = DEFAULT_LIBRARY_ROOT) -> Path:
    validated_profile_id = validate_profile_id(profile_id)
    library_root = target_library_root.expanduser().resolve()
    library_root.mkdir(parents=True, exist_ok=True)
    workspace = (library_root / f"_voice-calibration-{validated_profile_id}").resolve()
    try:
        workspace.relative_to(library_root)
    except ValueError as error:
        raise WorkspaceError(
            "canonical calibration workspace escapes the library root."
        ) from error
    if workspace.exists():
        raise WorkspaceError(f"Refusing to reuse existing workspace: {workspace}")

    for relative in (
        "validation-corpus",
        "references/original",
        "renders",
        "selection",
        "logs",
    ):
        (workspace / relative).mkdir(parents=True, exist_ok=False)
    for prompt_id, text in PROMPTS:
        (workspace / "validation-corpus" / f"{prompt_id}.txt").write_text(
            text,
            encoding="utf-8",
        )
    write_json(
        workspace / "validation-corpus" / "corpus.json",
        draft_corpus(validated_profile_id, workspace),
    )
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an immutable three-prompt voice-calibration workspace."
    )
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args()

    try:
        workspace = initialize_workspace(args.profile_id)
        print(f"Created calibration workspace: {workspace}")
    except WorkspaceError as error:
        print(f"Cannot initialize calibration workspace: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
