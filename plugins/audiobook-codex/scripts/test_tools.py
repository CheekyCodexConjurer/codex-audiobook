from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import wave
import zipfile

from validate_epub_export import validate_epub_document_texts


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> None:
    run_with_python(sys.executable, *args)


def run_with_python(python: str, *args: str) -> None:
    normalized = list(args)
    for index, value in enumerate(normalized[:-1]):
        if value == "--book-root":
            candidate = Path(normalized[index + 1])
            if candidate.name.casefold() == "assembly":
                normalized[index + 1] = str(candidate.parent)
    completed = subprocess.run([python, *normalized], text=True, capture_output=True)
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(args)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_fails(*args: str) -> None:
    normalized = list(args)
    for index, value in enumerate(normalized[:-1]):
        if value == "--book-root":
            candidate = Path(normalized[index + 1])
            if candidate.name.casefold() == "assembly":
                normalized[index + 1] = str(candidate.parent)
    completed = subprocess.run([sys.executable, *normalized], text=True, capture_output=True)
    if completed.returncode == 0:
        raise AssertionError(f"Command unexpectedly succeeded: {' '.join(args)}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def translation_ledger_for(
    book_map_path: Path,
    source_ledger_path: Path,
    source_ledger: dict,
    text_root: Path,
    source_language: str,
    book_title: str,
    document_title_by_id: dict[str, str],
) -> dict:
    pages = []
    for source_page in source_ledger["pages"]:
        translation_page = (
            text_root
            / "translation"
            / "pt-BR"
            / "pages"
            / Path(source_page["source_file"]).name
        )
        translation_page.parent.mkdir(parents=True, exist_ok=True)
        translation_page.write_text(
            f"Texto traduzido da pagina {source_page['logical_page']}.",
            encoding="utf-8",
        )
        pages.append(
            {
                "logical_page": source_page["logical_page"],
                "status": source_page["status"],
                "source_file": source_page["source_file"],
                "source_sha256": source_page["source_sha256"],
                "translation_file": translation_page.relative_to(text_root).as_posix(),
                "translation_sha256": sha256_file(translation_page),
                "translated_by": "codex",
                "reviewed_by": "codex",
                "notes": "",
            }
        )

    chapter_outputs = []
    for source_output in source_ledger["chapter_outputs"]:
        translation_file = (
            text_root
            / "translation"
            / "pt-BR"
            / "chapters"
            / Path(source_output["source_file"]).name
        )
        translation_file.parent.mkdir(parents=True, exist_ok=True)
        translation_file.write_text(
            f"{document_title_by_id[source_output['id']]}\n\nTexto traduzido para PT-BR.",
            encoding="utf-8",
        )
        chapter_outputs.append(
            {
                "id": source_output["id"],
                "source_file": source_output["source_file"],
                "source_sha256": source_output["source_sha256"],
                "translation_file": translation_file.relative_to(text_root).as_posix(),
                "translation_sha256": sha256_file(translation_file),
                "source_pages": copy.deepcopy(source_output["source_pages"]),
                "translated_by": "codex",
                "reviewed_by": "codex",
            }
        )

    return {
        "schema_version": "1.1",
        "book_map_sha256": sha256_file(book_map_path),
        "text_ledger_sha256": sha256_file(source_ledger_path),
        "source_language": source_language,
        "target_language": "pt-BR",
        "translation_decision": {
            "scope": "whole-book",
            "reason": "The source work is fully non-Portuguese.",
            "reviewed_by": "codex",
            "evidence": [
                {
                    "logical_page": source_page["logical_page"],
                    "source_sha256": source_page["source_sha256"],
                    "source_span": (
                        text_root / source_page["source_file"]
                    ).read_text(encoding="utf-8"),
                    "reason": "Reviewed as part of the whole source-language decision.",
                }
                for source_page in source_ledger["pages"]
                if source_page["status"] == "verified"
            ],
        },
        "translation_quality": {
            "profile": "faithful-contextual-ptbr-v1",
            "context_policy": "whole-chapter-with-neighbors-v1",
            "research_policy": "context-first-evidence-recorded-v1",
            "brief": {
                "genre": "Test fixture",
                "period": "Test fixture",
                "setting": "Test fixture",
                "narrator_voice": "Test narrator",
                "register": "Test register",
                "style_goals": "Preserve fixture meaning and voice.",
                "names_policy": "Preserve fixture names.",
                "foreign_fragments_policy": "Preserve intentional fixture fragments.",
                "reviewed_by": "codex-verifier",
            },
            "glossary": [],
            "ambiguities": [],
            "review": {
                "semantic_fidelity": "approved",
                "literary_naturalness": "approved",
                "whole_book_consistency": "approved",
                "independent": True,
                "reviewed_by": "codex-verifier",
            },
        },
        "pages": pages,
        "chapter_outputs": chapter_outputs,
        "edition": {
            "book": {"title": book_title, "subtitle": ""},
            "document_titles": [
                {"id": output_id, "title": title}
                for output_id, title in document_title_by_id.items()
            ],
        },
    }


