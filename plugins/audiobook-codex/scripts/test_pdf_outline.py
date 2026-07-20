from __future__ import annotations

from types import SimpleNamespace
import unittest

from validate_pdf_export import (
    _find_fragment_ignoring_whitespace,
    expected_outline_titles,
    validate_outline,
)


class PdfOutlineValidationTests(unittest.TestCase):
    def test_outline_order_accepts_canonical_document_titles(self) -> None:
        reader = SimpleNamespace(
            outline=[
                SimpleNamespace(title="Sumário"),
                SimpleNamespace(title="Título e autor"),
                SimpleNamespace(title="Sumário"),
                SimpleNamespace(title="Posfácio"),
            ]
        )
        self.assertEqual(
            validate_outline(
                reader,
                ["Título e autor", "Sumário", "Posfácio"],
            ),
            [],
        )

    def test_outline_order_rejects_noncanonical_title_case(self) -> None:
        reader = SimpleNamespace(
            outline=[
                SimpleNamespace(title="Sumário"),
                SimpleNamespace(title="TÍTULO E AUTOR"),
                SimpleNamespace(title="POSFÁCIO"),
            ]
        )
        self.assertEqual(
            validate_outline(reader, ["Título e autor", "Posfácio"]),
            ["PDF outline does not preserve the validated document order"],
        )

    def test_outline_order_still_rejects_reordered_documents(self) -> None:
        reader = SimpleNamespace(
            outline=[
                SimpleNamespace(title="Sumário"),
                SimpleNamespace(title="POSFÁCIO"),
                SimpleNamespace(title="TÍTULO E AUTOR"),
            ]
        )
        self.assertEqual(
            validate_outline(reader, ["Título e autor", "Posfácio"]),
            ["PDF outline does not preserve the validated document order"],
        )

    def test_semantic_outline_uses_canonical_document_titles(self) -> None:
        documents = [
            {
                "kind": "chapter",
                "title": "O que são resultados?",
                "_layout_blocks": [
                    {"kind": "heading", "level": 1, "block_index": 1}
                ],
            }
        ]
        self.assertEqual(
            ["O que são resultados?"],
            expected_outline_titles(None, documents, "original"),
        )

    def test_url_fragment_accepts_pdf_line_break_whitespace(self) -> None:
        value = (
            "Texto anterior "
            "https://example.com/a-very-long-pa th/another-seg ment "
            "Texto posterior"
        )
        fragment = "https://example.com/a-very-long-path/another-segment"
        start, end = _find_fragment_ignoring_whitespace(value, fragment, 0)
        self.assertGreaterEqual(start, 0)
        self.assertEqual(
            "".join(value[start:end].split()),
            fragment,
        )


if __name__ == "__main__":
    unittest.main()
