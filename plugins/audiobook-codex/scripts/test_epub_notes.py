from __future__ import annotations

from pathlib import Path
import tempfile

from export_epub import note_reference_markup, semantic_body_parts


def block(kind: str, start_line: int, end_line: int, **extra: object) -> dict:
    return {
        "kind": kind,
        "spans": [
            {
                "source_file": "text/source/pages/page-0001.txt",
                "start_line": start_line,
                "end_line": end_line,
            }
        ],
        **extra,
    }


def test_note_reference_markup_links_only_attached_markers() -> None:
    html = note_reference_markup(
        "Texto2 e 202 e 2 solto; palavra* e * solto; termo† e outro‡.",
        {"2": "note-2", "*": "note-star", "†": "note-dagger", "‡": "note-double-dagger"},
    )

    assert 'Texto<sup><a id="noteref-note-2" epub:type="noteref" href="#note-2">2</a></sup>' in html
    assert 'palavra<sup><a id="noteref-note-star" epub:type="noteref" href="#note-star">*</a></sup>' in html
    assert 'termo<sup><a id="noteref-note-dagger" epub:type="noteref" href="#note-dagger">†</a></sup>' in html
    assert 'outro<sup><a id="noteref-note-double-dagger" epub:type="noteref" href="#note-double-dagger">‡</a></sup>' in html
    assert "202" in html
    assert " e 2 solto" in html
    assert " e * solto" in html
    assert html.count('href="#note-2"') == 1
    assert html.count('href="#note-star"') == 1


def test_attached_symbol_after_punctuation_is_linked() -> None:
    html = note_reference_markup("Frase.* Outra frase", {"*": "note-star"})

    assert 'Frase.<sup><a id="noteref-note-star" epub:type="noteref" href="#note-star">*</a></sup>' in html


def test_numeric_marker_attached_to_full_date_is_linked_safely() -> None:
    html = note_reference_markup(
        "Data 15/11/19081 e número 202.",
        {"1": "note-1", "2": "note-2"},
        note_hrefs={"note-1": "other.xhtml#note-1"},
    )
    assert (
        '15/11/1908<sup><a id="noteref-note-1" epub:type="noteref" '
        'href="other.xhtml#note-1">1</a></sup>'
    ) in html
    assert "202" in html
    assert 'href="#note-2"' not in html


def test_cross_document_reference_and_backlink_targets() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        page = book_root / "text" / "source" / "pages" / "page-0001.txt"
        page.parent.mkdir(parents=True)
        page.write_text("Umbanda.4\n4 Nota cruzada.\n", encoding="utf-8")
        reference = "\n".join(
            semantic_body_parts(
                [block("paragraph", 1, 1)],
                book_root,
                [],
                global_note_ids={"4": "note-4"},
                note_hrefs={"note-4": "035-chapter-32.xhtml#note-4"},
                global_reference_targets={
                    "note-4": "034-chapter-31.xhtml#noteref-note-4"
                },
            )
        )
        body = "\n".join(
            semantic_body_parts(
                [block("note", 2, 2, id="note-4", marker="4")],
                book_root,
                [],
                global_note_ids={"4": "note-4"},
                note_hrefs={"note-4": "#note-4"},
                global_reference_targets={
                    "note-4": "034-chapter-31.xhtml#noteref-note-4"
                },
            )
        )
    assert 'href="035-chapter-32.xhtml#note-4"' in reference
    assert 'href="034-chapter-31.xhtml#noteref-note-4"' in body


def test_semantic_blocks_emit_bidirectional_note_links_without_flattening_layout() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        page = book_root / "text" / "source" / "pages" / "page-0001.txt"
        page.parent.mkdir(parents=True)
        page.write_text(
            "Título  com  espaço‡\n"
            "Fonte2 e 202 e 2 solto.\n"
            "Verso  com espaço†\n"
            "Fala*\n"
            "‡ Nota do título.\n"
            "2 Nota numérica.\n"
            "† Nota do verso.\n"
            "* Nota da fala.\n",
            encoding="utf-8",
        )
        parts = "\n".join(
            semantic_body_parts(
                [
                    block("heading", 1, 1, level=2),
                    block("paragraph", 2, 2),
                    block("verse", 3, 3),
                    block("dialogue", 4, 4),
                    block("note", 5, 5, id="note-double-dagger", marker="‡"),
                    block("note", 6, 6, id="note-2", marker="2"),
                    block("note", 7, 7, id="note-dagger", marker="†"),
                    block("note", 8, 8, id="note-star", marker="*"),
                ],
                book_root,
                [],
            )
        )

    assert '<h2 class="source-heading">' in parts
    assert 'class="verse-line"' in parts
    assert 'class="dialogue">Fala<sup><a id="noteref-note-star" epub:type="noteref" href="#note-star">*</a></sup></p>' in parts
    assert 'Título  com  espaço<sup><a id="noteref-note-double-dagger" epub:type="noteref" href="#note-double-dagger">‡</a></sup>' in parts
    assert 'Fonte<sup><a id="noteref-note-2" epub:type="noteref" href="#note-2">2</a></sup> e 202 e 2 solto.' in parts
    assert 'Verso  com espaço<sup><a id="noteref-note-dagger" epub:type="noteref" href="#note-dagger">†</a></sup>' in parts
    assert '<aside id="note-star" epub:type="footnote" class="footnote">' in parts
    assert '<sup><a epub:type="backlink" href="#noteref-note-star">*</a></sup> Nota da fala.' in parts
    assert '<sup><a epub:type="backlink" href="#noteref-note-2">2</a></sup> Nota numérica.' in parts


def test_revised_semantic_note_text_preserves_reference_and_backlink() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        book_root = Path(raw_root)
        page = book_root / "text" / "source" / "pages" / "page-0001.txt"
        page.parent.mkdir(parents=True)
        page.write_text("Texto antigo2\n2 Nota antiga.\n", encoding="utf-8")
        parts = "\n".join(
            semantic_body_parts(
                [
                    block("paragraph", 1, 1),
                    block("note", 2, 2, id="note-2", marker="2"),
                ],
                book_root,
                [],
                [
                    {
                        "id": "revision-reference",
                        "source_span": "Texto antigo2",
                        "revised_span": "Texto revisado2",
                    },
                    {
                        "id": "revision-note",
                        "source_span": "Nota antiga.",
                        "revised_span": "Nota revisada.",
                    },
                ],
            )
        )

    assert 'Texto revisado<sup><a id="noteref-note-2" epub:type="noteref" href="#note-2">2</a></sup>' in parts
    assert '<sup><a epub:type="backlink" href="#noteref-note-2">2</a></sup> Nota revisada.' in parts


def run_tests() -> None:
    test_note_reference_markup_links_only_attached_markers()
    test_attached_symbol_after_punctuation_is_linked()
    test_numeric_marker_attached_to_full_date_is_linked_safely()
    test_cross_document_reference_and_backlink_targets()
    test_semantic_blocks_emit_bidirectional_note_links_without_flattening_layout()
    test_revised_semantic_note_text_preserves_reference_and_backlink()


if __name__ == "__main__":
    run_tests()