def write_epub(path: Path) -> None:
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL4"
        "xAAAAABJRU5ErkJggg=="
    )
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    package = """<?xml version="1.0" encoding="utf-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="chapter-1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter-2" href="chapter-2.xhtml" media-type="application/xhtml+xml"/>
    <item id="image-illustration" href="images/a-illustration.png" media-type="image/png"/>
    <item id="image-cover" href="images/z-cover.png" media-type="image/png" properties="cover-image"/>
  </manifest>
  <spine><itemref idref="chapter-1"/><itemref idref="chapter-2"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr(
            "OEBPS/chapter-1.xhtml",
            '<html><body><p>Um.</p><img src="images/a-illustration.png" alt=""/></body></html>',
        )
        archive.writestr("OEBPS/chapter-2.xhtml", "<html><body><p>Dois.</p></body></html>")
        archive.writestr("OEBPS/images/a-illustration.png", image)
        archive.writestr("OEBPS/images/z-cover.png", image)


def write_pdf_with_image(path: Path, image_path: Path) -> None:
    from PIL import Image

    image = Image.new("RGB", (16, 12), color=(40, 80, 120))
    image.save(image_path, "JPEG")
    image.save(path, "PDF", resolution=72.0)


def create_junction(link: Path, target: Path) -> None:
    command = subprocess.list2cmdline(["mklink", "/J", str(link), str(target)])
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", command],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Could not create test junction {link}: {completed.stderr or completed.stdout}"
        )


def main() -> None:
    from audio_tools import (
        DEFAULT_PUBLICATION_TEMPO,
        SAMPLE_RATE,
        apply_publication_tempo,
        join_wavs,
        transcode,
        validate_publication_tempo,
        validate_speech_wav,
        write_wav,
    )
    from chapter_audio import assemble_chapters
    from epub_layout import validate_layout
    from path_safety import resolve_under
    from pypdf import PdfWriter
    from chatterbox_text import DEFAULT_MAX_CHARS, NarratorTextError, prepare_chatterbox_segments
    from narration_plan import (
        SourceUnit,
        _locutor_units,
        _segments_for_unit,
        _source_units,
        normalized_text as narration_normalized_text,
    )
    from narrator_quality import (
        QUALITY_PROFILE,
        audit_text,
        classify_finding,
        classify_locution_line,
        draft_review,
        roman_to_pt_br,
    )
    import render_chatterbox as render_chatterbox_module
    from render_chatterbox import (
        AUDIO_PROMPT_PER_GENERATE_STRATEGY,
        DEFAULT_MODEL_ROOT,
        DEFAULT_REFERENCE_VOICE,
        DEFAULT_VOICE_PROFILE,
        DEFAULT_VOICE_PROFILE_NAME,
        FEMININA_PROFILE,
        FEMININA_PROFILE_CALIBRATION,
        FEMININA_REFERENCE_VOICE,
        FIXED_PER_SEGMENT_SEED_STRATEGY,
        MASCULINA_PROFILE,
        MASCULINA_PROFILE_CALIBRATION,
        MASCULINA_REFERENCE_VOICE,
        PER_SEGMENT_INDEX_SEED_STRATEGY,
        PRECOMPUTED_CONDITIONALS_STRATEGY,
        VOICE_PROFILES,
        compatible_render_identity,
        copy_or_link_atomically,
        load_render_journal,
        new_render_journal,
        prepare_reflow_reuse_sources,
        reflow_reuse_provenance,
        reusable_segment_record,
        selected_profile,
        segment_record,
        segment_seed,
    )
    from validate_book_map import validate_book_map
    from validate_chapter_audio import validate_chapter_audio
    from validate_narrator_quality import validate_review

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    calibration_text = (
        "Na manhã de junho, a chuva fina cobria o jardim, enquanto a brisa movia "
        "lentamente as folhas. O relógio marcou oito e trinta. João abriu a janela "
        'e perguntou: "Quem deixou a pequena caixa azul junto à porta?" Após um breve '
        'silêncio, respirou devagar e disse: "Muito bem. Hoje começa uma nova história."'
    )
    calibration_segments = prepare_chatterbox_segments(calibration_text, DEFAULT_MAX_CHARS)
    assert len(calibration_text) == 302
    assert len(calibration_segments) == 1
    assert calibration_segments[0].text == calibration_text
    assert FEMININA_PROFILE["max_chars"] == DEFAULT_MAX_CHARS
    assert FEMININA_PROFILE["silence_seconds"] == 0.22
    assert FEMININA_PROFILE["min_p"] == 0.114
    assert FEMININA_PROFILE["seed"] == 20260713
    assert FEMININA_PROFILE_CALIBRATION["winner_id"] == "minp-0-114-temp-0-80"
    assert (
        VOICE_PROFILES["feminina-v1"]["conditioning_strategy"]
        == PRECOMPUTED_CONDITIONALS_STRATEGY
    )
    assert (
        VOICE_PROFILES["feminina-v1"]["seed_strategy"]
        == PER_SEGMENT_INDEX_SEED_STRATEGY
    )
    assert MASCULINA_PROFILE["exaggeration"] == 0.5
    assert MASCULINA_PROFILE["cfg_weight"] == 0.35
    assert MASCULINA_PROFILE["seed"] == 54321
    assert MASCULINA_PROFILE_CALIBRATION["winner_id"] == "seed54321-base"
    assert DEFAULT_VOICE_PROFILE_NAME == "masculina-v1"
    assert DEFAULT_VOICE_PROFILE is VOICE_PROFILES[DEFAULT_VOICE_PROFILE_NAME]
    assert DEFAULT_REFERENCE_VOICE == MASCULINA_REFERENCE_VOICE
    assert (
        VOICE_PROFILES["masculina-v1"]["conditioning_strategy"]
        == AUDIO_PROMPT_PER_GENERATE_STRATEGY
    )
    assert (
        VOICE_PROFILES["masculina-v1"]["seed_strategy"]
        == FIXED_PER_SEGMENT_SEED_STRATEGY
    )
    profile_args = SimpleNamespace(
        max_chars=FEMININA_PROFILE["max_chars"],
        silence_seconds=FEMININA_PROFILE["silence_seconds"],
        exaggeration=FEMININA_PROFILE["exaggeration"],
        cfg_weight=FEMININA_PROFILE["cfg_weight"],
        temperature=FEMININA_PROFILE["temperature"],
        repetition_penalty=FEMININA_PROFILE["repetition_penalty"],
        min_p=FEMININA_PROFILE["min_p"],
        top_p=FEMININA_PROFILE["top_p"],
        seed=FEMININA_PROFILE["seed"],
    )
    calibrated_model = FEMININA_PROFILE_CALIBRATION["model"]
    calibrated_version = FEMININA_PROFILE_CALIBRATION["chatterbox_tts_version"]
    assert (
        selected_profile(
            profile_args,
            FEMININA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            calibrated_model,
            calibrated_version,
        )
        == "feminina-v1"
    )
    masculina_args = SimpleNamespace(
        max_chars=MASCULINA_PROFILE["max_chars"],
        silence_seconds=MASCULINA_PROFILE["silence_seconds"],
        exaggeration=MASCULINA_PROFILE["exaggeration"],
        cfg_weight=MASCULINA_PROFILE["cfg_weight"],
        temperature=MASCULINA_PROFILE["temperature"],
        repetition_penalty=MASCULINA_PROFILE["repetition_penalty"],
        min_p=MASCULINA_PROFILE["min_p"],
        top_p=MASCULINA_PROFILE["top_p"],
        seed=MASCULINA_PROFILE["seed"],
    )
    assert (
        selected_profile(
            masculina_args,
            MASCULINA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            MASCULINA_PROFILE_CALIBRATION["model"],
            MASCULINA_PROFILE_CALIBRATION["chatterbox_tts_version"],
        )
        == "masculina-v1"
    )
    masculina_args.seed = 1
    assert (
        selected_profile(
            masculina_args,
            MASCULINA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            MASCULINA_PROFILE_CALIBRATION["model"],
            MASCULINA_PROFILE_CALIBRATION["chatterbox_tts_version"],
        )
        == "custom"
    )
    profile_args.silence_seconds = 0.0
    assert (
        selected_profile(
            profile_args,
            FEMININA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            calibrated_model,
            calibrated_version,
        )
        == "custom"
    )
    assert compatible_render_identity(
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "a"},
            "generation": {
                "seed": 1,
                "seed_strategy": PER_SEGMENT_INDEX_SEED_STRATEGY,
            },
        },
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "b"},
            "generation": {
                "seed": 1,
                "seed_strategy": PER_SEGMENT_INDEX_SEED_STRATEGY,
            },
        },
    )
    assert not compatible_render_identity(
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "a"},
            "generation": {
                "seed": 1,
                "seed_strategy": PER_SEGMENT_INDEX_SEED_STRATEGY,
            },
        },
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "b"},
            "generation": {
                "seed": 2,
                "seed_strategy": PER_SEGMENT_INDEX_SEED_STRATEGY,
            },
        },
    )
    assert not compatible_render_identity(
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "a"},
            "generation": {
                "seed": 1,
                "seed_strategy": PER_SEGMENT_INDEX_SEED_STRATEGY,
            },
        },
        {
            "engine": "test",
            "runtime": {"renderer_sha256": "b"},
            "generation": {
                "seed": 1,
                "seed_strategy": FIXED_PER_SEGMENT_SEED_STRATEGY,
            },
        },
    )
    profile_args.silence_seconds = FEMININA_PROFILE["silence_seconds"]
    assert (
        selected_profile(
            profile_args,
            FEMININA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cpu",
            calibrated_model,
            calibrated_version,
        )
        == "custom"
    )
    altered_model = dict(calibrated_model)
    altered_model["t3_sha256"] = "0" * 64
    assert (
        selected_profile(
            profile_args,
            FEMININA_REFERENCE_VOICE.resolve(),
            DEFAULT_MODEL_ROOT.resolve(),
            "cuda",
            altered_model,
            calibrated_version,
        )
        == "custom"
    )
    line_segments = prepare_chatterbox_segments(
        "Primeira linha completa.\n\nSegunda linha completa.",
        DEFAULT_MAX_CHARS,
    )
    assert [(item.line_number, item.text) for item in line_segments] == [
        (1, "Primeira linha completa."),
        (3, "Segunda linha completa."),
    ]
    leading_line_segments = prepare_chatterbox_segments(
        "\n\nPrimeira linha completa.",
        DEFAULT_MAX_CHARS,
    )
    assert leading_line_segments[0].line_number == 3
    for invalid_text in (
        "[thoughtful] Texto.",
        "[thoughtful Texto.",
        "<emphasis>Texto</emphasis>.",
        "**Texto.**",
        "*Texto em itálico.*",
        "_Texto em itálico_.",
        "~~Texto riscado~~.",
        "`Texto em código`.",
        "# Título.",
        "> Citação.",
        "- Item de lista.",
        "+ Outro item.",
        "---",
        "A | B",
        "O relógio marcou 8 horas.",
        "Dr. João chegou.",
        "Visite https://example.com.",
        "Visite exemplo.com.",
        "Isso e etc.",
        "XII.",
        "Capítulo XXIII.",
        "X" * (DEFAULT_MAX_CHARS + 1),
    ):
        try:
            prepare_chatterbox_segments(invalid_text, DEFAULT_MAX_CHARS)
        except NarratorTextError:
            pass
        else:
            raise AssertionError(f"Expected Chatterbox narrator text to fail: {invalid_text!r}")
    assert [item.text for item in prepare_chatterbox_segments("civil.\nCapítulo civil.", DEFAULT_MAX_CHARS)] == [
        "civil.",
        "Capítulo civil.",
    ]
    fluent_paragraph = (
        "Como tudo tem a hora certa para acontecer, me parece que os Cem Anos de Umbanda "
        "marca um interesse renovado dos umbandistas pela história. Da Umbanda e como não "
        "poderia deixar de ser Leal de Souza é o principal marco, depois de Zélio de Moraes "
        "e o Caboclo das Sete Encruzilhadas."
    )
    fluent_segments = _segments_for_unit(
        SourceUnit(fluent_paragraph, "paragraph"),
        fluent_paragraph,
        DEFAULT_MAX_CHARS,
    )
    assert fluent_segments == [(fluent_paragraph, "paragraph")]
    long_sentence = (
        "Esta é uma frase deliberadamente longa; A segunda oração começa em maiúscula e mantém "
        "o sentido. A terceira oração também começa em maiúscula e permite uma quebra segura "
        "quando o limite do narrador for alcançado."
    )
    long_segments = _segments_for_unit(
        SourceUnit(long_sentence, "paragraph"),
        long_sentence,
        120,
    )
    assert all(len(text) <= 120 for text, _ in long_segments)
    assert narration_normalized_text(" ".join(text for text, _ in long_segments)) == long_sentence
    expansion_units = _locutor_units(
        "Antes 1925.\n1 Nota final.",
        "Antes mil novecentos e vinte e cinco. Nota um. Nota final.",
    )
    assert "mil novecentos e vinte e cinco" in expansion_units[0][1]
    assert not expansion_units[0][1].endswith(" e")
    wrapped_dialogue_units = _source_units(
        "- Você acha que o espiritismo não pode ser pago. Mas quem não tem\n"
        "emprego, como é que há de fazer espiritismo?\n"
        "E, continuando, desenvolveu o argumento."
    )
    assert [
        (unit.text, unit.role)
        for unit in wrapped_dialogue_units
    ] == [
        (
            "- Você acha que o espiritismo não pode ser pago. Mas quem não tem "
            "emprego, como é que há de fazer espiritismo?",
            "dialogue",
        ),
        ("E, continuando, desenvolveu o argumento.", "paragraph"),
    ]

    quality_text = (
        "XXII.\n"
        "Capítulo XXIII.\n"
        "Ele respondeu.. Sim!.\n"
        "vi o caminho.\n"
        "mil histórias de uma cidade civil.\n"
        "Sr. João chegou em 12/03/2026 às 14:30, com 15%.\n"
        "Esta frase continua\n"
        "em outra linha.\n"
        "— Quem está aí?.\n"
        "Verso terminado.\n"
        "> Citação encerrada..\n"
        "ABC."
    )
    quality_findings = audit_text(quality_text)
    quality_lines = quality_text.splitlines()
    quality_kinds = [finding.kind for finding in quality_findings]
    assert quality_kinds.count("roman_heading") == 1
    assert quality_kinds.count("labelled_roman_numeral") == 1
    assert quality_kinds.count("punctuation_cluster") == 4
    assert "abbreviation" in quality_kinds
    assert "date_or_time" in quality_kinds
    assert "raw_number" in quality_kinds
    assert "spoken_symbol" in quality_kinds
    assert any(
        finding.kind == "line_boundary" and finding.locutor_span == "em outra linha."
        for finding in quality_findings
    )
    assert any(
        finding.kind == "uppercase_token" and finding.locutor_span == "ABC"
        for finding in quality_findings
    )
    assert roman_to_pt_br("XXII") == "vinte e dois"
    assert roman_to_pt_br("Xxiii") == "vinte e três"
    assert roman_to_pt_br("civil") is None
    assert not any(
        finding.kind in {"roman_heading", "labelled_roman_numeral"}
        and finding.line_number in {4, 5}
        for finding in quality_findings
    )
    assert classify_finding(quality_findings[0], quality_lines) == "heading"
    assert any(
        classify_finding(finding, quality_lines) == "dialogue"
        for finding in quality_findings
        if finding.line_number == 9
    )
    assert any(
        classify_finding(finding, quality_lines) == "quotation"
        for finding in quality_findings
        if finding.line_number == 11
    )
    assert classify_locution_line(["Capítulo vinte e dois."], 0) == "heading"
    assert classify_locution_line(["— Uma fala completa."], 0) == "dialogue"
    assert classify_locution_line(['"Uma citação."'], 0) == "quotation"
    assert classify_locution_line(["Nota do editor."], 0) == "note"
    assert classify_locution_line(["1. Primeiro item."], 0) == "list"
    assert classify_locution_line(["Primeiro verso", "Segundo verso"], 0) == "verse"
    assert classify_locution_line(["O PDF E O EPUB ESTÃO PRONTOS."], 0) == "prose"
    assert classify_locution_line([""], 0) == "excluded"

    with tempfile.TemporaryDirectory(prefix="audiobook-codex-test-") as temporary:
        root = Path(temporary)
        resume_output = root / "resume-output"
        resume_segments = resume_output / "segments"
        resume_segments.mkdir(parents=True)
        resume_segment_path = resume_segments / "segment-0001.wav"
        speech_frames = b"\x00\x10" * SAMPLE_RATE
        silent_frames = b"\x00\x00" * SAMPLE_RATE
        write_wav(resume_segment_path, speech_frames)
        joined_path = root / "variable-pauses.wav"
        second_joined_path = root / "variable-pauses-second.wav"
        write_wav(second_joined_path, speech_frames)
        silent_segment_path = resume_output / "segments" / "silent.wav"
        write_wav(silent_segment_path, silent_frames)
        try:
            validate_speech_wav(silent_segment_path)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected silent WAV to fail speech validation.")
        speech_metrics = validate_speech_wav(resume_segment_path)
        assert speech_metrics["peak_rms"] > 180
        assert speech_metrics["longest_silence_seconds"] == 0
        assert speech_metrics["voiced_ratio"] == 1.0
        mostly_silent_path = resume_output / "segments" / "mostly-silent.wav"
        mostly_silent_frames = bytearray()
        for _ in range(10):
            mostly_silent_frames.extend(b"\x00\x10" * int(SAMPLE_RATE * 0.05))
            mostly_silent_frames.extend(b"\x00\x00" * int(SAMPLE_RATE * 0.95))
        write_wav(mostly_silent_path, bytes(mostly_silent_frames))
        try:
            validate_speech_wav(mostly_silent_path)
        except RuntimeError as error:
            assert "voiced ratio" in str(error)
        else:
            raise AssertionError("Expected mostly silent WAV to fail speech validation.")
        retry_segment = SimpleNamespace(
            line_number=7,
            text="Retry render.",
            warnings=(),
        )
        retry_path = resume_output / "segments" / "retry.wav"
        render_calls: list[int] = []
        seed_calls: list[tuple[int | None, str]] = []
        original_render_segment = render_chatterbox_module.render_segment
        original_seed_torch = render_chatterbox_module.seed_torch
        try:
            def fake_seed_torch(value: int | None, device: str) -> None:
                seed_calls.append((value, device))

            def fake_render_segment(*args: object, **kwargs: object) -> None:
                target = args[2]
                if not isinstance(target, Path):
                    raise AssertionError("Expected render target path.")
                render_calls.append(len(render_calls) + 1)
                write_wav(target, silent_frames if len(render_calls) == 1 else speech_frames)

            render_chatterbox_module.seed_torch = fake_seed_torch
            render_chatterbox_module.render_segment = fake_render_segment
            accepted_seed, attempts, rendered_audio = (
                render_chatterbox_module.render_segment_with_retries(
                segment_index=7,
                model=object(),
                text=retry_segment.text,
                target=retry_path,
                voice_reference=root / "voice.wav",
                conditioning_strategy=AUDIO_PROMPT_PER_GENERATE_STRATEGY,
                exaggeration=0.0,
                cfg_weight=0.0,
                temperature=0.0,
                repetition_penalty=0.0,
                min_p=0.0,
                top_p=0.0,
                seed=100,
                device="cpu",
                )
            )
        finally:
            render_chatterbox_module.render_segment = original_render_segment
            render_chatterbox_module.seed_torch = original_seed_torch
        assert accepted_seed == render_chatterbox_module.render_retry_seed(100, 7, 1)
        assert seed_calls == [(100, "cpu"), (accepted_seed, "cpu")]
        assert attempts[0]["status"] == "rejected"
        assert attempts[0]["seed"] == 100
        assert "reason" in attempts[0]
        assert attempts[1]["status"] == "accepted"
        assert rendered_audio["audio_sha256"] == sha256_file(retry_path)
        render_calls.clear()
        seed_calls.clear()
        try:
            render_chatterbox_module.seed_torch = fake_seed_torch
            render_chatterbox_module.render_segment = fake_render_segment
            accepted_seed, attempts, rendered_audio = (
                render_chatterbox_module.render_segment_with_retries(
                segment_index=1,
                model=object(),
                text=retry_segment.text,
                target=retry_path,
                voice_reference=root / "voice.wav",
                conditioning_strategy=PRECOMPUTED_CONDITIONALS_STRATEGY,
                exaggeration=0.0,
                cfg_weight=0.0,
                temperature=0.0,
                repetition_penalty=0.0,
                min_p=0.0,
                top_p=0.0,
                seed=100,
                device="cpu",
                skip_initial_seed=True,
                )
            )
        finally:
            render_chatterbox_module.render_segment = original_render_segment
            render_chatterbox_module.seed_torch = original_seed_torch
        assert accepted_seed == render_chatterbox_module.render_retry_seed(100, 1, 1)
        assert seed_calls == [(accepted_seed, "cpu")]
        assert attempts[0]["status"] == "rejected"
        assert attempts[1]["status"] == "accepted"
        assert rendered_audio["audio_sha256"] == sha256_file(retry_path)
        retry_record = segment_record(
            7,
            retry_segment,
            retry_path,
            resume_output,
            accepted_seed,
            attempts,
        )
        assert reusable_segment_record(
            retry_record,
            7,
            retry_segment,
            retry_path,
            resume_output,
            100,
        )
        joined_duration = join_wavs(
            [resume_segment_path, second_joined_path],
            joined_path,
            boundary_pauses=[0.05],
        )
        assert abs(joined_duration - 2.05) < 0.0001
        tempo_path = root / "publication-tempo.wav"
        apply_publication_tempo(joined_path, tempo_path, DEFAULT_PUBLICATION_TEMPO)
        with wave.open(str(tempo_path), "rb") as rendered:
            tempo_duration = rendered.getnframes() / rendered.getframerate()
        assert abs(tempo_duration - joined_duration / DEFAULT_PUBLICATION_TEMPO) < 0.05
        assert validate_publication_tempo(1.10) == 1.1
        try:
            validate_publication_tempo(0)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected invalid publication tempo to fail.")
        chapter_book = root / "chapter-book"
        chapter_output = chapter_book / "audio" / "chatterbox"
        chapter_segments = chapter_output / "segments"
        chapter_segments.mkdir(parents=True)
        chapter_metadata = chapter_book / "metadata"
        chapter_metadata.mkdir(parents=True)
        first_chapter_wav = chapter_segments / "segment-0001.wav"
        second_chapter_wav = chapter_segments / "segment-0002.wav"
        third_chapter_wav = chapter_segments / "segment-0003.wav"
        write_wav(first_chapter_wav, speech_frames)
        write_wav(second_chapter_wav, speech_frames)
        write_wav(third_chapter_wav, speech_frames)
        chapter_plan = {
            "segments": [
                {
                    "id": "chapter-01-0001",
                    "index": 1,
                    "text_sha256": "a" * 64,
                    "pause_after": {"kind": "sentence", "seconds": 0.05},
                    "source": {
                        "base_output_id": "chapter-01",
                        "locutor_chapter": "locutor/chapters/chapter-01.txt",
                        "logical_pages": [1],
                    },
                },
                {
                    "id": "chapter-01-0002",
                    "index": 2,
                    "text_sha256": "b" * 64,
                    "pause_after": {"kind": "paragraph", "seconds": 0.42},
                    "source": {
                        "base_output_id": "chapter-01",
                        "locutor_chapter": "locutor/chapters/chapter-01.txt",
                        "logical_pages": [1],
                    },
                },
                {
                    "id": "chapter-02-0001",
                    "index": 3,
                    "text_sha256": "c" * 64,
                    "pause_after": {"kind": "end", "seconds": 0.0},
                    "source": {
                        "base_output_id": "chapter-02",
                        "locutor_chapter": "locutor/chapters/chapter-02.txt",
                        "logical_pages": [2],
                    },
                },
            ]
        }
        chapter_journal = {
            "schema_version": "2.0",
            "segment_render_identity": {"engine": "test"},
            "segments": [
                {
                    "index": 1,
                    "semantic_id": "chapter-01-0001",
                    "text_sha256": "a" * 64,
                    "path": "segments/segment-0001.wav",
                    "audio_sha256": sha256_file(first_chapter_wav),
                },
                {
                    "index": 2,
                    "semantic_id": "chapter-01-0002",
                    "text_sha256": "b" * 64,
                    "path": "segments/segment-0002.wav",
                    "audio_sha256": sha256_file(second_chapter_wav),
                },
            ],
        }
        chapter_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            chapter_plan,
            chapter_journal,
        )
        completed_chapter = next(
            entry for entry in chapter_manifest["chapters"] if entry["id"] == "chapter-01"
        )
        assert completed_chapter["status"] == "complete"
        assert completed_chapter["audio"]["master_duration_seconds"] == 2.05
        assert (
            abs(
                completed_chapter["audio"]["duration_seconds"]
                - 2.05 / DEFAULT_PUBLICATION_TEMPO
            )
            < 0.05
        )
        assert (chapter_output / "chapters" / "original" / "chapter-01.wav").is_file()
        assert (chapter_output / "chapters" / "final" / "chapter-01.mp3").is_file()
        assert (chapter_output / "chapters" / "temp").is_dir()
        assert not list((chapter_output / "chapters" / "original").glob(".*.tmp*"))
        assert not list((chapter_output / "chapters" / "final").glob(".*.tmp*"))
        incomplete_chapter = next(
            entry for entry in chapter_manifest["chapters"] if entry["id"] == "chapter-02"
        )
        assert incomplete_chapter["status"] == "incomplete"
        assert not validate_chapter_audio(
            chapter_book,
            chapter_output,
            chapter_plan,
            chapter_journal,
            chapter_manifest,
        )
        unplanned_silent_wav = chapter_segments / "segment-0099.wav"
        write_wav(unplanned_silent_wav, silent_frames)
        invalid_incomplete_journal = copy.deepcopy(chapter_journal)
        invalid_incomplete_journal["segments"].append(
            {
                "index": 99,
                "semantic_id": "unplanned-silent",
                "text_sha256": "9" * 64,
                "path": "segments/segment-0099.wav",
                "audio_sha256": sha256_file(unplanned_silent_wav),
            }
        )
        assert any(
            "journal segment 99 WAV is invalid" in error
            for error in validate_chapter_audio(
                chapter_book,
                chapter_output,
                chapter_plan,
                invalid_incomplete_journal,
                chapter_manifest,
            )
        )
        outside_chapter_output = root / "outside-chapter-output"
        try:
            assemble_chapters(
                chapter_book,
                outside_chapter_output,
                chapter_plan,
                chapter_journal,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected outside chapter output to fail.")
        assert not outside_chapter_output.exists()
        assert validate_chapter_audio(
            chapter_book,
            outside_chapter_output,
            chapter_plan,
            chapter_journal,
            chapter_manifest,
        )
        junction_chapter_output = chapter_book / "audio" / "junction-output"
        junction_segments = junction_chapter_output / "segments"
        junction_segments.mkdir(parents=True)
        for source in (first_chapter_wav, second_chapter_wav, third_chapter_wav):
            shutil.copy2(source, junction_segments / source.name)
        chapter_junction_target = root / "chapter-junction-target"
        chapter_junction_target.mkdir()
        create_junction(junction_chapter_output / "chapters", chapter_junction_target)
        try:
            assemble_chapters(
                chapter_book,
                junction_chapter_output,
                chapter_plan,
                chapter_journal,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected chapter layout junction to fail.")
        assert not (chapter_junction_target / "original").exists()
        assert not (chapter_junction_target / "final").exists()
        assert not (chapter_junction_target / "temp").exists()
        repeated_chapter_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            chapter_plan,
            chapter_journal,
            ["chapter-01"],
        )
        assert repeated_chapter_manifest["chapters"][0]["audio"] == completed_chapter["audio"]
        unsafe_chapter_plan = copy.deepcopy(chapter_plan)
        unsafe_chapter_plan["segments"][0]["source"]["base_output_id"] = "../audiobook"
        try:
            assemble_chapters(
                chapter_book,
                chapter_output,
                unsafe_chapter_plan,
                chapter_journal,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected unsafe chapter ID to fail.")
        complete_chapter_journal = copy.deepcopy(chapter_journal)
        complete_chapter_journal["segments"].append(
            {
                "index": 3,
                "semantic_id": "chapter-02-0001",
                "text_sha256": "c" * 64,
                "path": "segments/segment-0003.wav",
                "audio_sha256": sha256_file(third_chapter_wav),
            }
        )
        escaped_record_journal = copy.deepcopy(complete_chapter_journal)
        escaped_record_journal["segments"][0]["path"] = "../segment-0001.wav"
        escaped_record_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            chapter_plan,
            escaped_record_journal,
        )
        assert next(
            entry
            for entry in escaped_record_manifest["chapters"]
            if entry["id"] == "chapter-01"
        )["status"] == "incomplete"
        all_complete_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
        )
        assert all(entry["status"] == "complete" for entry in all_complete_manifest["chapters"])
        long_pause_plan = copy.deepcopy(chapter_plan)
        long_pause_plan["segments"][0]["pause_after"]["seconds"] = 4.0
        try:
            assemble_chapters(
                chapter_book,
                chapter_output,
                long_pause_plan,
                complete_chapter_journal,
            )
        except RuntimeError as error:
            assert "continuous silence" in str(error)
        else:
            raise AssertionError("Expected assembled chapter WAV validation to fail.")
        published_master_hash = next(
            entry
            for entry in all_complete_manifest["chapters"]
            if entry["id"] == "chapter-01"
        )["audio"]["master_wav_sha256"]
        normal_tempo_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
            publication_tempo=1.0,
        )
        normal_tempo_chapter = next(
            entry
            for entry in normal_tempo_manifest["chapters"]
            if entry["id"] == "chapter-01"
        )
        assert normal_tempo_chapter["audio"]["duration_seconds"] == 2.05
        assert normal_tempo_chapter["audio"]["master_wav_sha256"] == published_master_hash
        assert normal_tempo_chapter["publication"]["tempo"] == 1.0
        assert not validate_chapter_audio(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
            normal_tempo_manifest,
        )
        invalid_tempo_manifest = copy.deepcopy(normal_tempo_manifest)
        invalid_tempo_manifest["publication"]["tempo"] = 1.2
        assert validate_chapter_audio(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
            invalid_tempo_manifest,
        )
        invalid_plan_manifest = copy.deepcopy(normal_tempo_manifest)
        invalid_plan_manifest["narration_plan"]["path"] = "metadata/other-plan.json"
        assert validate_chapter_audio(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
            invalid_plan_manifest,
        )
        duplicate_chapter_manifest = copy.deepcopy(normal_tempo_manifest)
        duplicate_chapter_manifest["chapters"].append(
            copy.deepcopy(duplicate_chapter_manifest["chapters"][0])
        )
        assert validate_chapter_audio(
            chapter_book,
            chapter_output,
            chapter_plan,
            complete_chapter_journal,
            duplicate_chapter_manifest,
        )
        changed_unselected_plan = copy.deepcopy(chapter_plan)
        changed_unselected_plan["segments"][2]["pause_after"]["seconds"] = 0.5
        selective_manifest = assemble_chapters(
            chapter_book,
            chapter_output,
            changed_unselected_plan,
            complete_chapter_journal,
            ["chapter-01"],
        )
        assert next(
            entry for entry in selective_manifest["chapters"] if entry["id"] == "chapter-02"
        )["status"] == "incomplete"
        resume_segment = SimpleNamespace(
            line_number=3,
            text="Locução de retomada completa.",
            warnings=(),
        )
        resume_identity = {"engine": "test", "device": "cpu"}
        resume_seed = segment_seed(20260713, 1)
        resume_journal = new_render_journal(
            resume_identity,
            "locutor/book.txt",
            "a" * 64,
        )
        resume_journal["segments"] = [
            segment_record(
                1,
                resume_segment,
                resume_segment_path,
                resume_output,
                resume_seed,
            )
        ]
        resume_journal_path = root / "audio-render-journal.json"
        write_json(resume_journal_path, resume_journal)
        loaded_journal, loaded_records = load_render_journal(
            resume_journal_path,
            resume_identity,
        )
        assert loaded_journal["status"] == "incomplete"
        assert reusable_segment_record(
            loaded_records[1],
            1,
            resume_segment,
            resume_segment_path,
            resume_output,
            resume_seed,
        )
        reflow_segment = SimpleNamespace(
            line_number=2,
            text=resume_segment.text,
            warnings=(),
        )
        reflow_cache = resume_output / "segments" / ".reflow-test"
        reflow_sources = prepare_reflow_reuse_sources(
            loaded_records,
            [reflow_segment],
            resume_output,
            reflow_cache,
        )
        assert len(reflow_sources) == 1
        reflow_record, reflow_source_path = next(iter(reflow_sources.values()))
        assert reflow_record["index"] == 1
        assert reflow_source_path.is_file()
        reflow_target = resume_segments / "segment-0002.wav"
        copy_or_link_atomically(reflow_source_path, reflow_target)
        reflow_seed = segment_seed(20260713, 2)
        reused_reflow_record = segment_record(
            2,
            reflow_segment,
            reflow_target,
            resume_output,
            reflow_record["seed"],
        )
        reused_reflow_record["reused_from"] = reflow_reuse_provenance(
            reflow_record,
            reflow_seed,
        )
        assert reusable_segment_record(
            reused_reflow_record,
            2,
            reflow_segment,
            reflow_target,
            resume_output,
            reflow_seed,
        )
        assert reused_reflow_record["reused_from"]["source_audio_sha256"] == reflow_record[
            "audio_sha256"
        ]
        tampered_reflow_record = copy.deepcopy(reused_reflow_record)
        tampered_reflow_record["reused_from"]["source_audio_sha256"] = "0" * 64
        assert not reusable_segment_record(
            tampered_reflow_record,
            2,
            reflow_segment,
            reflow_target,
            resume_output,
            reflow_seed,
        )
        duplicate_source = resume_segments / "segment-0003.wav"
        copy_or_link_atomically(resume_segment_path, duplicate_source)
        duplicate_segment = SimpleNamespace(
            line_number=3,
            text=resume_segment.text,
            warnings=(),
        )
        duplicate_record = segment_record(
            3,
            duplicate_segment,
            duplicate_source,
            resume_output,
            segment_seed(20260713, 3),
        )
        assert not prepare_reflow_reuse_sources(
            {1: loaded_records[1], 3: duplicate_record},
            [reflow_segment],
            resume_output,
            resume_output / "segments" / ".reflow-ambiguous",
        )
        silent_reflow_segment = SimpleNamespace(
            line_number=4,
            text="Trecho silencioso.",
            warnings=(),
        )
        silent_duration = 1
        silent_record = {
            "index": 4,
            "semantic_id": "line-4",
            "locutor_line": 4,
            "character_count": len(silent_reflow_segment.text),
            "text_sha256": hashlib.sha256(
                silent_reflow_segment.text.encode("utf-8")
            ).hexdigest(),
            "warnings": [],
            "path": silent_segment_path.relative_to(resume_output).as_posix(),
            "audio_sha256": sha256_file(silent_segment_path),
            "duration_seconds": silent_duration,
            "speech": {},
            "seed": segment_seed(20260713, 4),
        }
        assert not prepare_reflow_reuse_sources(
            {4: silent_record},
            [silent_reflow_segment],
            resume_output,
            resume_output / "segments" / ".reflow-silent",
        )
        escaped_record = dict(loaded_records[1])
        escaped_record["path"] = "../segment-0001.wav"
        assert not prepare_reflow_reuse_sources(
            {1: escaped_record},
            [reflow_segment],
            resume_output,
            resume_output / "segments" / ".reflow-escaped",
        )
        collision_source_a = resume_segments / "segment-0010.wav"
        collision_source_b = resume_segments / "segment-0011.wav"
        copy_or_link_atomically(reflow_target, collision_source_a)
        copy_or_link_atomically(reflow_target, collision_source_b)
        collision_segment_a = SimpleNamespace(
            line_number=10,
            text="Primeiro trecho preservado.",
            warnings=(),
        )
        collision_segment_b = SimpleNamespace(
            line_number=11,
            text="Segundo trecho preservado.",
            warnings=(),
        )
        collision_records = {
            10: segment_record(
                10,
                collision_segment_a,
                collision_source_a,
                resume_output,
                segment_seed(20260713, 10),
            ),
            11: segment_record(
                11,
                collision_segment_b,
                collision_source_b,
                resume_output,
                segment_seed(20260713, 11),
            ),
        }
        collision_targets = [
            SimpleNamespace(
                line_number=11,
                text=collision_segment_a.text,
                warnings=(),
            ),
            SimpleNamespace(
                line_number=12,
                text=collision_segment_b.text,
                warnings=(),
            ),
        ]
        collision_cache = resume_segments / ".reflow-collision"
        collision_sources = prepare_reflow_reuse_sources(
            collision_records,
            collision_targets,
            resume_output,
            collision_cache,
        )
        assert len(collision_sources) == 2
        replacement_a = collision_source_a.with_suffix(".replacement.wav")
        replacement_b = collision_source_b.with_suffix(".replacement.wav")
        write_wav(replacement_a, b"\x00\x20" * SAMPLE_RATE)
        write_wav(replacement_b, b"\x00\x30" * SAMPLE_RATE)
        os.replace(replacement_a, collision_source_a)
        os.replace(replacement_b, collision_source_b)
        for source_record, cached_path in collision_sources.values():
            destination = resume_segments / f"segment-{source_record['index'] + 1:04d}.wav"
            copy_or_link_atomically(cached_path, destination)
            assert sha256_file(destination) == source_record["audio_sha256"]
        shutil.rmtree(collision_cache)
        shutil.rmtree(reflow_cache)
        _, identity_free_records = load_render_journal(resume_journal_path, None)
        assert identity_free_records == loaded_records
        assert segment_seed(20260713, 2) == 20260714
        assert (
            segment_seed(54321, 1, FIXED_PER_SEGMENT_SEED_STRATEGY)
            == 54321
        )
        assert (
            segment_seed(54321, 2, FIXED_PER_SEGMENT_SEED_STRATEGY)
            == 54321
        )
        assert segment_seed(None, 1) is None
        resume_segment_path.write_bytes(b"not a WAV")
        assert not reusable_segment_record(
            loaded_records[1],
            1,
            resume_segment,
            resume_segment_path,
            resume_output,
            resume_seed,
        )
        try:
            load_render_journal(resume_journal_path, {"engine": "test", "device": "cuda"})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected a render journal identity mismatch to fail.")

        pdf_path = root / "source.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=360, height=540)
        with pdf_path.open("wb") as target:
            writer.write(target)

        library_root = root / "library"
        public_book_root = library_root / "Livro Fonte - 1933 - Autor Teste"
        book_root = public_book_root / "assembly"
        map_path = book_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(pdf_path),
            "--library-root",
            str(library_root),
            "--title",
            "Livro Fonte",
            "--publication-year",
            "1933",
            "--author",
            "Autor Teste",
            "--dpi",
            "72",
        )
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(map_path), "--check-files")
        initial_map = json.loads(map_path.read_text(encoding="utf-8"))
        assert initial_map["source"]["path"] == "source/original.pdf"
        assert initial_map["source"]["original_path"] == str(pdf_path.resolve())
        assert (book_root / "source" / "original.pdf").read_bytes() == pdf_path.read_bytes()

        same_name_dir = root / "different-source"
        same_name_dir.mkdir()
        same_name_pdf = same_name_dir / "source.pdf"
        second_writer = PdfWriter()
        second_writer.add_blank_page(width=360, height=540)
        second_writer.add_blank_page(width=360, height=540)
        with same_name_pdf.open("wb") as target:
            second_writer.write(target)
        run_fails(
            str(ROOT / "preflight.py"),
            "--source",
            str(same_name_pdf),
            "--library-root",
            str(library_root),
            "--title",
            "Livro Fonte",
            "--publication-year",
            "1933",
            "--author",
            "Autor Teste",
            "--dpi",
            "72",
        )

        escaped_map = json.loads(map_path.read_text(encoding="utf-8"))
        escaped_map["pages"][0]["render_path"] = "../outside.png"
        escaped_map_path = book_root / "metadata" / "escaped-map.json"
        escaped_map_path.write_text(
            json.dumps(escaped_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(escaped_map_path),
            "--check-files",
        )
        escaped_source_map = json.loads(map_path.read_text(encoding="utf-8"))
        escaped_source_map["source"]["path"] = "../outside.pdf"
        escaped_source_map_path = book_root / "metadata" / "escaped-source-map.json"
        escaped_source_map_path.write_text(
            json.dumps(escaped_source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(escaped_source_map_path),
            "--check-files",
        )
        absolute_map = copy.deepcopy(escaped_map)
        absolute_map["pages"][0]["render_path"] = str(root / "outside.png")
        absolute_map_path = book_root / "metadata" / "absolute-map.json"
        absolute_map_path.write_text(
            json.dumps(absolute_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(absolute_map_path),
            "--check-files",
        )
        copied_source = book_root / "metadata" / "borrowed-source.pdf"
        copied_source.write_bytes((book_root / "source" / "original.pdf").read_bytes())
        inroot_source_map = json.loads(map_path.read_text(encoding="utf-8"))
        inroot_source_map["source"]["path"] = "metadata/borrowed-source.pdf"
        inroot_source_map_path = book_root / "metadata" / "inroot-source-map.json"
        inroot_source_map_path.write_text(
            json.dumps(inroot_source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(inroot_source_map_path),
            "--check-files",
        )
        invalid_source_format_map = copy.deepcopy(initial_map)
        invalid_source_format_map["source"]["format"] = []
        invalid_source_format_errors = validate_book_map(
            invalid_source_format_map,
            book_root,
            False,
            True,
        )
        assert "source.format must be pdf or epub" in invalid_source_format_errors
        junction_root = root / "junction-root"
        junction_target_root = root / "junction-targets"
        for index, subtree in enumerate(
            (
                Path("source"),
                Path("assets") / "images" / "original",
                Path("assets") / "restoration" / "approved",
                Path("text") / "source" / "pages",
                Path("text") / "locutor",
            ),
            start=1,
        ):
            target = junction_target_root / f"target-{index}"
            target.mkdir(parents=True)
            (target / "probe.txt").write_text("redirected", encoding="utf-8")
            link = junction_root / subtree
            link.parent.mkdir(parents=True, exist_ok=True)
            create_junction(link, target)
            assert (
                resolve_under(
                    junction_root,
                    (subtree / "probe.txt").as_posix(),
                    (subtree,),
                )
                is None
            )

        spread_library = root / "spread-library"
        spread_public_root = spread_library / "Livro Aberto - 1934 - Autor Teste"
        spread_root = spread_public_root / "assembly"
        spread_map = spread_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(pdf_path),
            "--library-root",
            str(spread_library),
            "--title",
            "Livro Aberto",
            "--publication-year",
            "1934",
            "--author",
            "Autor Teste",
            "--layout",
            "spread",
            "--dpi",
            "72",
        )
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(spread_map), "--check-files")
        spread = json.loads(spread_map.read_text(encoding="utf-8"))
        assert spread["source"]["page_count_logical"] == 2
        assert [page["side"] for page in spread["pages"]] == ["left", "right"]

        epub_path = root / "source.epub"
        epub_library = root / "epub-library"
        epub_public_root = epub_library / "Livro EPUB - 2024 - Autora EPUB"
        epub_root = epub_public_root / "assembly"
        write_epub(epub_path)
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(epub_path),
            "--library-root",
            str(epub_library),
            "--title",
            "Livro EPUB",
            "--publication-year",
            "2024",
            "--author",
            "Autora EPUB",
        )
        epub_map = epub_root / "metadata" / "book-map.json"
        run(str(ROOT / "validate_book_map.py"), "--book-map", str(epub_map))
        epub = json.loads(epub_map.read_text(encoding="utf-8"))
        assert epub["source"]["format"] == "epub"
        assert epub["source"]["page_count_logical"] == 2
        epub_assets_path = epub_root / "metadata" / "assets-manifest.json"
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(epub_assets_path),
            "--book-root",
            str(epub_root),
            "--book-map",
            str(epub_map),
            "--check-files",
        )
        epub_assets = json.loads(epub_assets_path.read_text(encoding="utf-8"))
        assert len(epub_assets["assets"]) == 2
        epub_asset_by_locator = {
            asset["source"]["source_locator"]: asset
            for asset in epub_assets["assets"]
        }
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["source"]["format"] == "epub"
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["epub"]["role"] == "unresolved"
        assert epub_asset_by_locator["OEBPS/images/z-cover.png"]["epub"]["role"] == "cover"
        assert epub_asset_by_locator["OEBPS/images/z-cover.png"]["classification"]["content"] == "cover"

        epub_export_map = json.loads(epub_map.read_text(encoding="utf-8"))
        epub_export_map["analysis"]["status"] = "approved"
        epub_export_map["analysis"]["source_language"] = "en"
        for page in epub_export_map["pages"]:
            page["status"] = "mapped"
            page["blank"] = False
            page["chapter_id"] = "chapter-001"
        epub_export_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "EPUB Source",
                "start_logical_page": 1,
                "end_logical_page": 2,
            }
        ]
        epub_export_map["book"] = {"title": "EPUB Source", "author": "Autor"}
        epub_map.write_text(
            json.dumps(epub_export_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        epub_text_root = epub_root / "text"
        epub_page_records = []
        epub_page_text = {
            1: (
                "I\nEPUB SOURCE\n\nPrimeiro verso\nSegundo verso\n\n- Fala direta.\n\n"
                "Dr. José Meirelles2.\n2 Doutor José Meireles foi dirigente."
            ),
            2: "Texto de parágrafo.",
        }
        for logical_page in (1, 2):
            epub_page = epub_text_root / "source" / "pages" / f"page-{logical_page:04d}.txt"
            epub_page.parent.mkdir(parents=True, exist_ok=True)
            epub_page.write_text(epub_page_text[logical_page], encoding="utf-8")
            epub_page_records.append(
                {
                    "logical_page": logical_page,
                    "status": "verified",
                    "source_file": f"source/pages/page-{logical_page:04d}.txt",
                    "source_sha256": sha256_file(epub_page),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            )
        epub_chapter = epub_text_root / "source" / "chapters" / "chapter-01-epub-source.txt"
        epub_chapter.parent.mkdir(parents=True, exist_ok=True)
        epub_chapter.write_text("EPUB SOURCE\n\nSource text from the EPUB.", encoding="utf-8")
        epub_ledger_path = epub_root / "metadata" / "text-ledger.json"
        epub_ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "book_map_sha256": sha256_file(epub_map),
                    "pages": epub_page_records,
                    "chapter_outputs": [
                        {
                            "id": "chapter-001",
                            "source_file": "source/chapters/chapter-01-epub-source.txt",
                            "source_sha256": sha256_file(epub_chapter),
                            "source_pages": [
                                {
                                    "logical_page": record["logical_page"],
                                    "source_sha256": record["source_sha256"],
                                }
                                for record in epub_page_records
                            ],
                            "verified_by": "codex",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        epub_layout_path = epub_root / "metadata" / "epub-layout.json"
        write_json(
            epub_layout_path,
            {
                "schema_version": "1.0",
                "text_edition": "original",
                "book_map_sha256": sha256_file(epub_map),
                "text_ledger_sha256": sha256_file(epub_ledger_path),
                "documents": [
                    {
                        "id": "chapter-001",
                        "blocks": [
                            {
                                "kind": "heading",
                                "level": 1,
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0001.txt",
                                        "source_sha256": epub_page_records[0]["source_sha256"],
                                        "start_line": 1,
                                        "end_line": 2,
                                    }
                                ],
                            },
                            {
                                "kind": "verse",
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0001.txt",
                                        "source_sha256": epub_page_records[0]["source_sha256"],
                                        "start_line": 4,
                                        "end_line": 5,
                                    }
                                ],
                            },
                            {
                                "kind": "dialogue",
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0001.txt",
                                        "source_sha256": epub_page_records[0]["source_sha256"],
                                        "start_line": 7,
                                        "end_line": 7,
                                    }
                                ],
                            },
                            {
                                "kind": "paragraph",
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0001.txt",
                                        "source_sha256": epub_page_records[0]["source_sha256"],
                                        "start_line": 9,
                                        "end_line": 9,
                                    }
                                ],
                            },
                            {
                                "kind": "note",
                                "id": "note-2",
                                "marker": "2",
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0001.txt",
                                        "source_sha256": epub_page_records[0]["source_sha256"],
                                        "start_line": 10,
                                        "end_line": 10,
                                    }
                                ],
                            },
                            {
                                "kind": "paragraph",
                                "spans": [
                                    {
                                        "source_file": "text/source/pages/page-0002.txt",
                                        "source_sha256": epub_page_records[1]["source_sha256"],
                                        "start_line": 1,
                                        "end_line": 1,
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--text-root",
            str(epub_text_root),
        )
        run(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
        )
        epub_layout_data = json.loads(epub_layout_path.read_text(encoding="utf-8"))
        layout_order_root = root / "layout-order"
        shutil.copytree(epub_root, layout_order_root)
        layout_order_map_path = layout_order_root / "metadata" / "book-map.json"
        layout_order_map = json.loads(layout_order_map_path.read_text(encoding="utf-8"))
        layout_order_map["pages"][0]["chapter_id"] = "chapter-001"
        layout_order_map["pages"][1]["chapter_id"] = "chapter-002"
        layout_order_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Primeiro Capítulo",
                "start_logical_page": 1,
                "end_logical_page": 1,
            },
            {
                "id": "chapter-002",
                "number": 2,
                "title": "Segundo Capítulo",
                "start_logical_page": 2,
                "end_logical_page": 2,
            },
        ]
        write_json(layout_order_map_path, layout_order_map)
        layout_order_text_root = layout_order_root / "text"
        layout_order_second_chapter = (
            layout_order_text_root / "source" / "chapters" / "chapter-02-second-chapter.txt"
        )
        layout_order_second_chapter.write_text(
            "Segundo Capítulo\n\nTexto de parágrafo.",
            encoding="utf-8",
        )
        layout_order_ledger_path = layout_order_root / "metadata" / "text-ledger.json"
        layout_order_ledger = json.loads(layout_order_ledger_path.read_text(encoding="utf-8"))
        layout_order_ledger["book_map_sha256"] = sha256_file(layout_order_map_path)
        layout_order_ledger["chapter_outputs"] = [
            {
                "id": "chapter-002",
                "source_file": "source/chapters/chapter-02-second-chapter.txt",
                "source_sha256": sha256_file(layout_order_second_chapter),
                "source_pages": [
                    {
                        "logical_page": 2,
                        "source_sha256": layout_order_ledger["pages"][1]["source_sha256"],
                    }
                ],
                "verified_by": "codex",
            },
            {
                "id": "chapter-001",
                "source_file": "source/chapters/chapter-01-epub-source.txt",
                "source_sha256": sha256_file(
                    layout_order_text_root
                    / "source"
                    / "chapters"
                    / "chapter-01-epub-source.txt"
                ),
                "source_pages": [
                    {
                        "logical_page": 1,
                        "source_sha256": layout_order_ledger["pages"][0]["source_sha256"],
                    }
                ],
                "verified_by": "codex",
            },
        ]
        write_json(layout_order_ledger_path, layout_order_ledger)
        layout_order_layout = copy.deepcopy(epub_layout_data)
        layout_order_layout["book_map_sha256"] = sha256_file(layout_order_map_path)
        layout_order_layout["text_ledger_sha256"] = sha256_file(layout_order_ledger_path)
        layout_order_layout["documents"] = [
            {
                "id": "chapter-002",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][:3]),
            },
            {
                "id": "chapter-001",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][3:]),
            },
        ]
        layout_order_layout_path = layout_order_root / "metadata" / "epub-layout.json"
        write_json(layout_order_layout_path, layout_order_layout)
        run_fails(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(layout_order_root),
        )
        canonical_layout_order = copy.deepcopy(layout_order_layout)
        canonical_layout_order["documents"] = [
            {
                "id": "chapter-001",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][:3]),
            },
            {
                "id": "chapter-002",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][3:]),
            },
        ]
        write_json(layout_order_layout_path, canonical_layout_order)
        run(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(layout_order_root),
        )
        layout_order_manifest_path = (
            layout_order_root / "metadata" / "epub-manifest-order.json"
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(layout_order_map_path),
            "--ledger",
            str(layout_order_ledger_path),
            "--assets-manifest",
            str(layout_order_root / "metadata" / "assets-manifest.json"),
            "--text-root",
            str(layout_order_text_root),
            "--output",
            str(layout_order_manifest_path),
        )
        layout_order_export = layout_order_root / "exports" / "epub" / "canonical-order.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(layout_order_root),
            "--epub-manifest",
            str(layout_order_manifest_path),
            "--output",
            str(layout_order_export),
        )
        reordered_layout_order = copy.deepcopy(canonical_layout_order)
        reordered_layout_order["documents"][0]["id"] = "chapter-002"
        reordered_layout_order["documents"][1]["id"] = "chapter-001"
        write_json(layout_order_layout_path, reordered_layout_order)
        reordered_manifest_order = json.loads(
            layout_order_manifest_path.read_text(encoding="utf-8")
        )
        source_cover_documents = [
            document
            for document in reordered_manifest_order["documents"]
            if document.get("kind") == "source_cover"
        ]
        content_documents = [
            document
            for document in reordered_manifest_order["documents"]
            if document.get("kind") != "source_cover"
        ]
        reordered_manifest_order["documents"] = [
            *source_cover_documents,
            *reversed(content_documents),
        ]
        reordered_manifest_order["layout"]["sha256"] = sha256_file(
            layout_order_layout_path
        )
        reordered_manifest_order_path = (
            layout_order_root / "metadata" / "epub-manifest-reordered.json"
        )
        write_json(reordered_manifest_order_path, reordered_manifest_order)
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(layout_order_root),
            "--epub-manifest",
            str(reordered_manifest_order_path),
            "--output",
            str(layout_order_root / "exports" / "epub" / "reordered-order.epub"),
        )
        run_fails(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(layout_order_root),
            "--epub-manifest",
            str(reordered_manifest_order_path),
            "--epub",
            str(layout_order_export),
        )
        ordered_documents_layout = copy.deepcopy(epub_layout_data)
        ordered_documents_layout["documents"] = [
            {
                "id": "chapter-001",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][:3]),
            },
            {
                "id": "chapter-002",
                "blocks": copy.deepcopy(epub_layout_data["documents"][0]["blocks"][3:]),
            },
        ]
        epub_ledger_data = json.loads(epub_ledger_path.read_text(encoding="utf-8"))
        assert not validate_layout(
            ordered_documents_layout,
            epub_root,
            sha256_file(epub_map),
            sha256_file(epub_ledger_path),
            epub_ledger_data,
            ["chapter-001", "chapter-002"],
        )
        duplicate_note_marker_layout = copy.deepcopy(ordered_documents_layout)
        duplicate_note_marker_layout["documents"][0]["blocks"][1]["kind"] = "note"
        duplicate_note_marker_layout["documents"][0]["blocks"][1]["id"] = "note-other"
        duplicate_note_marker_layout["documents"][0]["blocks"][1]["marker"] = "2"
        duplicate_note_marker_errors = validate_layout(
            duplicate_note_marker_layout,
            epub_root,
            sha256_file(epub_map),
            sha256_file(epub_ledger_path),
            epub_ledger_data,
            ["chapter-001", "chapter-002"],
        )
        assert any("marker is duplicated: 2" in error for error in duplicate_note_marker_errors)
        duplicate_note_id_layout = copy.deepcopy(ordered_documents_layout)
        duplicate_note_id_layout["documents"][0]["blocks"][1]["kind"] = "note"
        duplicate_note_id_layout["documents"][0]["blocks"][1]["id"] = "note-2"
        duplicate_note_id_layout["documents"][0]["blocks"][1]["marker"] = "3"
        duplicate_note_id_errors = validate_layout(
            duplicate_note_id_layout,
            epub_root,
            sha256_file(epub_map),
            sha256_file(epub_ledger_path),
            epub_ledger_data,
            ["chapter-001", "chapter-002"],
        )
        assert any("id is duplicated: note-2" in error for error in duplicate_note_id_errors)
        swapped_document_layout = copy.deepcopy(ordered_documents_layout)
        swapped_document_layout["documents"][0]["id"] = "chapter-002"
        swapped_document_layout["documents"][1]["id"] = "chapter-001"
        swapped_document_errors = validate_layout(
            swapped_document_layout,
            epub_root,
            sha256_file(epub_map),
            sha256_file(epub_ledger_path),
            epub_ledger_data,
            ["chapter-001", "chapter-002"],
        )
        assert any("document order" in error for error in swapped_document_errors)
        invalid_layout_hash = copy.deepcopy(epub_layout_data)
        invalid_layout_hash["documents"][0]["blocks"][0]["spans"][0]["source_sha256"] = "0" * 64
        invalid_layout_hash_path = epub_root / "metadata" / "epub-layout-invalid-hash.json"
        write_json(invalid_layout_hash_path, invalid_layout_hash)
        run_fails(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
            "--layout",
            str(invalid_layout_hash_path),
        )
        incomplete_layout = copy.deepcopy(epub_layout_data)
        incomplete_layout["documents"][0]["blocks"].pop()
        incomplete_layout_path = epub_root / "metadata" / "epub-layout-incomplete.json"
        write_json(incomplete_layout_path, incomplete_layout)
        run_fails(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
            "--layout",
            str(incomplete_layout_path),
        )
        overlapping_layout = copy.deepcopy(epub_layout_data)
        overlapping_layout["documents"][0]["blocks"].append(
            {
                "kind": "paragraph",
                "spans": [
                    {
                        "source_file": "text/source/pages/page-0001.txt",
                        "source_sha256": epub_page_records[0]["source_sha256"],
                        "start_line": 4,
                        "end_line": 4,
                    }
                ],
            }
        )
        overlapping_layout_path = epub_root / "metadata" / "epub-layout-overlapping.json"
        write_json(overlapping_layout_path, overlapping_layout)
        run_fails(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
            "--layout",
            str(overlapping_layout_path),
        )
        unknown_layout = copy.deepcopy(epub_layout_data)
        unknown_layout["documents"][0]["blocks"][0]["kind"] = "unknown"
        unknown_layout_path = epub_root / "metadata" / "epub-layout-unknown.json"
        write_json(unknown_layout_path, unknown_layout)
        run_fails(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
            "--layout",
            str(unknown_layout_path),
        )
        epub_translation_ledger_path = epub_root / "metadata" / "translation-ledger.json"
        epub_translation_ledger = translation_ledger_for(
            epub_map,
            epub_ledger_path,
            json.loads(epub_ledger_path.read_text(encoding="utf-8")),
            epub_text_root,
            "en",
            "Fonte EPUB em Portugues",
            {"chapter-001": "Fonte EPUB"},
        )
        write_json(epub_translation_ledger_path, epub_translation_ledger)
        assert epub_translation_ledger["schema_version"] == "1.1"
        run(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(epub_translation_ledger_path),
            "--text-root",
            str(epub_text_root),
        )
        epub_translated_layout_path = epub_root / "metadata" / "epub-layout.pt-br.json"
        translated_chapter_output = epub_translation_ledger["chapter_outputs"][0]
        translated_chapter_file = f"text/{translated_chapter_output['translation_file']}"
        write_json(
            epub_translated_layout_path,
            {
                "schema_version": "1.0",
                "text_edition": "translated-pt-br",
                "book_map_sha256": sha256_file(epub_map),
                "text_ledger_sha256": sha256_file(epub_ledger_path),
                "translation_ledger_sha256": sha256_file(epub_translation_ledger_path),
                "documents": [
                    {
                        "id": translated_chapter_output["id"],
                        "blocks": [
                            {
                                "kind": "heading",
                                "level": 1,
                                "text_file": translated_chapter_file,
                                "text_sha256": translated_chapter_output["translation_sha256"],
                                "block_index": 1,
                            },
                            {
                                "kind": "paragraph",
                                "text_file": translated_chapter_file,
                                "text_sha256": translated_chapter_output["translation_sha256"],
                                "block_index": 2,
                            },
                        ],
                    }
                ],
            },
        )
        run(
            str(ROOT / "validate_epub_layout.py"),
            "--book-root",
            str(epub_root),
            "--text-edition",
            "translated-pt-br",
        )
        invalid_language_evidence = copy.deepcopy(epub_translation_ledger)
        invalid_language_evidence["translation_decision"]["evidence"][0]["source_sha256"] = "0" * 64
        invalid_language_evidence_path = epub_root / "metadata" / "translation-ledger-invalid-evidence.json"
        write_json(invalid_language_evidence_path, invalid_language_evidence)
        run_fails(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(invalid_language_evidence_path),
            "--text-root",
            str(epub_text_root),
        )
        missing_translation_quality = copy.deepcopy(epub_translation_ledger)
        del missing_translation_quality["translation_quality"]
        missing_translation_quality_path = (
            epub_root / "metadata" / "translation-ledger-missing-quality.json"
        )
        write_json(missing_translation_quality_path, missing_translation_quality)
        run_fails(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(missing_translation_quality_path),
            "--text-root",
            str(epub_text_root),
        )
        unresolved_ambiguity = copy.deepcopy(epub_translation_ledger)
        first_verified_translation_page = next(
            page
            for page in unresolved_ambiguity["pages"]
            if page["status"] == "verified"
        )
        unresolved_ambiguity["translation_quality"]["ambiguities"] = [
            {
                "id": "ambiguity-0001",
                "source_pages": [first_verified_translation_page["logical_page"]],
                "source_span": "ambiguous source expression",
                "category": "idiom",
                "question": "Which contextual sense applies?",
                "status": "unresolved",
                "resolution": "",
                "resolved_by": "",
                "research": [],
            }
        ]
        unresolved_ambiguity_path = (
            epub_root / "metadata" / "translation-ledger-unresolved-ambiguity.json"
        )
        write_json(unresolved_ambiguity_path, unresolved_ambiguity)
        run_fails(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(unresolved_ambiguity_path),
            "--text-root",
            str(epub_text_root),
        )
        resolved_researched_ambiguity = copy.deepcopy(epub_translation_ledger)
        resolved_researched_ambiguity["translation_quality"]["ambiguities"] = [
            {
                "id": "ambiguity-0001",
                "source_pages": [first_verified_translation_page["logical_page"]],
                "source_span": "archaic source expression",
                "category": "archaic",
                "question": "Which historical sense applies?",
                "status": "resolved",
                "resolution": "Use the period-specific sense.",
                "resolved_by": "codex-verifier",
                "research": [
                    {
                        "source_type": "dictionary",
                        "reference": "Historical dictionary entry",
                        "accessed_on": "2026-07-17",
                        "finding": "The historical sense matches the scene.",
                    }
                ],
            }
        ]
        resolved_researched_ambiguity_path = (
            epub_root / "metadata" / "translation-ledger-resolved-research.json"
        )
        write_json(
            resolved_researched_ambiguity_path,
            resolved_researched_ambiguity,
        )
        run(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(resolved_researched_ambiguity_path),
            "--text-root",
            str(epub_text_root),
        )
        invalid_research_date = copy.deepcopy(resolved_researched_ambiguity)
        invalid_research_date["translation_quality"]["ambiguities"][0]["research"][0][
            "accessed_on"
        ] = "not-a-date"
        invalid_research_date_path = (
            epub_root / "metadata" / "translation-ledger-invalid-research-date.json"
        )
        write_json(invalid_research_date_path, invalid_research_date)
        run_fails(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--translation-ledger",
            str(invalid_research_date_path),
            "--text-root",
            str(epub_text_root),
        )
        epub_export_manifest = epub_root / "metadata" / "epub-manifest-export.json"
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--assets-manifest",
            str(epub_assets_path),
            "--text-root",
            str(epub_text_root),
            "--output",
            str(epub_export_manifest),
        )
        epub_export_data = json.loads(epub_export_manifest.read_text(encoding="utf-8"))
        assert epub_export_data["layout"]["mode"] == "semantic"
        assert epub_export_data["layout"]["sha256"] == sha256_file(epub_layout_path)
        source_cover_document = epub_export_data["documents"][0]
        assert source_cover_document["kind"] == "source_cover"
        assert source_cover_document["source_file"] is None
        assert source_cover_document["asset_ids"] == [
            epub_asset_by_locator["OEBPS/images/z-cover.png"]["id"]
        ]
        assert epub_asset_by_locator["OEBPS/images/a-illustration.png"]["id"] not in source_cover_document["asset_ids"]
        epub_source_cover_export = epub_root / "exports" / "epub" / "source-cover.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--output",
            str(epub_source_cover_export),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--epub",
            str(epub_source_cover_export),
        )
        source_cover_after_chapter = copy.deepcopy(epub_export_data)
        source_cover_after_chapter["documents"] = [
            *source_cover_after_chapter["documents"][1:],
            source_cover_after_chapter["documents"][0],
        ]
        source_cover_after_chapter_path = (
            epub_root / "metadata" / "epub-manifest-source-cover-after-chapter.json"
        )
        write_json(source_cover_after_chapter_path, source_cover_after_chapter)
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(source_cover_after_chapter_path),
            "--output",
            str(epub_root / "exports" / "epub" / "invalid-source-cover-order.epub"),
        )
        run_fails(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(source_cover_after_chapter_path),
            "--epub",
            str(epub_source_cover_export),
        )
        duplicate_source_cover = copy.deepcopy(epub_export_data)
        duplicate_source_cover_document = copy.deepcopy(
            duplicate_source_cover["documents"][0]
        )
        duplicate_source_cover_document["id"] = "source-cover-duplicate"
        duplicate_source_cover["documents"].insert(1, duplicate_source_cover_document)
        duplicate_source_cover_path = (
            epub_root / "metadata" / "epub-manifest-duplicate-source-cover.json"
        )
        write_json(duplicate_source_cover_path, duplicate_source_cover)
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(duplicate_source_cover_path),
            "--output",
            str(epub_root / "exports" / "epub" / "invalid-duplicate-source-cover.epub"),
        )
        run_fails(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(duplicate_source_cover_path),
            "--epub",
            str(epub_source_cover_export),
        )
        reordered_spine_export = epub_root / "exports" / "epub" / "reordered-spine.epub"
        spine_reordered = False
        with zipfile.ZipFile(epub_source_cover_export) as source_archive, zipfile.ZipFile(
            reordered_spine_export,
            "w",
        ) as reordered_archive:
            for info in source_archive.infolist():
                payload = source_archive.read(info.filename)
                if info.filename == "OEBPS/content.opf":
                    original_spine = (
                        b'<itemref idref="doc-1"/>\n    <itemref idref="doc-2"/>'
                    )
                    reordered_spine = (
                        b'<itemref idref="doc-2"/>\n    <itemref idref="doc-1"/>'
                    )
                    assert original_spine in payload
                    payload = payload.replace(original_spine, reordered_spine, 1)
                    spine_reordered = True
                reordered_archive.writestr(info, payload)
        assert spine_reordered
        reordered_spine_sidecar = json.loads(
            epub_source_cover_export.with_suffix(".epub.json").read_text(encoding="utf-8")
        )
        reordered_spine_sidecar["epub_path"] = reordered_spine_export.relative_to(
            epub_root
        ).as_posix()
        reordered_spine_sidecar["epub_sha256"] = sha256_file(reordered_spine_export)
        write_json(reordered_spine_export.with_suffix(".epub.json"), reordered_spine_sidecar)
        run_fails(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--epub",
            str(reordered_spine_export),
        )
        with zipfile.ZipFile(epub_source_cover_export) as archive:
            source_cover_xhtml = archive.read("OEBPS/text/001-source-cover.xhtml").decode("utf-8")
            assert 'epub:type="titlepage"' in source_cover_xhtml
            assert "z-cover" in source_cover_xhtml
            original_chapter_xhtml = archive.read("OEBPS/text/002-chapter-001.xhtml").decode("utf-8")
            assert '<h1 class="source-heading">' in original_chapter_xhtml
            assert "Primeiro verso" in original_chapter_xhtml
            assert 'class="verse"' in original_chapter_xhtml
            assert 'class="dialogue">- Fala direta.</p>' in original_chapter_xhtml
            assert 'epub:type="noteref" href="#note-2">2</a>' in original_chapter_xhtml
            assert 'id="note-2" epub:type="footnote" class="footnote"' in original_chapter_xhtml
            assert "Texto de parágrafo." in original_chapter_xhtml
            assert "Source text from the EPUB." not in original_chapter_xhtml
            assert "Texto traduzido para PT-BR." not in original_chapter_xhtml
        tampered_source_export = epub_root / "exports" / "epub" / "tampered-source.epub"
        with zipfile.ZipFile(epub_source_cover_export) as source_archive, zipfile.ZipFile(
            tampered_source_export,
            "w",
        ) as tampered_archive:
            for info in source_archive.infolist():
                payload = source_archive.read(info.filename)
                if info.filename == "OEBPS/text/002-chapter-001.xhtml":
                    payload = payload.replace(b"Primeiro verso", b"Texto adulterado")
                tampered_archive.writestr(info, payload)
        tampered_sidecar = json.loads(
            epub_source_cover_export.with_suffix(".epub.json").read_text(encoding="utf-8")
        )
        tampered_sidecar["epub_path"] = tampered_source_export.relative_to(epub_root).as_posix()
        tampered_sidecar["epub_sha256"] = sha256_file(tampered_source_export)
        write_json(tampered_source_export.with_suffix(".epub.json"), tampered_sidecar)
        with zipfile.ZipFile(tampered_source_export) as archive:
            assert b"Texto adulterado" in archive.read("OEBPS/text/002-chapter-001.xhtml")
        assert validate_epub_document_texts(
            tampered_source_export,
            epub_root,
            [
                {
                    **document,
                    "_text_path": (
                        epub_text_root / document["source_file"].removeprefix("text/")
                        if document.get("source_file")
                        else None
                    ),
                }
                for document in epub_export_data["documents"]
            ],
        )
        run_fails(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(epub_export_manifest),
            "--epub",
            str(tampered_source_export),
        )
        epub_translated_manifest = epub_root / "metadata" / "epub-manifest.pt-br.json"
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(epub_map),
            "--ledger",
            str(epub_ledger_path),
            "--assets-manifest",
            str(epub_assets_path),
            "--text-root",
            str(epub_text_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(epub_translated_manifest),
        )
        epub_translated_data = json.loads(epub_translated_manifest.read_text(encoding="utf-8"))
        assert epub_translated_data["text_edition"] == "translated-pt-br"
        assert epub_translated_data["language"] == "pt-BR"
        assert epub_translated_data["source_language"] == "en"
        assert epub_translated_data["translation_ledger_sha256"] == sha256_file(epub_translation_ledger_path)
        assert epub_translated_data["layout"] == {
            "mode": "semantic",
            "path": "metadata/epub-layout.pt-br.json",
            "sha256": sha256_file(epub_translated_layout_path),
        }
        assert epub_translated_data["documents"][1]["translation_file"].startswith(
            "text/translation/pt-BR/"
        )
        assert all(
            document.get("kind") == "source_cover"
            or (
                str(document.get("source_file")).startswith("text/source/")
                and str(document.get("translation_file")).startswith("text/translation/pt-BR/")
            )
            for document in epub_translated_data["documents"]
        )
        epub_translated_export = epub_root / "exports" / "epub" / "translated.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(epub_translated_export),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(epub_root),
            "--epub",
            str(epub_translated_export),
            "--text-edition",
            "translated-pt-br",
        )
        translated_sidecar = json.loads(
            epub_translated_export.with_suffix(".epub.json").read_text(encoding="utf-8")
        )
        assert translated_sidecar["text_edition"] == "translated-pt-br"
        assert translated_sidecar["language"] == "pt-BR"
        assert translated_sidecar["source_language"] == "en"
        assert translated_sidecar["translation_ledger_sha256"] == sha256_file(epub_translation_ledger_path)
        assert translated_sidecar["layout"] == epub_translated_data["layout"]
        with zipfile.ZipFile(epub_translated_export) as archive:
            translated_chapter_xhtml = archive.read("OEBPS/text/002-chapter-001.xhtml").decode("utf-8")
            assert "Texto traduzido para PT-BR." in translated_chapter_xhtml
            assert "Source text from the EPUB." not in translated_chapter_xhtml
        original_rejects_translated_source = copy.deepcopy(epub_export_data)
        original_rejects_translated_source["documents"][1]["source_file"] = (
            epub_translated_data["documents"][1]["translation_file"]
        )
        original_rejects_translated_source["documents"][1]["source_sha256"] = (
            epub_translated_data["documents"][1]["translation_sha256"]
        )
        original_rejects_translated_source_path = epub_root / "metadata" / "epub-manifest-original-translated-path.json"
        write_json(original_rejects_translated_source_path, original_rejects_translated_source)
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(epub_root),
            "--epub-manifest",
            str(original_rejects_translated_source_path),
            "--output",
            str(epub_root / "exports" / "epub" / "invalid-original-translated-path.epub"),
        )

        image_pdf = root / "image-source.pdf"
        image_jpeg = root / "image-source.jpg"
        write_pdf_with_image(image_pdf, image_jpeg)
        image_library = root / "image-library"
        image_public_root = image_library / "Livro com Imagem - 1933 - Antônio de Teste"
        image_book_root = image_public_root / "assembly"
        image_map_path = image_book_root / "metadata" / "book-map.json"
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(image_pdf),
            "--library-root",
            str(image_library),
            "--title",
            "Livro com Imagem",
            "--publication-year",
            "1933",
            "--author",
            "Antônio de Teste",
            "--dpi",
            "72",
        )
        image_assets_path = image_book_root / "metadata" / "assets-manifest.json"
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        assert len(image_assets["assets"]) == 1
        image_asset = image_assets["assets"][0]
        assert image_asset["source"]["format"] == "pdf"
        assert (image_book_root / image_asset["original"]["path"]).is_file()
        escaped_original_assets = copy.deepcopy(image_assets)
        escaped_original = escaped_original_assets["assets"][0]["original"]
        escaped_original["path"] = "assets/images/original/../../../source/original.pdf"
        escaped_original["sha256"] = sha256_file(image_book_root / "source" / "original.pdf")
        escaped_original_assets_path = image_book_root / "metadata" / "escaped-original-assets.json"
        escaped_original_assets_path.write_text(
            json.dumps(escaped_original_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(escaped_original_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        image_assets["assets"][0]["classification"]["content"] = "illustration"
        image_assets["assets"][0]["classification"]["text_pixels"] = "none"
        image_assets["assets"][0]["classification"]["restoration_eligibility"] = "review_required"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "preflight.py"),
            "--source",
            str(image_pdf),
            "--library-root",
            str(image_library),
            "--title",
            "Livro com Imagem",
            "--publication-year",
            "1933",
            "--author",
            "Antônio de Teste",
            "--assets-only",
        )
        refreshed_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        assert refreshed_assets["assets"][0]["classification"]["content"] == "illustration"

        book_map = json.loads(map_path.read_text(encoding="utf-8"))
        book_map["analysis"]["status"] = "approved"
        book_map["pages"][0]["status"] = "mapped"
        book_map["pages"][0]["blank"] = False
        book_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Abertura",
                "start_logical_page": 1,
                "end_logical_page": 1,
            }
        ]
        map_path.write_text(json.dumps(book_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(
            str(ROOT / "validate_book_map.py"),
            "--book-map",
            str(map_path),
            "--require-ready",
            "--check-files",
        )

        text_root = book_root / "text"
        source_file = text_root / "source" / "pages" / "page-0001.txt"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("Texto fiel de teste.", encoding="utf-8")
        ledger_path = book_root / "metadata" / "text-ledger.json"
        ledger = {
            "schema_version": "1.0",
            "book_map_sha256": sha256_file(map_path),
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": sha256_file(source_file),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            ],
        }
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(ledger_path),
            "--text-root",
            str(text_root),
        )
        wrong_hash_ledger = copy.deepcopy(ledger)
        wrong_hash_ledger["book_map_sha256"] = "0" * 64
        wrong_hash_path = book_root / "metadata" / "wrong-hash-ledger.json"
        wrong_hash_path.write_text(
            json.dumps(wrong_hash_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(wrong_hash_path),
            "--text-root",
            str(text_root),
        )
        escaped_ledger = copy.deepcopy(ledger)
        escaped_ledger["pages"][0]["source_file"] = "../../outside.txt"
        escaped_ledger_path = book_root / "metadata" / "escaped-ledger.json"
        escaped_ledger_path.write_text(
            json.dumps(escaped_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(escaped_ledger_path),
            "--text-root",
            str(text_root),
        )
        absolute_ledger = copy.deepcopy(ledger)
        absolute_ledger["pages"][0]["source_file"] = str(root / "outside.txt")
        absolute_ledger_path = book_root / "metadata" / "absolute-ledger.json"
        absolute_ledger_path.write_text(
            json.dumps(absolute_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(absolute_ledger_path),
            "--text-root",
            str(text_root),
        )
        locutor_page = text_root / "locutor" / "pages" / "page-0001.txt"
        locutor_page.parent.mkdir(parents=True)
        locutor_page.write_text("Texto derivado do locutor.", encoding="utf-8")
        source_from_locutor = copy.deepcopy(ledger)
        source_from_locutor["pages"][0]["source_file"] = "locutor/pages/page-0001.txt"
        source_from_locutor["pages"][0]["source_sha256"] = sha256_file(locutor_page)
        source_from_locutor_path = book_root / "metadata" / "source-from-locutor-ledger.json"
        source_from_locutor_path.write_text(
            json.dumps(source_from_locutor, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(source_from_locutor_path),
            "--text-root",
            str(text_root),
        )
        locutor_ledger = copy.deepcopy(ledger)
        locutor_ledger["pages"][0]["locutor_file"] = "locutor/pages/page-0001.txt"
        locutor_ledger["pages"][0]["locutor_sha256"] = sha256_file(locutor_page)
        locutor_ledger_path = book_root / "metadata" / "locutor-ledger.json"
        locutor_ledger_path.write_text(
            json.dumps(locutor_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(locutor_ledger_path),
            "--text-root",
            str(text_root),
            "--require-locutor",
        )
        locutor_from_source = copy.deepcopy(locutor_ledger)
        locutor_from_source["pages"][0]["locutor_file"] = "source/pages/page-0001.txt"
        locutor_from_source["pages"][0]["locutor_sha256"] = sha256_file(source_file)
        locutor_from_source_path = book_root / "metadata" / "locutor-from-source-ledger.json"
        locutor_from_source_path.write_text(
            json.dumps(locutor_from_source, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(map_path),
            "--ledger",
            str(locutor_from_source_path),
            "--text-root",
            str(text_root),
            "--require-locutor",
        )

        export_map = json.loads(image_map_path.read_text(encoding="utf-8"))
        export_map["analysis"]["status"] = "approved"
        export_map["analysis"]["source_language"] = "pt-BR"
        export_map["pages"][0]["status"] = "mapped"
        export_map["pages"][0]["blank"] = False
        export_map["pages"][0]["chapter_id"] = "chapter-001"
        export_map["chapters"] = [
            {
                "id": "chapter-001",
                "number": 1,
                "title": "Livro com Imagem",
                "start_logical_page": 1,
                "end_logical_page": 1,
            }
        ]
        export_map["book"] = {
            "title": "Livro com Ação",
            "subtitle": "Coração e Orixás",
            "author": "Antônio de Teste",
            "original_publication_place": "São Paulo",
            "original_publication_year": 1933,
        }
        image_map_path.write_text(
            json.dumps(export_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        image_text_root = image_book_root / "text"
        image_page_file = image_text_root / "source" / "pages" / "page-0001.txt"
        image_page_file.parent.mkdir(parents=True)
        image_page_file.write_text(
            "LIVRO COM IMAGEM\n\nTexto fiel de EPUB. He said hello. Vossa mercê chegou.",
            encoding="utf-8",
        )
        image_chapter = image_text_root / "source" / "chapters" / "chapter-01-livro-com-imagem.txt"
        image_chapter.parent.mkdir(parents=True)
        image_chapter.write_text(
            "LIVRO COM IMAGEM\n\nPrimeiro verso\nSegundo verso\nTerceiro verso\n\n"
            "Texto fiel de EPUB. He said hello. Vossa mercê chegou.",
            encoding="utf-8",
        )
        image_ledger_path = image_book_root / "metadata" / "text-ledger.json"
        image_ledger = {
            "schema_version": "1.0",
            "book_map_sha256": sha256_file(image_map_path),
            "pages": [
                {
                    "logical_page": 1,
                    "status": "verified",
                    "source_file": "source/pages/page-0001.txt",
                    "source_sha256": sha256_file(image_page_file),
                    "transcribed_by": "codex",
                    "verified_by": "codex",
                    "notes": "",
                }
            ],
            "chapter_outputs": [
                {
                    "id": "chapter-001",
                    "source_file": "source/chapters/chapter-01-livro-com-imagem.txt",
                    "source_sha256": sha256_file(image_chapter),
                    "source_pages": [
                        {
                            "logical_page": 1,
                            "source_sha256": sha256_file(image_page_file),
                        }
                    ],
                    "verified_by": "codex",
                }
            ],
        }
        image_ledger_path.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "verify_text_ledger.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--text-root",
            str(image_text_root),
        )
        image_translation_ledger_path = image_book_root / "metadata" / "translation-ledger.json"
        image_translation_ledger = translation_ledger_for(
            image_map_path,
            image_ledger_path,
            image_ledger,
            image_text_root,
            "pt-BR",
            "Livro com Acao",
            {"chapter-001": "Livro com Imagem"},
        )
        write_json(image_translation_ledger_path, image_translation_ledger)
        run_fails(
            str(ROOT / "verify_translation_ledger.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--translation-ledger",
            str(image_translation_ledger_path),
            "--text-root",
            str(image_text_root),
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--text-edition",
            "translated-pt-br",
            "--output",
            str(image_book_root / "metadata" / "invalid-pt-br-epub-manifest.json"),
        )
        archaic_locutor = image_text_root / "locutor" / "chapters" / "chapter-01-livro-com-imagem.txt"
        archaic_locutor.parent.mkdir(parents=True, exist_ok=True)
        archaic_locutor.write_text(
            "LIVRO COM IMAGEM\n\nPrimeiro verso\nSegundo verso\nTerceiro verso\n\n"
            "Texto fiel de EPUB. He said hello. Você chegou.",
            encoding="utf-8",
        )
        narrator_changes_path = image_book_root / "metadata" / "narrator-changes.json"
        narrator_changes = {
            "schema_version": "2.0",
            "source_book_sha256": sha256_file(image_book_root / "source" / "original.pdf"),
            "book_map_sha256": sha256_file(image_map_path),
            "mode": "archaic-modernized",
            "base_edition": "source",
            "base_ledger_sha256": sha256_file(image_ledger_path),
            "archaic_assessment": {
                "status": "confirmed",
                "reviewed_by": "codex",
                "evidence": [
                    {
                        "logical_page": 1,
                        "source_sha256": image_ledger["pages"][0]["source_sha256"],
                        "source_span": "Vossa mercê chegou.",
                        "reason": "Exact archaic source span was reviewed before narrator modernization.",
                    }
                ],
            },
            "outputs": [
                {
                    "id": "chapter-001-locutor",
                    "kind": "full-book",
                    "locutor_file": "locutor/chapters/chapter-01-livro-com-imagem.txt",
                    "locutor_sha256": sha256_file(archaic_locutor),
                    "reviewed_by": "codex",
                    "base_outputs": [
                        {
                            "id": "chapter-001",
                            "base_file": image_ledger["chapter_outputs"][0]["source_file"],
                            "base_sha256": image_ledger["chapter_outputs"][0]["source_sha256"],
                        }
                    ],
                }
            ],
            "changes": [
                {
                    "output_id": "chapter-001-locutor",
                    "base_output_id": "chapter-001",
                    "kind": "archaic_lexical_modernization",
                    "base_span": "Vossa mercê chegou.",
                    "locutor_span": "Você chegou.",
                    "logical_pages": [1],
                    "source_sha256": image_ledger["pages"][0]["source_sha256"],
                    "reason": "Modernized a confirmed archaic form after exact evidence review.",
                    "reviewed_by": "codex",
                }
            ],
        }
        write_json(narrator_changes_path, narrator_changes)
        run(
            str(ROOT / "validate_narrator_lineage.py"),
            "--book-root",
            str(image_book_root),
            "--input-file",
            str(archaic_locutor),
        )
        missing_archaic_evidence = copy.deepcopy(narrator_changes)
        missing_archaic_evidence["archaic_assessment"].pop("evidence")
        missing_archaic_evidence_path = image_book_root / "metadata" / "narrator-changes-missing-evidence.json"
        write_json(missing_archaic_evidence_path, missing_archaic_evidence)
        run_fails(
            str(ROOT / "validate_narrator_lineage.py"),
            "--book-root",
            str(image_book_root),
            "--narrator-changes",
            str(missing_archaic_evidence_path),
            "--input-file",
            str(archaic_locutor),
        )
        mismatched_archaic_evidence = copy.deepcopy(narrator_changes)
        mismatched_archaic_evidence["archaic_assessment"]["evidence"][0][
            "source_span"
        ] = "Texto fiel de EPUB."
        mismatched_archaic_evidence_path = (
            image_book_root / "metadata" / "narrator-changes-mismatched-evidence.json"
        )
        write_json(mismatched_archaic_evidence_path, mismatched_archaic_evidence)
        run_fails(
            str(ROOT / "validate_narrator_lineage.py"),
            "--book-root",
            str(image_book_root),
            "--narrator-changes",
            str(mismatched_archaic_evidence_path),
            "--input-file",
            str(archaic_locutor),
        )
        wrong_locutor_hash = copy.deepcopy(narrator_changes)
        wrong_locutor_hash["outputs"][0]["locutor_sha256"] = "0" * 64
        wrong_locutor_hash_path = image_book_root / "metadata" / "narrator-changes-wrong-hash.json"
        write_json(wrong_locutor_hash_path, wrong_locutor_hash)
        run_fails(
            str(ROOT / "validate_narrator_lineage.py"),
            "--book-root",
            str(image_book_root),
            "--narrator-changes",
            str(wrong_locutor_hash_path),
            "--input-file",
            str(archaic_locutor),
        )
        unrelated_locutor = image_text_root / "locutor" / "chapters" / "unrelated.txt"
        unrelated_locutor.write_text("Conteúdo sem relação com a fonte.", encoding="utf-8")
        unrelated_narrator_changes = copy.deepcopy(narrator_changes)
        unrelated_narrator_changes["outputs"][0]["locutor_file"] = "locutor/chapters/unrelated.txt"
        unrelated_narrator_changes["outputs"][0]["locutor_sha256"] = sha256_file(unrelated_locutor)
        unrelated_narrator_changes_path = image_book_root / "metadata" / "narrator-changes-unrelated.json"
        write_json(unrelated_narrator_changes_path, unrelated_narrator_changes)
        run_fails(
            str(ROOT / "validate_narrator_lineage.py"),
            "--book-root",
            str(image_book_root),
            "--narrator-changes",
            str(unrelated_narrator_changes_path),
            "--input-file",
            str(unrelated_locutor),
        )
        audio_root = image_book_root / "audio"
        narrator_review_path = image_book_root / "metadata" / "narrator-review.json"
        active_quality_findings = audit_text(archaic_locutor.read_text(encoding="utf-8"))
        narrator_review_draft = draft_review(
            image_book_root,
            archaic_locutor,
            active_quality_findings,
        )
        assert narrator_review_draft["output_file"] == archaic_locutor.relative_to(
            image_text_root
        ).as_posix()
        assert narrator_review_draft["status"] == "needs-review"
        assert all(
            finding["category"] == ""
            and finding["suggested_category"] in {
                "heading",
                "prose",
                "dialogue",
                "quotation",
                "verse",
                "note",
                "list",
                "excluded",
            }
            for finding in narrator_review_draft["findings"]
        )
        assert narrator_review_draft["review_scope"]["logical_pages"] == [1]
        assert {
            finding.locutor_span
            for finding in active_quality_findings
            if finding.kind == "uppercase_token"
        } == {"LIVRO", "COM", "IMAGEM", "EPUB"}
        pronunciation_entries = [
            {
                "term": "EPUB",
                "kind": "acronym",
                "decision": "preserved",
                "locutor_span": "EPUB",
                "logical_pages": [1],
                "reason": "The acronym remains in the reviewed narrator text.",
                "reviewed_by": "codex",
            }
        ]
        narrator_review = {
            "schema_version": "1.0",
            "profile": QUALITY_PROFILE,
            "status": "approved",
            "reviewed_by": "codex",
            "output_file": archaic_locutor.relative_to(image_text_root).as_posix(),
            "output_sha256": sha256_file(archaic_locutor),
            "narrator_changes_sha256": sha256_file(narrator_changes_path),
            "review_scope": {
                "categories": [
                    "heading",
                    "prose",
                    "dialogue",
                    "quotation",
                    "verse",
                    "note",
                    "list",
                ],
                "logical_pages": [1],
            },
            "findings": [
                {
                    "id": finding.id,
                    "kind": finding.kind,
                    "severity": finding.severity,
                    "line_number": finding.line_number,
                    "column": finding.column,
                    "locutor_span": finding.locutor_span,
                    "context": finding.context,
                    "category": "heading" if finding.line_number == 1 else "prose",
                    "status": "preserved",
                    "logical_pages": [1],
                    "reason": "The visible uppercase source form was reviewed for speech.",
                    "reviewed_by": "codex",
                }
                for finding in active_quality_findings
            ],
            "pronunciation_review": {
                "status": "approved",
                "reviewed_by": "codex",
                "entries": pronunciation_entries,
            },
        }
        write_json(narrator_review_path, narrator_review)
        quality_errors, quality_provenance = validate_review(
            image_book_root,
            narrator_review_path,
            archaic_locutor,
        )
        assert not quality_errors, quality_errors
        assert quality_provenance is not None
        assert quality_provenance["profile"] == QUALITY_PROFILE
        missing_epub_decision = copy.deepcopy(narrator_review)
        missing_epub_decision["pronunciation_review"]["entries"] = []
        write_json(narrator_review_path, missing_epub_decision)
        missing_epub_errors, _ = validate_review(
            image_book_root,
            narrator_review_path,
            archaic_locutor,
        )
        assert any("acronym pronunciation decision" in error for error in missing_epub_errors)
        write_json(narrator_review_path, narrator_review)
        prose_all_caps_locutor = (
            image_text_root / "locutor" / "chapters" / "prose-all-caps.txt"
        )
        prose_all_caps_locutor.write_text(
            "O PDF E O EPUB ESTÃO PRONTOS.",
            encoding="utf-8",
        )
        prose_narrator_changes = copy.deepcopy(narrator_changes)
        prose_narrator_changes["outputs"].append(
            {
                "id": "prose-all-caps",
                "kind": "chapter",
                "locutor_file": "locutor/chapters/prose-all-caps.txt",
                "locutor_sha256": sha256_file(prose_all_caps_locutor),
                "reviewed_by": "codex",
                "base_outputs": copy.deepcopy(
                    narrator_changes["outputs"][0]["base_outputs"]
                ),
            }
        )
        prose_narrator_changes_path = (
            image_book_root / "metadata" / "narrator-changes-prose-all-caps.json"
        )
        write_json(prose_narrator_changes_path, prose_narrator_changes)
        prose_all_caps_findings = audit_text(
            prose_all_caps_locutor.read_text(encoding="utf-8")
        )
        prose_all_caps_review = draft_review(
            image_book_root,
            prose_all_caps_locutor,
            prose_all_caps_findings,
            prose_narrator_changes_path,
        )
        assert all(
            finding["category"] == ""
            and finding["suggested_category"] == "prose"
            for finding in prose_all_caps_review["findings"]
        )
        prose_all_caps_review["status"] = "approved"
        prose_all_caps_review["reviewed_by"] = "codex"
        for finding in prose_all_caps_review["findings"]:
            finding["category"] = "prose"
            finding["status"] = "preserved"
            finding["reason"] = "The all-caps prose was reviewed for speech."
            finding["reviewed_by"] = "codex"
        prose_all_caps_review["pronunciation_review"]["status"] = "approved"
        prose_all_caps_review["pronunciation_review"]["reviewed_by"] = "codex"
        prose_all_caps_review["pronunciation_review"]["entries"] = []
        prose_all_caps_review_path = (
            image_book_root / "metadata" / "narrator-review-prose-all-caps.json"
        )
        write_json(prose_all_caps_review_path, prose_all_caps_review)
        prose_all_caps_errors, _ = validate_review(
            image_book_root,
            prose_all_caps_review_path,
            prose_all_caps_locutor,
            prose_narrator_changes_path,
        )
        for finding in prose_all_caps_findings:
            if finding.locutor_span in {"PDF", "EPUB"}:
                assert any(finding.id in error for error in prose_all_caps_errors)
        wrong_scope_review = copy.deepcopy(narrator_review)
        wrong_scope_review["review_scope"]["logical_pages"] = [1, 2]
        write_json(narrator_review_path, wrong_scope_review)
        wrong_scope_errors, _ = validate_review(
            image_book_root,
            narrator_review_path,
            archaic_locutor,
        )
        assert any("exactly cover the selected narrator output" in error for error in wrong_scope_errors)
        write_json(narrator_review_path, narrator_review)
        pending_narrator_review = copy.deepcopy(narrator_review)
        pending_narrator_review["status"] = "needs-review"
        write_json(narrator_review_path, pending_narrator_review)
        run_fails(
            str(ROOT / "validate_narrator_quality.py"),
            "--book-root",
            str(image_book_root),
            "--input-file",
            str(archaic_locutor),
        )
        custom_narrator_changes_path = (
            image_book_root / "metadata" / "narrator-changes-custom.json"
        )
        custom_narrator_changes = copy.deepcopy(narrator_changes)
        custom_narrator_changes["quality_review_test"] = "custom-narrator-changes"
        write_json(custom_narrator_changes_path, custom_narrator_changes)
        custom_draft_path = image_book_root / "metadata" / "narrator-review-custom.draft.json"
        run(
            str(ROOT / "narrator_quality.py"),
            "--book-root",
            str(image_book_root),
            "--input-file",
            str(archaic_locutor),
            "--output",
            str(custom_draft_path),
            "--narrator-changes",
            str(custom_narrator_changes_path),
        )
        custom_draft = json.loads(custom_draft_path.read_text(encoding="utf-8"))
        assert custom_draft["narrator_changes_sha256"] == sha256_file(
            custom_narrator_changes_path
        )
        assert custom_draft["review_scope"]["logical_pages"] == [1]
        custom_narrator_review = copy.deepcopy(narrator_review)
        custom_narrator_review["narrator_changes_sha256"] = sha256_file(
            custom_narrator_changes_path
        )
        write_json(narrator_review_path, custom_narrator_review)
        custom_quality_errors, _ = validate_review(
            image_book_root,
            narrator_review_path,
            archaic_locutor,
            custom_narrator_changes_path,
        )
        assert not custom_quality_errors
        default_quality_errors, _ = validate_review(
            image_book_root,
            narrator_review_path,
            archaic_locutor,
        )
        assert any(
            "narrator_changes_sha256" in error for error in default_quality_errors
        )
        write_json(narrator_review_path, narrator_review)
        narrator_review_path.unlink()
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(archaic_locutor),
            "--output-dir",
            str(audio_root / "chatterbox-missing-quality"),
            "--book-root",
            str(image_book_root),
            "--require-quality",
            "--format",
            "wav",
        )
        invalid_narrator_review = copy.deepcopy(narrator_review)
        invalid_narrator_review["output_sha256"] = "0" * 64
        write_json(narrator_review_path, invalid_narrator_review)
        run_fails(
            str(ROOT / "validate_narrator_quality.py"),
            "--book-root",
            str(image_book_root),
            "--input-file",
            str(archaic_locutor),
        )
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(archaic_locutor),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-quality"),
            "--book-root",
            str(image_book_root),
            "--require-quality",
            "--format",
            "wav",
        )
        write_json(narrator_review_path, narrator_review)
        run(
            str(ROOT / "validate_narrator_quality.py"),
            "--book-root",
            str(image_book_root),
            "--input-file",
            str(archaic_locutor),
        )
        image_epub_manifest = image_book_root / "metadata" / "epub-manifest.json"
        missing_chapter_outputs = copy.deepcopy(image_ledger)
        missing_chapter_outputs.pop("chapter_outputs")
        image_ledger_path.write_text(
            json.dumps(missing_chapter_outputs, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        image_ledger_path.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        alternate_ledger = image_book_root / "metadata" / "alternate-text-ledger.json"
        alternate_ledger.write_text(
            json.dumps(image_ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(alternate_ledger),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        unplaced_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        assert unplaced_manifest["documents"][0]["asset_ids"] == []

        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        image_assets["assets"][0]["classification"] = {
            "content": "illustration",
            "text_pixels": "none",
            "restoration_eligibility": "eligible",
            "evidence": ["The PDF source page contains this standalone non-text illustration."],
        }
        image_assets["assets"][0]["epub"] = {
            "role": "illustration",
            "placement": "end",
            "document_id": "chapter-001",
            "alt_text": "",
        }
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        unanchored_assets = copy.deepcopy(image_assets)
        unanchored_assets["assets"][0]["epub"]["document_id"] = None
        image_assets_path.write_text(
            json.dumps(unanchored_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        plain_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        assert "visual_profile" not in plain_manifest
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--visual-profile",
            "antique-paper",
            "--output",
            str(image_epub_manifest),
        )
        visual_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        assert visual_manifest["visual_profile"] == {
            "name": "antique-paper",
            "cover": {"mode": "editorial"},
        }
        assert visual_manifest["book"]["subtitle"] == "Coração e Orixás"
        assert visual_manifest["book"]["publication_place"] == "São Paulo"
        canonical_epub = image_book_root / "exports" / "epub" / "canonical.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--output",
            str(canonical_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub",
            str(canonical_epub),
        )
        escaped_epub = root / "escaped-output.epub"
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--output",
            str(escaped_epub),
        )
        assert not escaped_epub.exists()
        with zipfile.ZipFile(canonical_epub) as archive:
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
            assert "OEBPS/nav.xhtml" in archive.namelist()
            assert any(path.startswith("OEBPS/images/") for path in archive.namelist())
            assert "OEBPS/text/000-cover.xhtml" in archive.namelist()
            assert "OEBPS/images/editorial-cover.jpg" in archive.namelist()
            assert "OEBPS/fonts/IMFeENrm28P.ttf" in archive.namelist()
            assert "OEBPS/fonts/IMFeENit28P.ttf" in archive.namelist()
            assert "OEBPS/fonts/OFL.txt" in archive.namelist()
            stylesheet = archive.read("OEBPS/styles/book.css").decode("utf-8")
            for color in ("#FFFFFF", "#000000"):
                assert color in stylesheet
            assert 'font-family: "IM FELL English";' in stylesheet
            assert "../fonts/IMFeENrm28P.ttf" in stylesheet
            assert "../fonts/IMFeENit28P.ttf" in stylesheet
            assert archive.read("OEBPS/fonts/IMFeENrm28P.ttf") == (
                ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENrm28P.ttf"
            ).read_bytes()
            assert archive.read("OEBPS/fonts/IMFeENit28P.ttf") == (
                ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENit28P.ttf"
            ).read_bytes()
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            assert 'id="editorial-cover"' in opf
            assert 'properties="cover-image"' in opf
            assert 'id="image-1" href="images/pdf-page-0001-image-01.jpg" media-type="image/jpeg"/>' in opf
            cover = archive.read("OEBPS/text/000-cover.xhtml").decode("utf-8")
            assert 'epub:type="cover"' in cover
            assert "../images/editorial-cover.jpg" in cover
            assert 'alt="Capa editorial: Livro com Ação, por Antônio de Teste."' in cover
            chapter_xhtml = archive.read("OEBPS/text/001-chapter-001.xhtml").decode("utf-8")
            assert "Primeiro verso<br/>Segundo verso<br/>Terceiro verso" in chapter_xhtml

        visual_sidecar = json.loads(canonical_epub.with_suffix(".epub.json").read_text(encoding="utf-8"))
        assert visual_sidecar["visual_profile"]["name"] == "antique-paper"
        assert visual_sidecar["visual_profile"]["cover"]["epub_path"] == "OEBPS/images/editorial-cover.jpg"
        assert len(visual_sidecar["visual_profile"]["resources"]) == 3

        from io import BytesIO
        from PIL import Image, ImageFont
        from epub_presentation import cover_image

        font = ImageFont.truetype(
            str(ROOT.parent / "assets" / "fonts" / "im-fell-english" / "IMFeENrm28P.ttf"),
            48,
        )
        missing_glyph = bytes(font.getmask("\uffff"))
        for character in ("\u00e1", "\u00e3", "\u00e7", "\u00e9", "\u00ed", "\u00f3", "\u00f5", "\u00fa"):
            assert bytes(font.getmask(character)) != missing_glyph
        long_cover = cover_image(
            {
                "title": "Uma História Editorial de Muitas Linhas para Validar o Layout da Capa",
                "subtitle": "Uma edição cuidadosamente organizada para leitura digital",
                "author": "Nome Composto do Autor de Uma Obra Muito Extensa",
                "publication_place": "São Paulo",
                "publication_year": 1933,
            }
        )
        assert long_cover.startswith(b"\xff\xd8")
        with Image.open(BytesIO(long_cover)) as generated_cover:
            assert generated_cover.getpixel((0, 0)) == (255, 255, 255)
        try:
            cover_image({"title": "X" * 2000})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected an oversized editorial cover title to fail clearly.")

        legacy_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        legacy_manifest.pop("visual_profile")
        legacy_manifest_path = image_book_root / "metadata" / "epub-manifest-legacy.json"
        legacy_manifest_path.write_text(
            json.dumps(legacy_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        legacy_epub = image_book_root / "exports" / "epub" / "legacy.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(legacy_manifest_path),
            "--output",
            str(legacy_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(legacy_manifest_path),
            "--epub",
            str(legacy_epub),
        )
        with zipfile.ZipFile(legacy_epub) as archive:
            assert "OEBPS/text/000-cover.xhtml" not in archive.namelist()
            assert "OEBPS/fonts/IMFeENrm28P.ttf" not in archive.namelist()

        invalid_visual_manifest = json.loads(image_epub_manifest.read_text(encoding="utf-8"))
        invalid_visual_manifest["visual_profile"]["name"] = "unknown"
        invalid_visual_manifest_path = image_book_root / "metadata" / "epub-manifest-invalid-visual.json"
        invalid_visual_manifest_path.write_text(
            json.dumps(invalid_visual_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--epub-manifest",
            str(invalid_visual_manifest_path),
            "--output",
            str(image_book_root / "exports" / "epub" / "invalid-visual.epub"),
        )

        image_assets = json.loads(image_assets_path.read_text(encoding="utf-8"))
        original_asset = image_assets["assets"][0]
        original_path = image_book_root / original_asset["original"]["path"]
        restored_path = (
            image_book_root
            / "assets"
            / "restoration"
            / "approved"
            / f"{original_path.stem}.png"
        )
        restored_path.parent.mkdir(parents=True)
        from PIL import Image

        with Image.open(original_path) as original_image:
            original_image.save(restored_path, "PNG")
        original_asset["restoration"] = {
            "status": "approved",
            "approved": {
                "path": restored_path.relative_to(image_book_root).as_posix(),
                "sha256": sha256_file(restored_path),
                "original_sha256": original_asset["original"]["sha256"],
                "media_type": "image/jpeg",
                "tool": "codex-imagegen",
                "prompt": "Restore only visual defects; preserve all content.",
                "reviewed_by": "codex test",
                "approved_at": "2026-07-13T00:00:00Z",
            },
        }
        original_asset["classification"]["text_pixels"] = "mixed"
        original_asset["classification"]["restoration_eligibility"] = "review_required"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "none"
        original_asset["classification"]["restoration_eligibility"] = "eligible"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["restoration"]["approved"]["media_type"] = "image/png"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_asset["classification"]["restoration_eligibility"] = "prohibited"
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "mixed"
        original_asset["classification"]["restoration_eligibility"] = "manual_exception"
        original_asset["restoration"]["approved"]["exception_reason"] = (
            "Approved visual cleanup of a source facsimile; original remains canonical evidence."
        )
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        original_asset["classification"]["text_pixels"] = "none"
        original_asset["classification"]["restoration_eligibility"] = "eligible"
        original_asset["restoration"]["approved"].pop("exception_reason")
        image_assets_path.write_text(
            json.dumps(image_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(image_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        escaped_approved_assets = copy.deepcopy(image_assets)
        escaped_approved = escaped_approved_assets["assets"][0]["restoration"]["approved"]
        escaped_approved["path"] = (
            "assets/restoration/approved/../../../assets/images/original/"
            + original_path.name
        )
        escaped_approved["sha256"] = original_asset["original"]["sha256"]
        escaped_approved["media_type"] = original_asset["original"]["media_type"]
        escaped_approved_assets_path = image_book_root / "metadata" / "escaped-approved-assets.json"
        escaped_approved_assets_path.write_text(
            json.dumps(escaped_approved_assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_assets_manifest.py"),
            "--assets-manifest",
            str(escaped_approved_assets_path),
            "--book-root",
            str(image_book_root),
            "--book-map",
            str(image_map_path),
            "--check-files",
        )
        run(
            str(ROOT / "build_epub_manifest.py"),
            "--book-map",
            str(image_map_path),
            "--ledger",
            str(image_ledger_path),
            "--assets-manifest",
            str(image_assets_path),
            "--text-root",
            str(image_text_root),
            "--layout",
            "legacy",
            "--output",
            str(image_epub_manifest),
        )
        restored_epub = image_book_root / "exports" / "epub" / "restored.epub"
        run(
            str(ROOT / "export_epub.py"),
            "--book-root",
            str(image_book_root),
            "--image-edition",
            "approved-restored",
            "--output",
            str(restored_epub),
        )
        run(
            str(ROOT / "validate_epub_export.py"),
            "--book-root",
            str(image_book_root),
            "--epub",
            str(restored_epub),
            "--image-edition",
            "approved-restored",
        )
        with zipfile.ZipFile(restored_epub) as archive:
            restored_opf = archive.read("OEBPS/content.opf").decode("utf-8")
            assert 'media-type="image/png"' in restored_opf
            assert any(
                path.endswith(".png")
                for path in archive.namelist()
                if path.startswith("OEBPS/images/")
            )

        audio_root = image_book_root / "audio"
        mock_wav = audio_root / "mock" / "wav" / "audiobook.wav"
        mock_wav.parent.mkdir(parents=True)
        write_wav(mock_wav, speech_frames)
        assert mock_wav.is_file()
        compressed_audio_root = audio_root / "mock" / "m4a"
        compressed_audio_root.mkdir(parents=True)
        compressed_audio = compressed_audio_root / "audiobook.m4a"
        transcode(mock_wav, compressed_audio, "m4a")
        assert compressed_audio.is_file()
        audio_manifest_path = image_book_root / "metadata" / "audio-manifest.json"
        audio_manifest = {
            "schema_version": "1.0",
            "mock": True,
            "render_mode": "mock",
            "final_audio": compressed_audio.relative_to(image_book_root).as_posix(),
            "final_audio_sha256": sha256_file(compressed_audio),
        }
        audio_manifest_path.write_text(
            json.dumps(audio_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assert audio_manifest["final_audio_sha256"] == sha256_file(compressed_audio)
        mp3_audio_root = audio_root / "mock" / "mp3"
        mp3_audio_root.mkdir(parents=True)
        mp3_audio = mp3_audio_root / "audiobook.mp3"
        transcode(mock_wav, mp3_audio, "mp3")
        assert mp3_audio.is_file()
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=sample_rate,channels,bit_rate",
                "-of",
                "json",
                str(mp3_audio),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["sample_rate"] == "44100"
        assert stream["channels"] == 1
        assert stream["bit_rate"] == "128000"
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(compressed_audio),
        )
        audio_manifest["mock"] = False
        audio_manifest["render_mode"] = "real"
        audio_manifest_path.write_text(
            json.dumps(audio_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(compressed_audio),
        )
        published_audio = image_public_root / f"{image_public_root.name}.mp3"
        published_epub = image_public_root / f"{image_public_root.name}.epub"
        assert not published_audio.exists()
        assert not published_epub.exists()

        real_audio = audio_root / "real" / "audiobook.mp3"
        real_audio.parent.mkdir(parents=True, exist_ok=True)
        real_audio.write_bytes(mp3_audio.read_bytes())
        real_lineage = {
            "schema_version": narrator_changes["schema_version"],
            "narrator_changes_sha256": sha256_file(narrator_changes_path),
            "mode": narrator_changes["mode"],
            "base_edition": narrator_changes["base_edition"],
            "base_ledger_sha256": narrator_changes["base_ledger_sha256"],
            "output_id": narrator_changes["outputs"][0]["id"],
            "path": "metadata/narrator-changes.json",
        }
        real_manifest = {
            "schema_version": "1.0",
            "mock": False,
            "render_mode": "real",
            "engine": "chatterbox-multilingual-v3-pt-br",
            "input_file": archaic_locutor.relative_to(image_book_root).as_posix(),
            "input_sha256": sha256_file(archaic_locutor),
            "narrator_lineage": real_lineage,
            "final_audio": real_audio.relative_to(image_book_root).as_posix(),
            "final_audio_sha256": sha256_file(real_audio),
        }
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        real_audio.write_bytes(b"changed-audio")
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
        )
        real_audio.write_bytes(mp3_audio.read_bytes())
        real_manifest["final_audio_sha256"] = sha256_file(real_audio)
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        untracked_real_manifest = dict(real_manifest)
        untracked_real_manifest.pop("narrator_lineage")
        audio_manifest_path.write_text(
            json.dumps(untracked_real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
        )
        non_chatterbox_manifest = dict(real_manifest)
        non_chatterbox_manifest.pop("engine")
        audio_manifest_path.write_text(
            json.dumps(non_chatterbox_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
        )
        audio_manifest_path.write_text(
            json.dumps(real_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        invalid_epub = image_book_root / "exports" / "epub" / "invalid-no-sidecar.epub"
        invalid_epub.write_bytes(b"not an epub")
        run_fails(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
            "--epub",
            str(invalid_epub),
        )
        assert not published_audio.exists()
        assert not published_epub.exists()
        assert "publication" not in json.loads(audio_manifest_path.read_text(encoding="utf-8"))
        revised_epub = image_book_root / "exports" / "epub" / "revised.epub"
        revised_epub.write_bytes(restored_epub.read_bytes())
        revised_sidecar = json.loads(
            restored_epub.with_suffix(".epub.json").read_text(encoding="utf-8")
        )
        revised_sidecar["epub_path"] = revised_epub.relative_to(image_book_root).as_posix()
        revised_sidecar["epub_sha256"] = sha256_file(revised_epub)
        revised_sidecar["text_edition"] = "revised-pt-br"
        revised_layout_path = image_book_root / "metadata" / "epub-layout.json"
        write_json(revised_layout_path, {"documents": []})
        revised_sidecar["layout"] = {
            "mode": "semantic",
            "path": "metadata/epub-layout.json",
            "sha256": sha256_file(revised_layout_path),
        }
        revision_ledger_path = image_book_root / "metadata" / "revision-ledger.json"
        write_json(revision_ledger_path, {"schema_version": "1.0"})
        revised_sidecar["revision_ledger_sha256"] = sha256_file(revision_ledger_path)
        revised_epub.with_suffix(".epub.json").write_text(
            json.dumps(revised_sidecar, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        revised_manifest = {
            "text_edition": "revised-pt-br",
            "language": revised_sidecar["language"],
            "book_map_sha256": sha256_file(image_book_root / "metadata" / "book-map.json"),
            "text_ledger_sha256": sha256_file(image_book_root / "metadata" / "text-ledger.json"),
            "assets_manifest_sha256": sha256_file(image_book_root / "metadata" / "assets-manifest.json"),
            "revision_ledger_sha256": sha256_file(revision_ledger_path),
        }
        if "layout" in revised_sidecar:
            revised_manifest["layout"] = revised_sidecar["layout"]
        write_json(
            image_book_root / "metadata" / "epub-manifest.revised.json",
            revised_manifest,
        )
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_public_root),
            "--epub",
            str(revised_epub),
        )
        published_revised_epub = image_public_root / f"{image_public_root.name}.epub"
        assert published_revised_epub.read_bytes() == revised_epub.read_bytes()
        assert json.loads(
            revised_epub.with_suffix(".epub.json").read_text(encoding="utf-8")
        )["publication"]["text_edition"] == "revised-pt-br"
        run(
            str(ROOT / "publish_artifacts.py"),
            "--book-root",
            str(image_book_root),
            "--audio",
            str(real_audio),
            "--epub",
            str(restored_epub),
            "--overwrite",
        )
        assert published_audio.read_bytes() == real_audio.read_bytes()
        assert published_epub.read_bytes() == restored_epub.read_bytes()
        publication_manifest = json.loads(
            (image_book_root / "metadata" / "publication-manifest.json").read_text(encoding="utf-8")
        )
        assert publication_manifest["artifacts"]["audio"]["path"] == published_audio.name
        assert publication_manifest["artifacts"]["epub"]["path"] == published_epub.name
        assert json.loads(audio_manifest_path.read_text(encoding="utf-8"))["publication"]["sha256"] == sha256_file(
            published_audio
        )
        assert json.loads(restored_epub.with_suffix(".epub.json").read_text(encoding="utf-8"))[
            "publication"
        ]["sha256"] == sha256_file(published_epub)

        run(str(ROOT / "render_chatterbox.py"), "--help")
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-max-chars"),
            "--standalone",
            "--max-chars",
            "321",
        )
        invalid_chatterbox_text = image_text_root / "locutor" / "invalid-chatterbox.txt"
        invalid_chatterbox_text.parent.mkdir(parents=True, exist_ok=True)
        invalid_chatterbox_text.write_text("[thoughtful] Texto.", encoding="utf-8")
        invalid_chatterbox_output = audio_root / "chatterbox-invalid-text"
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(invalid_chatterbox_text),
            "--output-dir",
            str(invalid_chatterbox_output),
            "--standalone",
        )
        assert not invalid_chatterbox_output.exists()
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-min-p"),
            "--standalone",
            "--min-p",
            "1.1",
        )
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(audio_root / "chatterbox-invalid-silence"),
            "--standalone",
            "--silence-seconds",
            "-0.1",
        )
        chatterbox_invalid_output = audio_root / "chatterbox-invalid"
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(image_page_file),
            "--output-dir",
            str(chatterbox_invalid_output),
            "--book-root",
            str(image_book_root),
            "--format",
            "wav",
        )
        assert not chatterbox_invalid_output.exists()
        untracked_locutor = image_text_root / "locutor" / "chapters" / "untracked.txt"
        untracked_locutor.write_text("Texto de locutor sem linhagem declarada.", encoding="utf-8")
        untracked_lineage_output = audio_root / "chatterbox-untracked-lineage"
        run_fails(
            str(ROOT / "render_chatterbox.py"),
            "--input-file",
            str(untracked_locutor),
            "--output-dir",
            str(untracked_lineage_output),
            "--book-root",
            str(image_book_root),
            "--format",
            "wav",
        )
        assert not untracked_lineage_output.exists()

        plugin_root = ROOT.parent
        marketplace = {
            "name": "test",
            "plugins": [
                {
                    "name": "audiobook-codex",
                    "source": {"source": "local", "path": "./wrong-path"},
                    "policy": {"installation": "NOT_AVAILABLE", "authentication": "ON_USE"},
                    "category": "Other",
                }
            ],
        }
        bad_marketplace = root / "bad-marketplace.json"
        bad_marketplace.write_text(
            json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            str(ROOT / "validate_plugin_local.py"),
            "--plugin-root",
            str(plugin_root),
            "--marketplace",
            str(bad_marketplace),
        )

        if os.environ.get("CHATTERBOX_REAL_SMOKE") == "1":
            chatterbox_python = os.environ.get("CHATTERBOX_PYTHON")
            if not chatterbox_python:
                raise AssertionError(
                    "CHATTERBOX_REAL_SMOKE=1 requires CHATTERBOX_PYTHON."
                )
            chatterbox_smoke_text = calibration_text
            chatterbox_smoke_input = root / "chatterbox-cuda-smoke.txt"
            chatterbox_smoke_input.write_text(chatterbox_smoke_text, encoding="utf-8")
            chatterbox_smoke_output = root / "chatterbox-cuda-smoke"
            run_with_python(
                chatterbox_python,
                str(ROOT / "render_chatterbox.py"),
                "--input-file",
                str(chatterbox_smoke_input),
                "--output-dir",
                str(chatterbox_smoke_output),
                "--standalone",
                "--device",
                "cuda",
                "--format",
                "wav",
                "--overwrite",
            )
            chatterbox_manifest = json.loads(
                (chatterbox_smoke_output / "audio-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            chatterbox_segment = (
                chatterbox_smoke_output / "segments" / "segment-0001.wav"
            )
            chatterbox_master_wav = (
                chatterbox_smoke_output / "raw" / "audiobook.master.wav"
            )
            chatterbox_final_wav = chatterbox_smoke_output / "raw" / "audiobook.wav"
            chatterbox_final_audio = chatterbox_smoke_output / "audiobook.wav"
            assert chatterbox_manifest["mock"] is False
            assert chatterbox_manifest["render_mode"] == "real"
            assert chatterbox_manifest["engine"] == "chatterbox-multilingual-v3-pt-br"
            assert chatterbox_manifest["profile"] == "masculina-v1"
            assert chatterbox_manifest["device"] == "cuda"
            assert chatterbox_manifest["sample_rate"] == 24000
            assert len(chatterbox_manifest["segments"]) == 1
            assert chatterbox_manifest["segments"][0]["locutor_line"] == 1
            assert (
                chatterbox_manifest["segments"][0]["character_count"]
                == len(chatterbox_smoke_text)
            )
            assert chatterbox_manifest["segments"][0]["warnings"] == [
                "uses punctuation normalized by Chatterbox"
            ]
            assert chatterbox_manifest["duration_seconds"] > 0
            assert chatterbox_segment.is_file() and chatterbox_segment.stat().st_size > 44
            assert chatterbox_master_wav.is_file()
            assert chatterbox_final_wav.is_file()
            assert chatterbox_final_audio.is_file()
            assert (
                chatterbox_manifest["final_wav_sha256"]
                == sha256_file(chatterbox_final_wav)
            )
            assert (
                chatterbox_manifest["master_wav_sha256"]
                == sha256_file(chatterbox_master_wav)
            )
            assert (
                chatterbox_manifest["master_wav_sha256"]
                == MASCULINA_PROFILE_CALIBRATION["main_prompt_wav_sha256"]
            )
            assert (
                chatterbox_manifest["final_audio_sha256"]
                == sha256_file(chatterbox_final_audio)
            )
            run(
                str(ROOT / "narration_plan.py"),
                "--book-root",
                str(image_book_root),
                "--input-file",
                str(archaic_locutor),
                "--refresh-approved-metadata",
            )
            chatterbox_lineage_output = audio_root / "chatterbox-cuda-lineage"
            run_with_python(
                chatterbox_python,
                str(ROOT / "render_chatterbox.py"),
                "--book-root",
                str(image_book_root),
                "--input-file",
                str(archaic_locutor),
                "--output-dir",
                str(chatterbox_lineage_output),
                "--device",
                "cuda",
                "--format",
                "wav",
                "--overwrite",
            )
            chatterbox_lineage_manifest = json.loads(
                (image_book_root / "metadata" / "audio-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            assert chatterbox_lineage_manifest["profile"] == "masculina-v1"
            assert chatterbox_lineage_manifest["narrator_lineage"]["mode"] == "archaic-modernized"
            assert (
                chatterbox_lineage_manifest["narrator_lineage"]["output_id"]
                == narrator_changes["outputs"][0]["id"]
            )
            assert (
                chatterbox_lineage_manifest["final_audio_sha256"]
                == sha256_file(chatterbox_lineage_output / "audiobook.wav")
            )

    print("Audiobook Codex script tests passed.")


if __name__ == "__main__":
    main()
