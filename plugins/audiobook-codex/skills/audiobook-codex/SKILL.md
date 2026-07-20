---
name: audiobook-codex
description: Build a local audiobook plus semantic EPUB and paired non-facsimile PDF from a PDF or EPUB using only Codex, native tools, Codex agents, the PDF plugin, optional Computer Use, native image editing, and the local Chatterbox PT-BR runtime. Use for book mapping, asset inventory, faithful source transcription, contextual whole-book PT-BR translation with auditable ambiguity research, optional faithful fluid PT-BR reading editions, PT-BR narrator text, EPUB/PDF export, optional reviewed image restoration, and local audiobook audio without third-party OCR or external LLMs.
---

# Audiobook Codex

Use this skill as a source-faithful pipeline with optional translation and fluid-reading stages. Keep the source artifact, visual assets, translation artifact, fluid artifact, narrator artifact, EPUB/PDF exports, and generated audio traceable to the same source hash and `book-map.json`. The default public library root is `E:\Pessoal\Library`; each public book root is `Nome do Livro - Ano - Autor`, contains `assembly/`, the selected EPUB/PDF pair, and the final MP3, and is what `--book-root` means. When `target` is `both`, the fluid pair is separately named; when it is `fluid`, it is the selected public pair. Manifests and sidecars stay assembly-relative.

Read [artifact-contract.md](references/artifact-contract.md) before any stage. Read [swarm-protocol.md](references/swarm-protocol.md) when delegating. Read [translation-policy.md](references/translation-policy.md) before translating. Read [fluid-edition-policy.md](references/fluid-edition-policy.md) before creating a fluid edition. Read [narrator-policy.md](references/narrator-policy.md) before creating narrator text.

Before adding or replacing a local voice reference, TTS engine, or official narrator
profile, use `$voice-calibration`. Do not calibrate against hosted TTS from this
pipeline and do not alter an approved profile without its separate promotion evidence.

## Stable Stage Selectors

The public invocation contract is intentionally limited to these stage selectors:

```text
$audiobook-codex stage=PHASE-1
$audiobook-codex stage=PHASE-2
```

Treat omitted options as the canonical defaults below. Resolve the active source or
book root from the attachment and current task context, reuse already validated stage
artifacts, and ask only when required book identity is unavailable or more than one
valid target or edition remains ambiguous. Explicit user options may override a default
only when they preserve the contracts and review gates in this skill.

- `PHASE-1`: native-only PDF/EPUB mapping followed by faithful transcription. It
  creates and validates the book map, asset manifest, immutable `text/source`, text
  ledger, semantic original-edition layout, and source EPUB manifest. It never creates
  narration or public EPUB/PDF/audio artifacts.
- `PHASE-2`: selects and produces the approved publication edition(s): `complete`,
  `fluid`, or `both`. It derives the required narrator text, renders the matching
  Chatterbox audio, exports the matching semantic EPUB/PDF pair, validates, and
  publishes only those selected edition artifacts.

`metadata/publication-selection.json` is the internal per-book flag. It uses
`target: "complete" | "fluid" | "both"` and defaults to `complete` at preflight. Change
it only after an explicit user request, using
`scripts/publication_selection.py --book-root <book-root> --target <target> --updated-by <reviewer> --reason <reason>`.
`complete` means the approved complete reading edition (`source` for a Portuguese book
or `translated-pt-br` for a whole foreign-language book); `fluid` means
`fluid-pt-br`; `both` produces the two edition tracks serially. The audio always uses
the same selected base edition as its EPUB/PDF pair.

Keep stage behavior, defaults, paths, role routing, and validation gates inside this
skill, its references, and the repository scripts. Do not copy those details back into
AHK bindings or other public stage prompts.

## Internal Swarm Defaults

Use [swarm-protocol.md](references/swarm-protocol.md) as the executable delegation
contract. Writable work requires validated schema `1.0` claim maps and writes only to
exclusive files plus `assembly/metadata/work` shards. Keep warm role pools supplied
through claim messages and use a sliding queue instead of waiting for a whole batch.
Workers never edit canonical shared ledgers/manifests; the main agent validates,
independently verifies, and atomically promotes accepted shards.

