from __future__ import annotations

import json
import tempfile
from pathlib import Path

from publication_selection import (
    default_selection,
    legacy_selection,
    load_selection,
    require_narrator_base,
    require_text_edition,
    selection_path,
    uses_unsuffixed_fluid_export_name,
    write_selection,
)
from publish_artifacts import Publication, validate_audio_reader_edition


def expect_error(action: object, text: str) -> None:
    try:
        action()
    except RuntimeError as error:
        assert text in str(error), error
        return
    raise AssertionError("Expected RuntimeError")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="publication-selection-") as temporary:
        book_root = Path(temporary)
        assert load_selection(book_root) == legacy_selection()
        assert default_selection()["target"] == "complete"

        path = selection_path(book_root)
        (book_root / "metadata").mkdir(parents=True, exist_ok=True)
        (book_root / "metadata" / "book-map.json").write_text(
            '{"analysis":{"source_language":"pt-BR"}}\n',
            encoding="utf-8",
        )
        write_selection(path, "fluid", "codex", "Pajelança publication request.")
        assert require_text_edition(book_root, "fluid-pt-br")["target"] == "fluid"
        assert require_narrator_base(book_root, "fluid-pt-br")["target"] == "fluid"
        assert uses_unsuffixed_fluid_export_name(book_root, "fluid-pt-br")
        assert not uses_unsuffixed_fluid_export_name(book_root, "original")
        expect_error(
            lambda: require_text_edition(book_root, "original"),
            "does not allow text edition",
        )
        expect_error(
            lambda: require_narrator_base(book_root, "source"),
            "does not allow narrator base edition",
        )

        write_selection(path, "complete", "codex", "Faithful publication request.")
        assert require_text_edition(book_root, "original")["target"] == "complete"
        assert require_text_edition(book_root, "translated-pt-br")["target"] == "complete"
        assert require_narrator_base(book_root, "source")["target"] == "complete"
        expect_error(
            lambda: require_text_edition(book_root, "fluid-pt-br"),
            "does not allow text edition",
        )
        for language in ("Portuguese", "por"):
            (book_root / "metadata" / "book-map.json").write_text(
                json.dumps({"analysis": {"source_language": language}}) + "\n",
                encoding="utf-8",
            )
            assert require_text_edition(book_root, "original")["target"] == "complete"
            assert require_narrator_base(book_root, "source")["target"] == "complete"

        write_selection(path, "both", "codex", "Publish both approved editions.")
        assert not uses_unsuffixed_fluid_export_name(book_root, "fluid-pt-br")
        for text_edition in ("original", "translated-pt-br", "fluid-pt-br"):
            require_text_edition(book_root, text_edition)
        for base_edition in ("source", "translated-pt-br", "fluid-pt-br"):
            require_narrator_base(book_root, base_edition)

        (book_root / "metadata" / "book-map.json").write_text(
            '{"analysis":{"source_language":"en"}}\n',
            encoding="utf-8",
        )
        expect_error(
            lambda: require_text_edition(book_root, "original"),
            "must publish translated-pt-br",
        )
        expect_error(
            lambda: require_narrator_base(book_root, "source"),
            "must publish translated-pt-br",
        )
        require_text_edition(book_root, "translated-pt-br")
        require_narrator_base(book_root, "translated-pt-br")

        audio = Publication(
            "audio",
            book_root / "audio.mp3",
            book_root / "audio.mp3",
            {"text_edition": "translated-pt-br"},
        )
        epub = Publication(
            "epub",
            book_root / "book.epub",
            book_root / "book.epub",
            {"text_edition": "translated-pt-br"},
        )
        validate_audio_reader_edition([audio, epub])
        expect_error(
            lambda: validate_audio_reader_edition(
                [audio, Publication("pdf", book_root / "book.pdf", book_root / "book.pdf", {"text_edition": "original"})]
            ),
            "must match",
        )
    print("Publication selection focused tests passed.")


if __name__ == "__main__":
    main()
