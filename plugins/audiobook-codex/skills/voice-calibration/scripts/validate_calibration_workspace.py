from __future__ import annotations

import argparse
from pathlib import Path
import sys

from calibration_workspace import WorkspaceError, validate_corpus


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the immutable input contract for a voice-calibration workspace."
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--check-files", action="store_true")
    args = parser.parse_args()

    try:
        root = args.workspace_root.expanduser().resolve()
        corpus = validate_corpus(
            root,
            require_ready=args.require_ready,
            check_files=args.check_files,
        )
        print(f"VALID calibration workspace: {root} ({corpus['status']})")
    except WorkspaceError as error:
        print(f"INVALID calibration workspace: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