Default upper bounds are 4-6 structure scouts, 6-10 transcribers plus 3-5 source
verifiers, 4-6 translators or fluid editors plus 2-3 literary verifiers, and 3-4
narrator workers plus 2 narrator verifiers. Admit fewer when the book has fewer
independent units or when verification/merge backpressure is active. Computer Use,
native restoration, canonical merge, publication, and Chatterbox inference remain
single-owner resources.

## Non-Negotiable Rules

- Use no third-party OCR service, external LLM, browser chat UI, or hosted TTS.
- Use the PDF plugin and rendered images as the primary visual source. Use Computer Use only when a desktop viewer is necessary for zoom, navigation, or screenshots.
- Treat PDF text extraction as evidence, never as an authoritative transcription.
- Never alter `text/source` or an approved `text/translation/pt-BR`. Create fluid reading edits only in `text/fluid/pt-BR` and speech-oriented changes only in `text/locutor`.
- Use `text/translation/pt-BR` only for a whole source book written in another language. A Portuguese book with intentional isolated English or other foreign words is not a translation case.
- Keep `assembly/source/`, `assembly/pages/`, and `assembly/assets/images/original/` immutable after preflight.
- Never transcribe from restored or generated pixels. `text/source` may use only original PDF or EPUB evidence.
- A generated image is a labeled derivative, never a replacement for an original scan or a claim about unreadable source content.
- Never restore text, handwriting, captions, signatures, seals, or other evidence-bearing pixels with image generation.
- A user-approved full-scan cleanup is a `manual_exception`: retain the original, record the exception reason, publish it only in the restored EPUB, and never use it as textual evidence.
- Do not claim source fidelity until every mapped unit has a verified text-ledger record.
- Stop for review instead of guessing an uncertain word. Do not put uncertainty placeholders in final TXT files.

## Phase 1A: Map

1. Use `E:\Pessoal\Library` as the default library root. Never write book artifacts beside an attached source because Codex attachment paths can be temporary.
2. Run exactly one `scripts/preflight.py --source <attachment> --library-root "E:\Pessoal\Library" --title <title> --publication-year <year> --author <author>` for a book root. Those three metadata arguments are required. Preflight creates the public book root `Nome do Livro - Ano - Autor`, then writes all working files under its `assembly/` directory: `source/original.pdf` or `source/original.epub`, rendered page assets, `metadata/book-map.json`, and `metadata/assets-manifest.json`. Do not run concurrent preflights or canonical manifest refreshes for the same root.
3. After source pages and original assets are immutable, create non-overlapping read-only structure claims by page/spine range and asset-classification claims by asset ID. Inspect layout, rotation, logical page order, printed-page alignment, TOC, content range, chapter starts, blanks, exclusions, and visual asset classifications. Identify non-content supplementary back matter such as bibliography/references, glossaries, indexes, further-reading/source lists, and colophons so it remains in the textual editions but can be excluded from narration. Prefer a separate mapped output when boundaries permit; record complete page/output exclusions in `ranges.narration_excluded`.
4. For every visual asset, record source page or EPUB locator, role, whether text-bearing pixels exist, restoration eligibility, and evidence. Preserve the extracted original regardless of eligibility.
5. Complete the map with evidence for each decision. Use segmented page offsets when preliminary pages and main matter do not share one offset.
6. The main agent reconciles range boundaries and atomically freezes the canonical map and asset manifest. Run `scripts/validate_book_map.py --require-ready --check-files`, `scripts/validate_assets_manifest.py --book-map ... --check-files`, and working-layout validation in parallel on that same snapshot.

Do not begin transcription until the map is valid and its `analysis.status` is `ready` or `approved`.

## Phase 1B: Faithful Text

