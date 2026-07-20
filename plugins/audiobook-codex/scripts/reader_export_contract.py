from __future__ import annotations

from pathlib import Path
from typing import Any


COMMON_EXPORT_CODE_MODULES = (
    "book_layout.py",
    "epub_layout.py",
    "epub_presentation.py",
    "export_epub.py",
    "path_safety.py",
    "reader_export_contract.py",
    "validate_assets_manifest.py",
    "validate_book_map.py",
    "verify_text_ledger.py",
    "verify_translation_ledger.py",
    "verify_revision_ledger.py",
    "verify_fluid_edition_ledger.py",
)
PDF_EXPORT_CODE_MODULES = ("export_pdf.py",)


def exporter_code_paths(kind: str, scripts_root: Path) -> list[Path]:
    modules = list(COMMON_EXPORT_CODE_MODULES)
    if kind == "pdf":
        modules.extend(PDF_EXPORT_CODE_MODULES)
    return [scripts_root / module for module in modules]


def sidecar_contract_projection(
    sidecar: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    return {key: sidecar.get(key) for key in expected}


def sidecar_contract_matches(
    sidecar: object,
    expected: dict[str, Any],
) -> bool:
    return (
        isinstance(sidecar, dict)
        and sidecar_contract_projection(sidecar, expected) == expected
    )


def sidecar_identity_matches(
    sidecar: object,
    expected: dict[str, Any],
    path_key: str,
    hash_key: str,
) -> bool:
    expected_path = expected.get(path_key)
    expected_hash = expected.get(hash_key)
    expected_fingerprint = expected.get("input_fingerprint")
    return (
        isinstance(sidecar, dict)
        and isinstance(expected_path, str)
        and isinstance(expected_hash, str)
        and isinstance(expected_fingerprint, dict)
        and sidecar.get(path_key) == expected_path
        and sidecar.get(hash_key) == expected_hash
        and sidecar.get("input_fingerprint") == expected_fingerprint
    )
