from __future__ import annotations

import os
from pathlib import Path
import stat


def _normalized_relative_path(raw_path: object) -> tuple[str, ...] | None:
    if not isinstance(raw_path, str):
        return None
    value = raw_path.strip().replace("\\", "/")
    if not value or value.startswith("/"):
        return None
    if Path(value).drive:
        return None
    parts = tuple(value.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        return None
    return parts


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if callable(is_junction) and is_junction(path):
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _has_reparse_component(root: Path, candidate: Path) -> bool:
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            return True
    return False


def resolve_under(
    root: Path,
    raw_path: object,
    required_subtrees: tuple[Path, ...] = (),
) -> Path | None:
    parts = _normalized_relative_path(raw_path)
    if parts is None:
        return None
    canonical_root = root.resolve()
    candidate = canonical_root.joinpath(*parts)
    if required_subtrees and not any(
        _is_under(candidate, canonical_root / subtree) for subtree in required_subtrees
    ):
        return None
    if _has_reparse_component(canonical_root, candidate):
        return None
    target = candidate.resolve()
    return target if _is_under(target, canonical_root) else None