1. Create validated claim maps for exclusive contiguous page/spine ranges, normally six to ten units and aligned to chapter boundaries where practical. Supply one neighboring unit as read-only context. Transcribe from rendered source pages or EPUB source into per-unit files under `text/source/pages`.
2. Preserve wording, order, spelling, punctuation, and visible language. Do not correct, modernize, translate, expand numbers, or normalize prose.
3. Each transcriber writes one text-ledger shard under `metadata/work/text-ledger.d`; it never edits `metadata/text-ledger.json`. As soon as a shard is produced, send it to a different verifier instead of waiting for every transcriber.
4. Validate each claim with `scripts/verify_text_ledger.py --mode claim --claim-map ...`, then merge only accepted shards. Assemble verified chapter and book TXT deterministically with `scripts/assemble_text_outputs.py`; do not guess chapter files by glob or concatenate manually.
5. Run the complete approval gate `scripts/verify_text_ledger.py --mode approval --book-map ... --ledger ... --text-root ...`.
6. Create `metadata/epub-layout.json` for original-text EPUBs. It must cover every non-empty verified `text/source/pages` line exactly once, preserve the validated chapter-output order, and classify each block as a paragraph, quotation, dialogue, verse, or heading without altering source text. Mark a displayed or semantically separate direct quotation as `quotation`; EPUB and PDF exports render it with bilateral indentation. Do not use `quotation` for ordinary dialogue turns.
7. Run `scripts/validate_epub_layout.py --book-root <book-root>` and then `scripts/build_epub_manifest.py --layout semantic`. Review the generated reading order and image placement before publication. Do not invent captions or alt text from uncertain pixels.

For a source already in Portuguese, faithful text is PT-BR only to the extent the source itself is PT-BR. For another source language, a literal artifact and a PT-BR adaptation cannot be the same file.

## Optional Stage: Translate

Invoke directly when, and only when, the whole source book is in another language:

```text
$audiobook-codex stage=TRANSLATE
```

1. Start textual translation after `text/source` and `metadata/text-ledger.json` pass their complete gates and the whole-work language decision, brief, and glossary revision are reviewed. The source EPUB manifest is required before translation publication, not before chapter translation. Set `analysis.source_language` from the predominant language of the whole verified work, never from an isolated quotation or word; record page-backed source spans for every verified page in the translation decision.
2. Before translating, create the `faithful-contextual-ptbr-v1` book brief and reviewed glossary in `metadata/translation-ledger.json`. Record genre, period, setting, narrator voice, register, style goals, names policy, and intentional foreign-fragment policy.
3. Create one chapter claim per translator under `text/translation/pt-BR`; split only an exceptionally large chapter into contiguous scenes while retaining the full chapter and neighboring context. Each claim pins brief, glossary, chapter, and neighboring-context hashes and writes one translation shard. Treat pages as lineage units, not context boundaries. Apply a semantic-fidelity pass followed by a natural literary PT-BR pass. Do not overwrite `text/source`, treat translation as a source correction, or place speech-only changes outside `text/locutor`.
4. Use the book context and glossary first. Record every material ambiguity. If internal context remains insufficient, use only Codex-native research under `context-first-evidence-recorded-v1`, prioritize dictionaries and primary, official, or scholarly evidence, and record the reference, access date, finding, resolution, and reviewer. Never use browser chat, an external LLM, or a published online translation as translation text. Do not guess; `needs-review` or `unresolved` entries block approval.
5. Translators emit glossary and ambiguity proposals under `metadata/work` rather than editing shared global arrays. Validate claim shards with `scripts/verify_translation_ledger.py --mode claim --claim-map ...`; the main agent resolves proposals, publishes a new frozen glossary revision between claims, and deterministically merges accepted shards into schema `1.1` `metadata/translation-ledger.json`.
6. After all chapters are frozen, complete the semantic-fidelity, literary-naturalness, and whole-book-consistency review gates. Build `metadata/epub-layout.pt-br.json` so it covers every approved `text/translation/pt-BR/chapters` block exactly once in ledger order, binds `translation_ledger_sha256`, and never uses fluid-only `join_with_previous`. Export separate translated EPUB/PDF editions with semantic PT-BR text and metadata only after all gates are approved. Preserve source image pixels; approved restored images remain optional derivatives and never become translation evidence.
7. Do not translate isolated foreign words, titles, or short expressions inside a Portuguese source merely because they are not Portuguese. In a fluid PT-BR edition, translate every semantically complete foreign-language quotation or paragraph into PT-BR, preserving only proper names, trademarks, code, and genuinely material short foreign forms through a reviewed glossary decision. Record the translation in the fluid ledger; do not retain a whole English (or other foreign-language) paragraph merely because it is cited.

