from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from narration_plan import (
    SourceUnit,
    _chapter_records,
    _locutor_units,
    _semantic_parts,
    _segments_for_unit,
    _source_units,
    _validate_unit_boundaries,
)


class NarrationPlanSafetyTests(unittest.TestCase):
    def test_wrapped_note_remains_one_semantic_unit(self) -> None:
        units = _source_units(
            "1 Para quem se interessa em conhecer melhor a história de Zélio de Moraes "
            "e do Caboclo das Sete\n"
            "Encruzilhadas recomendo o livro Iniciação à Umbanda, por Ronaldo Linares,\n"
            "Diamantino Trindade e Wagner Veneziani Costa.\n"
            "Graças ao amigo, a pesquisa continuou."
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                (
                    "note",
                    "1 Para quem se interessa em conhecer melhor a história de Zélio de "
                    "Moraes e do Caboclo das Sete Encruzilhadas recomendo o livro "
                    "Iniciação à Umbanda, por Ronaldo Linares, Diamantino Trindade e "
                    "Wagner Veneziani Costa.",
                ),
                ("paragraph", "Graças ao amigo, a pesquisa continuou."),
            ],
        )

    def test_short_note_does_not_absorb_following_paragraph(self) -> None:
        units = _source_units(
            "2 Dr. José Meireles foi dirigente da Tenda Espírita São Pedro\n"
            "Em meio desse debate, entrou em transe o Senhor Zélio de Moraes."
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                ("note", "2 Dr. José Meireles foi dirigente da Tenda Espírita São Pedro"),
                (
                    "paragraph",
                    "Em meio desse debate, entrou em transe o Senhor Zélio de Moraes.",
                ),
            ],
        )

    def test_dialogue_attribution_and_following_dialogue_are_distinct(self) -> None:
        units = _source_units(
            "- Cuidado, caboclo avisou o preto. O coração dessa filha não está batendo."
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                ("dialogue", "- Cuidado, caboclo"),
                ("attribution", "avisou o preto."),
                ("dialogue", "O coração dessa filha não está batendo."),
            ],
        )

    def test_wrapped_attribution_remains_attached(self) -> None:
        units = _source_units(
            "- No meu tempo eram chinelas, respondeu, e caminhando até a mesa\n"
            "existente no fundo da sala, voltou com uma bilha."
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                ("dialogue", "- No meu tempo eram chinelas,"),
                (
                    "attribution",
                    "respondeu, e caminhando até a mesa existente no fundo da sala, "
                    "voltou com uma bilha.",
                ),
            ],
        )

    def test_lowercase_dash_list_is_not_dialogue(self) -> None:
        units = _source_units(
            "- a primeira de Oxalá; a segunda de Ogum; a terceira de Oxóssi; a\n"
            "quarta, de Xangô."
        )
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].role, "paragraph")

    def test_initials_and_parenthetical_titles_are_not_split(self) -> None:
        initials = (
            "J. R. R. Tolkien escreveu uma frase completa sobre o tema. "
            "Outra sentença completa encerra o parágrafo."
        )
        chunks = _segments_for_unit(SourceUnit(initials, "paragraph"), initials, 80)
        self.assertEqual(chunks[0][0], "J. R. R. Tolkien escreveu uma frase completa sobre o tema.")

        title = (
            "Hanamatan escreveu (Iniciação à Umbanda. Editora Madras, História da "
            "Umbanda, Umbanda. Um Ensaio de Ecletismo e Umbanda Brasileira) e "
            "preservou a referência completa."
        )
        title_chunks = _segments_for_unit(SourceUnit(title, "paragraph"), title, 180)
        self.assertEqual(title_chunks, [(title, "paragraph")])

    def test_hyphenated_pronoun_does_not_hide_sentence_boundary(self) -> None:
        self.assertEqual(
            _semantic_parts(
                "O guia pode acompanhá-lo, orientando-a. Mas depois segue.",
                ".!?…",
            ),
            [
                "O guia pode acompanhá-lo, orientando-a.",
                "Mas depois segue.",
            ],
        )

    def test_lexical_boundary_without_punctuation_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsafe lexical boundary"):
            _validate_unit_boundaries(
                [
                    {
                        "id": "front-02-0001",
                        "index": 1,
                        "text": "Caboclo das Sete",
                        "role": "note",
                        "source": {"base_output_id": "front-02"},
                    },
                    {
                        "id": "front-02-0002",
                        "index": 2,
                        "text": "Encruzilhadas recomendou o livro.",
                        "role": "paragraph",
                        "source": {"base_output_id": "front-02"},
                    },
                ]
            )

    def test_alignment_exhaustion_is_explicit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ended before"):
            _locutor_units(
                "Primeira frase completa.\n\nSegunda frase completa.",
                "Primeira frase completa.",
            )

    def test_spoken_note_closure_stays_inside_note_unit(self) -> None:
        aligned = _locutor_units(
            "1 Nota breve.\nParágrafo seguinte.",
            "Nota um. Nota breve. Fim da nota um. Parágrafo seguinte.",
        )
        self.assertEqual(
            [(unit.role, spoken) for unit, spoken in aligned],
            [
                ("note", "Nota um. Nota breve. Fim da nota um."),
                ("paragraph", "Parágrafo seguinte."),
            ],
        )

    def test_translated_base_edition_selects_translation_ledger(self) -> None:
        with tempfile.TemporaryDirectory(prefix="narration-plan-translation-") as temporary:
            book_root = Path(temporary)
            metadata = book_root / "metadata"
            translated = book_root / "text" / "translation" / "pt-BR" / "chapters"
            locutor = book_root / "text" / "locutor"
            locutor_chapters = locutor / "chapters"
            metadata.mkdir(parents=True)
            translated.mkdir(parents=True)
            locutor_chapters.mkdir(parents=True)
            input_file = locutor / "book.txt"
            input_file.write_text("Texto traduzido.\n", encoding="utf-8")
            (translated / "chapter-01.txt").write_text(
                "Texto traduzido.", encoding="utf-8"
            )
            (locutor_chapters / "chapter-01.txt").write_text(
                "Texto traduzido.", encoding="utf-8"
            )
            (metadata / "narrator-changes.json").write_text(
                json.dumps(
                    {
                        "base_edition": "translated-pt-br",
                        "outputs": [
                            {
                                "id": "book",
                                "locutor_file": "locutor/book.txt",
                                "base_outputs": [{"id": "chapter-01"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (metadata / "translation-ledger.json").write_text(
                json.dumps(
                    {
                        "chapter_outputs": [
                            {
                                "id": "chapter-01",
                                "translation_file": (
                                    "translation/pt-BR/chapters/chapter-01.txt"
                                ),
                                "source_pages": [{"logical_page": 1}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            records = _chapter_records(book_root, input_file)
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["source_path"],
                translated / "chapter-01.txt",
            )
            self.assertEqual(
                records[0]["base_ledger_path"],
                metadata / "translation-ledger.json",
            )


if __name__ == "__main__":
    unittest.main()
