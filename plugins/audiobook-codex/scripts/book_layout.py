from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\Library")
ASSEMBLY_DIRECTORY = "assembly"
ASSEMBLY_SUBDIRECTORIES = (
    "assets",
    "audio",
    "exports",
    "metadata",
    "pages",
    "source",
    "text",
)
WINDOWS_INVALID_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class BookPaths:
    public_root: Path
    assembly_root: Path
    layout_kind: str

    def relative_to_public(self, path: Path) -> str:
        return path.resolve().relative_to(self.public_root.resolve()).as_posix()

    def relative_to_assembly(self, path: Path) -> str:
        return path.resolve().relative_to(self.assembly_root.resolve()).as_posix()


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def path_lexists(path: Path) -> bool:
    return os.path.lexists(path)


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = os.lstat(path).st_file_attributes
    except AttributeError:
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def assert_no_reparse_ancestors(path: Path, label: str) -> Path:
    lexical_path = lexical_absolute(path)
    current = Path(lexical_path.anchor) if lexical_path.anchor else Path()
    parts = lexical_path.parts[1:] if lexical_path.anchor else lexical_path.parts
    for part in parts:
        current /= part
        if not path_lexists(current):
            break
        if is_reparse_point(current):
            raise RuntimeError(f"{label} must not traverse a reparse point: {current}")
    return lexical_path


def assert_safe_assembly_entries(assembly_root: Path) -> None:
    for name in ASSEMBLY_SUBDIRECTORIES:
        path = assembly_root / name
        if path_lexists(path) and is_reparse_point(path):
            raise RuntimeError(
                f"Assembly entry must not be a reparse point: {name}"
            )


def _clean_folder_component(value: object, label: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = WINDOWS_INVALID_CHARACTERS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        raise RuntimeError(f"{label} must be non-empty.")
    return text


def canonical_book_folder_name(title: object, year: object, author: object) -> str:
    clean_title = _clean_folder_component(title, "Book title")
    clean_author = _clean_folder_component(author, "Book author")
    clean_year = str(year or "").strip()
    if not re.fullmatch(r"\d{1,4}", clean_year):
        raise RuntimeError("Book year must contain one to four digits.")
    name = f"{clean_title} - {clean_year} - {clean_author}"
    if len(name) > 240:
        raise RuntimeError("Canonical book folder name exceeds 240 characters.")
    return name


def book_identity(book: object) -> tuple[str, int, str]:
    if not isinstance(book, dict):
        raise RuntimeError("Book metadata must be an object.")
    title = book.get("title")
    author = book.get("author")
    year = book.get("original_publication_year")
    if not isinstance(year, int) or isinstance(year, bool) or year <= 0:
        raise RuntimeError("book.original_publication_year must be a positive integer.")
    canonical_book_folder_name(title, year, author)
    return str(title).strip(), year, str(author).strip()


def paths_for_new_book(
    library_root: Path,
    title: object,
    year: object,
    author: object,
) -> BookPaths:
    lexical_library = assert_no_reparse_ancestors(library_root, "Library root")
    public_root = lexical_library.resolve() / canonical_book_folder_name(
        title, year, author
    )
    return BookPaths(public_root, public_root / ASSEMBLY_DIRECTORY, "new")


def resolve_book_paths(
    book_root: Path,
    *,
    allow_legacy: bool = True,
    require_exists: bool = True,
) -> BookPaths:
    lexical_root = assert_no_reparse_ancestors(book_root, "Book root")
    public_root = lexical_root.resolve()
    if public_root.name.casefold() == ASSEMBLY_DIRECTORY:
        raise RuntimeError(
            f"--book-root must identify the public book folder, not its assembly directory: "
            f"{public_root}"
        )
    if require_exists and not public_root.is_dir():
        raise RuntimeError(f"Book root does not exist: {public_root}")

    reserved_entries = {
        name: public_root / name
        for name in (ASSEMBLY_DIRECTORY, *ASSEMBLY_SUBDIRECTORIES)
    }
    for name, path in reserved_entries.items():
        if path_lexists(path) and is_reparse_point(path):
            raise RuntimeError(
                f"Book root reserved entry must not be a reparse point: {name}"
            )

    new_assembly = reserved_entries[ASSEMBLY_DIRECTORY]
    has_new = new_assembly.is_dir()
    legacy_directories = sorted(
        name
        for name in ASSEMBLY_SUBDIRECTORIES
        if path_lexists(reserved_entries[name])
    )
    has_legacy = (public_root / "metadata").is_dir()
    if has_new and legacy_directories:
        raise RuntimeError(
            f"Book root contains both new and legacy layout directories "
            f"{legacy_directories}: {public_root}"
        )
    if has_new:
        assert_safe_assembly_entries(new_assembly)
        return BookPaths(public_root, new_assembly, "new")
    if has_legacy and allow_legacy:
        return BookPaths(public_root, public_root, "legacy")
    if has_legacy:
        raise RuntimeError(f"Legacy book layout is not allowed here: {public_root}")
    raise RuntimeError(
        f"Book root has no assembly directory"
        + (" or legacy metadata directory" if allow_legacy else "")
        + f": {public_root}"
    )


def ensure_assembly_tree(paths: BookPaths) -> None:
    assert_no_reparse_ancestors(paths.public_root, "Book root")
    assert_no_reparse_ancestors(paths.assembly_root, "Assembly root")
    if path_lexists(paths.assembly_root) and is_reparse_point(paths.assembly_root):
        raise RuntimeError(
            f"Assembly root must not be a reparse point: {paths.assembly_root}"
        )
    assert_safe_assembly_entries(paths.assembly_root)
    paths.public_root.mkdir(parents=True, exist_ok=True)
    paths.assembly_root.mkdir(parents=True, exist_ok=True)
    for name in ASSEMBLY_SUBDIRECTORIES:
        (paths.assembly_root / name).mkdir(exist_ok=True)
    assert_no_reparse_ancestors(paths.assembly_root, "Assembly root")
    assert_safe_assembly_entries(paths.assembly_root)


def validate_public_root_name(paths: BookPaths, book: object) -> list[str]:
    if paths.layout_kind != "new":
        return []
    try:
        title, year, author = book_identity(book)
        expected = canonical_book_folder_name(title, year, author)
    except RuntimeError as error:
        return [str(error)]
    if paths.public_root.name != expected:
        return [
            f"Book folder name must be {expected!r}, got {paths.public_root.name!r}"
        ]
    return []