## Optional Stage: Fluid PT-BR Edition

Invoke directly when the user wants a single modern, fluent reading edition:

```text
$audiobook-codex stage=FLUID
```

1. Start only from one approved PT-BR base. Select `translated-pt-br` automatically when a complete approved translation exists; otherwise select `source` only for a Portuguese source.
2. Freeze one `metadata/fluid-style.json` for the whole book: contemporary register, faithful tone, natural cadence, terminology policy, titles, and reviewed glossary.
3. Create one exclusive chapter claim per editor and rewrite under `text/fluid/pt-BR` only. Each claim pins the approved base chapter, complete chapter context, neighboring context, style, and glossary hashes and writes one fluid shard. Preserve every claim, example, qualification, implication, cited passage, proper name, technical distinction, intentional ambiguity, and authorial stance. Remove inline parenthetical bibliographic apparatus such as `(PARÉS, 2011, p. 125)` while retaining the sentence or quotation it supports. When a complete paragraph is explicitly a translation of the immediately preceding paragraph, preserve the preceding original-language paragraph and omit the translated duplicate. Omit `Tradução livre` labels associated with that duplicate. Do not add explanations, examples, conclusions, or connective claims.
4. Modernize every genuinely archaic surface form into contemporary PT-BR, including orthography, diacritics, inflection, contractions, pronouns, syntax, and obsolete vocabulary. Apply this to authorial prose, dialogue, historical quotations, documentary excerpts, letters, epigraphs, captions, and footnotes; quotation status never justifies preserving archaic spelling or grammar. Preserve meaning, attribution, historical bias, authorial stance, proper names, and intentional characterization. Reduce expendable redundancy, untangle prolix syntax, clarify unambiguous referents, and improve contemporary PT-BR flow. New styles use schema `1.2`; schemas `1.0` and `1.1` are legacy-only. Intensity may vary by paragraph; the whole-book voice may not.
5. Validate each shard with `scripts/verify_fluid_edition_ledger.py --mode claim --claim-map ...`, then deterministically merge accepted shards. In schema `1.2`, cover every ordered base block exactly once in `metadata/fluid-edition-ledger.json`: map included blocks to sequential fluid positions and hashes; record excluded duplicate translations, translation labels, or standalone citation-only blocks with null fluid positions/hashes. Record both inline and standalone reference removal as `citation_reference_exclusion`; a standalone exclusion is valid only when the complete base block is citation apparatus. Create `text/fluid/pt-BR/book.txt` with `scripts/assemble_text_outputs.py` as the canonical ordered join of actual fluid chapter files. Legacy schemas retain exact one-to-one block coverage.
6. Require independent approval of semantic fidelity, no additions, no unsupported omissions, comprehensive archaic modernization, editorial exclusions, fluency, and whole-book consistency. The fluid `edition.book.title` must exactly preserve the selected base-edition title; put any reading label only in `edition.book.subtitle` (for example, `Versão de audiolivro`). Then build `metadata/epub-layout.fluid.json`, `metadata/epub-manifest.fluid.json`, and separate `fluid-pt-br` EPUB/PDF exports. For `target: both`, publish the validated fluid pair under distinct `-fluida` export filenames beside the complete pair. For `target: fluid`, use the unsuffixed book-title export filename and publish that selected pair without requiring a complete pair. When reviewed source evidence proves that a base block boundary is only a page-break continuation inside one paragraph, keep both included ledger blocks and mark the later fluid layout paragraph with `join_with_previous: true`. The continuation may cross only contiguous semantic `note` blocks: raw coverage order remains unchanged, while presentation completes the paragraph before rendering those notes.

