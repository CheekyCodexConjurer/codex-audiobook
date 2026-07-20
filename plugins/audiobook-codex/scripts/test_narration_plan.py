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
    _source_text_with_layout_joins,
    _source_units,
    _validate_unit_boundaries,
)


class NarrationPlanSafetyTests(unittest.TestCase):
    def test_fluid_layout_joins_uppercase_paragraph_continuation(self) -> None:
        source = (
            "The practitioners borrowed ideas and procedures from the indigenous\n\n"
            "Indian shamans, especially those of the Tupí-speaking tribes."
        )
        prepared = _source_text_with_layout_joins(
            source,
            {
                "blocks": [
                    {"kind": "paragraph", "block_index": 1},
                    {
                        "kind": "paragraph",
                        "block_index": 2,
                        "join_with_previous": True,
                    },
                ]
            },
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in _source_units(prepared)],
            [
                (
                    "paragraph",
                    "The practitioners borrowed ideas and procedures from the indigenous "
                    "Indian shamans, especially those of the Tupí-speaking tribes.",
                )
            ],
        )

    def test_page_break_lowercase_continuation_stays_in_same_unit(self) -> None:
        units = _source_units(
            "Queremos que nossos clientes compartilhem um artigo ou enviem uma\n\n"
            "foto, ou concluam uma tarefa em menos tempo. O que isso produz?"
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                (
                    "paragraph",
                    "Queremos que nossos clientes compartilhem um artigo ou enviem uma "
                    "foto, ou concluam uma tarefa em menos tempo. O que isso produz?",
                )
            ],
        )

    def test_page_break_hyphenated_name_stays_in_same_unit(self) -> None:
        units = _source_units(
            "O resultado? “Teve seus desafios”, admitiu Emily Neville-\n\n"
            "O’Neill, gerente de produto sênior da equipe."
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                (
                    "paragraph",
                    "O resultado? “Teve seus desafios”, admitiu Emily Neville- "
                    "O’Neill, gerente de produto sênior da equipe.",
                )
            ],
        )

    def test_short_title_case_group_is_a_heading_but_colon_intro_is_not(self) -> None:
        units = _source_units(
            "Chegar ao pronto: o problema das funcionalidades\n\n"
            "É comum confundir lançar funcionalidades com estar concluído.\n\n"
            "Trabalhando juntos, fizemos três perguntas:\n\n"
            "A primeira pergunta tinha uma resposta.\n\n"
            "Quando terminamos?"
        )
        self.assertEqual(
            [(unit.role, unit.text) for unit in units],
            [
                ("heading", "Chegar ao pronto: o problema das funcionalidades"),
                (
                    "paragraph",
                    "É comum confundir lançar funcionalidades com estar concluído.",
                ),
                ("paragraph", "Trabalhando juntos, fizemos três perguntas:"),
                ("paragraph", "A primeira pergunta tinha uma resposta."),
                ("heading", "Quando terminamos?"),
            ],
        )

    def test_reviewed_collapsed_source_labels_do_not_create_empty_units(self) -> None:
        aligned = _locutor_units(
            "COMPRADOR\n\nATIVIDADE 1\n\nATIVIDADE 2",
            "O mapa apresenta a raia do comprador e suas atividades.",
            allow_collapsed_source_units=True,
        )
        self.assertEqual(
            " ".join(spoken for _unit, spoken in aligned),
            "O mapa apresenta a raia do comprador e suas atividades.",
        )

    def test_reviewed_figure_labels_stay_in_one_spoken_locution(self) -> None:
        aligned = _locutor_units(
            "CRIAR EXPERIMENTOS\n\nMEDIR RESULTADOS\n\nAPRENDER",
            "O diagrama apresenta um ciclo de três etapas: criar experimentos, "
            "medir resultados e aprender.",
            allow_collapsed_source_units=True,
        )
        self.assertEqual(
            [(unit.role, spoken) for unit, spoken in aligned],
            [
                (
                    "paragraph",
                    "O diagrama apresenta um ciclo de três etapas: criar experimentos, "
                    "medir resultados e aprender.",
                )
            ],
        )

    def test_long_figure_description_is_prose_even_from_one_heading_unit(self) -> None:
        aligned = _locutor_units(
            "COMPRADOR ATIVIDADE 1 ATIVIDADE 2 ATIVIDADE 3",
            "O segundo mapa repete as três raias e acrescenta sinais de mais e menos "
            "a atividades selecionadas, indicando pontos positivos e negativos da jornada.",
            allow_collapsed_source_units=True,
        )
        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0][0].role, "paragraph")

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
