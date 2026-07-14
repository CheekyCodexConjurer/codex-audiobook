from __future__ import annotations

import argparse
from pathlib import Path
import sys

from calibration_workspace import PROMPTS, WorkspaceError, draft_corpus, validate_profile_id, write_json


DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\e-books")


def resolve_workspace(library_root: Path, profile_id: str, output_root: Path | None) -> Path:
    root = output_root.expanduser().resolve() if output_root else (
        library_root / f"_voice-calibration-{profile_id}"
    ).resolve()
    try:
        root.relative_to(library_root)
    except ValueError as error:
        raise WorkspaceError("workspace must stay inside the selected library root.") from error
    return root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an immutable three-prompt voice-calibration workspace."
    )
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()

    try:
        profile_id = validate_profile_id(args.profile_id)
        library_root = args.library_root.expanduser().resolve()
        library_root.mkdir(parents=True, exist_ok=True)
        workspace = resolve_workspace(library_root, profile_id, args.output_root)
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
            draft_corpus(profile_id, workspace),
        )
        print(f"Created calibration workspace: {workspace}")
    except WorkspaceError as error:
        print(f"Cannot initialize calibration workspace: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