## Phase 2: Selected Narrator, Audio, EPUB, and PDF

1. Create exclusive narrator claims by chapter and derive `text/locutor/chapters` from verified `text/source`, approved `text/translation/pt-BR`, or approved `text/fluid/pt-BR`, according to the explicitly selected base edition. Narrator workers write only chapter text and narrator metadata shards; the main agent owns the canonical full-book files.
2. Apply `faithful-natural-v1`: classify every locution in context, record each finding's category, make only evidence-backed speech changes, and review headings, dialogue turns, quotations, verse, punctuation, numbers, abbreviations, and pronunciation-sensitive terms. Do not narrate semantic footnotes or their attached reference markers. Do not narrate non-content supplementary back matter such as bibliography/references, glossaries, indexes, further-reading/source lists, or colophons. Preserve all of it in the selected textual edition and EPUB/PDF. Remove semantic notes through reviewed `footnote_exclusion` records tied to the note ID; remove a trailing partial-output back-matter span through one reviewed `supplementary_matter_exclusion`, while complete mapped outputs remain `mapped_exclusion` records.
3. Send every produced narrator claim to a different verifier immediately. Merge only accepted narrator-change/review shards, then run `scripts/narrator_quality.py` for the complete fan-in, resolve or explicitly preserve every finding, and approve `metadata/narrator-review.json`. This profile is mandatory for every new `stage=PHASE-2`; existing artifacts are reprocessed only when explicitly requested.
4. Write `metadata/narrator-changes.json` v2 for every allowed narrator transformation, including the base edition, base ledger, output file hashes, granular change records, one `footnote_exclusion` record for each omitted note-content span or attached marker span, and one `supplementary_matter_exclusion` for each reviewed trailing partial-output back-matter span. Use `archaic-modernized` only after exact textual evidence confirms it is needed; leave ordinary Portuguese faithful.
5. Build `metadata/narration-plan.json` with `scripts/narration_plan.py --refresh-approved-metadata`. It reflows only `text/locutor/book.txt` into complete semantic segments, preserves the approved normalized wording, records source chapter/paragraph/page provenance, and assigns continuation, sentence, paragraph, or heading pauses.
6. Run `scripts/validate_narrator_lineage.py`, `scripts/validate_narrator_quality.py`, and `scripts/validate_narration_plan.py` for the selected locutor file. A full Portuguese source with isolated English, Latin, or other intentional words remains `faithful`; it is not a translation case.
7. Run `scripts/render_chatterbox.py --require-lineage --require-quality` through the dedicated Chatterbox PT-BR environment. Book-root renders enforce all three gates even if those flags are omitted. Keep exactly one GPU inference lane. As each chapter reaches its final verified segment, enqueue an immutable chapter snapshot to one bounded CPU assembly worker while GPU inference continues with the next chapter. Write its immutable 1.0x master to `audio/<profile>/chapters/original/<chapter-id>.wav` and canonical delivery files to `audio/<profile>/chapters/final/<chapter-id>.wav` and `.mp3`, plus `metadata/audio-chapters-manifest.json`. Drain and validate the assembly queue before mounting the book from chapter masters. Keep comparison renders under `audio/<profile>/chapters/temp/`. The default delivery cadence is `1.20x`; override it only with `--publication-tempo <multiplier>`. Use `--chapters <chapter-id>` to render or reassemble only a reviewed chapter; it must not replace the full-book audio manifest.
8. Keep base text hashes, narrator hashes, voice, segment hashes, master and delivery durations, and publication tempo in the manifest. The atomic render journal resumes a segment at the same plan position only with its matching seed and verified WAV checksum. After a plan reflow, it may move only one unique, canonical, validated speech payload with matching text, model, voice, renderer, and settings; it retains the historical render seed and records `reused_from` provenance. Ambiguous, silent, corrupt, or changed text re-renders. Publication tempo is deliberately outside this identity. Use `--remount --publication-tempo <multiplier>` to rebuild chapters and the unified delivery audio from verified segments without regenerating speech. A pause-only plan change remounts the final audio without regenerating speech; changed text boundaries re-render only affected segments.
9. EPUB/PDF reader editions use only the generated ABNT-style title page with verified title, subtitle, author, place, and year. Do not add a separate editorial cover image; the legacy `antique-paper` profile exists only to validate historical manifests.
10. After the selected manifest/layout/ledger snapshot is frozen, run `scripts/export_reader_pair.py --book-root <book-root> --epub-output <output.epub> --pdf-output <output.pdf> --image-edition original` as the canonical paired reader-export entry point. Use the same command with the selected `--text-edition` and image edition for translated, fluid, or approved-restored pairs.
11. The paired exporter holds one shared exclusive book transaction lock, recovers any interrupted multi-file promotion from its persistent `metadata/work` journal, snapshots `metadata`, `text`, and `assets`, runs `scripts/export_epub.py` and `scripts/export_pdf.py` in parallel, rejects input drift, then runs `scripts/validate_epub_export.py` and `scripts/validate_pdf_export.py` in parallel and rejects post-validation or pre-promotion drift. Both branches must pass. Exporters may no-op only when the current output and complete sidecar contract match the input fingerprint, including the render-contract revision plus every bundled code/font/presentation dependency that can affect output. A readable sidecar with matching artifact identity and fingerprint may have stale deterministic contract fields repaired atomically without rewriting the EPUB/PDF; a missing, unreadable, or identity-incomplete sidecar forces a fresh export. Validators reuse one in-process archive or document snapshot.
12. The PDF is a reader edition from the EPUB manifest/layout, not a source-page facsimile. It writes a required `.pdf.json` sidecar, and paired export must run with the Codex bundled Python so ReportLab is available.
13. Optional restoration is review-gated. Use the native `image_gen` tool only for an asset classified as non-text and review-eligible. A full scan with text requires an explicit `manual_exception` and exception reason. Save candidates under `assets/restoration/candidates/`, record provenance, compare visually against the original, and promote only approved assets under `assets/restoration/approved/`.
14. Run `scripts/export_reader_pair.py --image-edition approved-restored` only after every selected derivative has a complete approval record. This produces and validates a separate restored edition; it never replaces the canonical EPUB/PDF pair.
15. Run `scripts/publish_artifacts.py --book-root <book-root> --audio <final-audio> --epub <final-epub> --pdf <final-pdf>` after validation. Publication uses the same exclusive book transaction lock and recoverable journal, validates the canonical manifest's own hashes and layout descriptor, and requires newly supplied reader sidecars to match current book, text, assets, language, layout presence/content, and edition-specific lineage. Complete editions use the public book-folder filename. With `target: both`, a validated `fluid-pt-br` pair keeps its distinct export filenames beside the complete pair. With `target: fluid`, the selected fluid pair may replace its earlier public fluid filename and does not require a complete pair. Provenance outputs remain under `assembly/audio/`, `assembly/exports/epub/`, and `assembly/exports/pdf/`.

## Tool Choice

- PDF with usable text layer: extract local text for comparison, then visually verify it.
- Image-only or mixed PDF: render pages and have Codex read the images directly.
- EPUB: inspect the EPUB spine/XHTML source directly; do not OCR rendered EPUB pages.
- Translation: invoke `stage=TRANSLATE` directly for whole foreign-language books only. Use `faithful-contextual-ptbr-v1`, complete chapter context plus neighboring scenes, a reviewed glossary, and evidence-recorded ambiguity resolution. Keep translated text and EPUB/PDF editions separate from source-faithful outputs.
- Fluid edition: invoke `stage=FLUID` directly. Use `fluid-faithful-ptbr-v1`, one approved PT-BR base, one fixed whole-book style/glossary, exact ordered block coverage, and independent no-addition/no-omission review. Keep fluid EPUB/PDF editions separate from faithful outputs.
- Computer Use: one agent only, serially, when native rendering cannot resolve a visual ambiguity.
- Native image restoration: use the built-in `image_gen` tool, not an API-key script or browser chat. Load a local edit target into the task first, restate every invariant in the prompt, save candidates only under `assets/restoration/candidates/`, and require a human approval record before export.
- Printed text and handwriting: use only original assets or deterministic local cleanup. Do not use generated output as a source for transcription or semantic EPUB/PDF text.
- PDF export: use `scripts/export_pdf.py` with the Codex bundled Python and ReportLab. Preserve non-facsimile semantics: render from the validated EPUB manifest/layout and text edition, never from source page screenshots.
- Chatterbox PT-BR: use `E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe`, the local V3 PT-BR model files, and the bundled `assets/voices/Masculina.mp3` reference by default. The canonical render profile is `masculina-v1`; `feminina-v1` remains an approved explicit alternative. Use line-delimited narrator text of at most 320 characters per non-empty line plus the required `paragraph-pauses-v1` narration plan. Do not send the voice reference or narrator text to a hosted TTS service.
- When installed, use `audiobook-structure`, `audiobook-transcriber`, `audiobook-translator`, `audiobook-editor`, `audiobook-narrator`, and `audiobook-verifier` for the matching swarm roles. Select the unsuffixed profile for medium reasoning, or the matching `-low`, `-high`, `-xhigh`, or `-max` profile when the assignment warrants it; every variant uses 5.6 Sol. Otherwise use equivalent claim-scoped roles with the same write boundaries.

## Validation

Run the smallest relevant check after each stage. Before declaring completion, run:

```powershell
python scripts/validate_book_map.py --book-map <book-map.json> --require-ready --check-files
python scripts/validate_assets_manifest.py --assets-manifest <assets-manifest.json> --book-root <book-root>
python scripts/verify_text_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --text-root <text-root>
python scripts/validate_epub_layout.py --book-root <book-root>
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --layout semantic
python scripts/export_reader_pair.py --book-root <book-root> --epub-output <output.epub> --pdf-output <output.pdf> --image-edition original
python scripts/verify_translation_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --translation-ledger <translation-ledger.json> --text-root <text-root>
python scripts/validate_epub_layout.py --book-root <book-root> --text-edition translated-pt-br
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --text-edition translated-pt-br
python scripts/export_reader_pair.py --book-root <book-root> --epub-output <translated-output.epub> --pdf-output <translated-output.pdf> --text-edition translated-pt-br --image-edition original
python scripts/verify_fluid_edition_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --translation-ledger <translation-ledger.json?> --fluid-style <fluid-style.json> --fluid-ledger <fluid-edition-ledger.json> --text-root <text-root>
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --text-edition fluid-pt-br --layout semantic --epub-layout <epub-layout.fluid.json>
python scripts/export_reader_pair.py --book-root <book-root> --epub-output <fluid-output.epub> --pdf-output <fluid-output.pdf> --text-edition fluid-pt-br --image-edition original
python scripts/publish_artifacts.py --book-root <book-root> --epub <fluid-output.epub> --pdf <fluid-output.pdf>
python scripts/validate_narrator_lineage.py --book-root <book-root> --input-file <locutor.txt>
python scripts/narrator_quality.py --book-root <book-root> --input-file <locutor.txt> --output <book-root>\assembly\metadata\narrator-review.json [--narrator-changes <changes.json>]
python scripts/validate_narrator_quality.py --book-root <book-root> --input-file <locutor.txt>
python scripts/narration_plan.py --book-root <book-root> --refresh-approved-metadata
python scripts/validate_narration_plan.py --book-root <book-root> --input-file <locutor.txt>
E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe scripts/render_chatterbox.py --book-root <book-root> --input-file <locutor.txt> --output-dir <audio-dir> --format mp3 --require-lineage --require-quality
python scripts/publish_artifacts.py --book-root <book-root> --audio <audio-dir>\audiobook.mp3 --epub <output.epub> --pdf <output.pdf>
```

Use `--mock` only for validation. Never publish mock audio.
