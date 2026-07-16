# Artifact Contract

All paths below are relative to one book output root.

```text
book/
|- source/
|  `- original.pdf | original.epub
|- assets/
|  `- images/
|     `- original/
|- metadata/
|  |- book-map.json
|  |- assets-manifest.json
|  |- text-ledger.json
|  |- epub-layout.json
|  |- translation-ledger.json (optional)
|  |- epub-manifest.json
|  |- epub-manifest.pt-br.json (optional)
|  |- narrator-changes.json
|  |- narrator-review.json
|  |- audio-render-journal.json
|  |- audio-chapters-manifest.json
|  |- audio-manifest.json
|  `- publication-manifest.json
|- pages/
|  |- physical/
|  `- logical/
|- restoration/
|  |- candidates/
|  `- approved/
|- text/
|  |- source/pages/
|  |- source/chapters/
|  |- translation/pt-BR/pages/
|  |- translation/pt-BR/chapters/
|  |- locutor/pages/
|  `- locutor/chapters/
|- audio/
|  |- segments/
|  `- chapters/
|     |- original/
|     |- final/
|     `- temp/
|- exports/
|  `- epub/
|- <book>-audiobook.mp3
|- <book>-fiel-classico.epub
`- <book>-pt-br-classico.epub (optional)
```

## `book-map.json`

Required top-level keys:

```json
{
  "schema_version": "1.0",
  "source": {},
  "analysis": {},
  "page_number_alignment": { "segments": [] },
  "toc_chapters": [],
  "chapters": [],
  "ranges": { "ignored": [], "narration_excluded": [] },
  "pages": [],
  "warnings": []
}
```

`source.path` is always the relative path to the immutable stored input, normally
`source/original.pdf` or `source/original.epub`. `source.original_path` records the
attachment path only as provenance; all later stages must read `source.path` so a
temporary Codex attachment cannot invalidate the book.

`pages` is the canonical coverage ledger. Each logical page or EPUB spine unit must appear once with:

- `logical_page`
- `source_page` or `source_locator`
- `side`
- `render_path` when visual rendering exists
- `printed_page` when known
- `blank`
- `status`
- `chapter_id`
- `evidence`

Use `page_number_alignment.segments` when page numbering has separate front-matter and body offsets. A segment contains `logical_start_page`, `logical_end_page`, `pdf_to_printed_page_offset`, and evidence.

## `assets-manifest.json`

`assets/images/original` contains byte-preserved source image assets extracted from the
PDF or EPUB. It is not a transcription input. Every asset has one immutable original
record and may have zero or more derived restoration records.

```json
{
  "schema_version": "1.0",
  "source_sha256": "",
  "assets": [
    {
      "id": "pdf-page-0001-image-01",
      "source": {
        "format": "pdf",
        "source_page": 1,
        "logical_pages": [1],
        "object_name": "Image13.jpg"
      },
      "original": {
        "path": "assets/images/original/pdf-page-0001-image-01.jpg",
        "sha256": "",
        "media_type": "image/jpeg",
        "width": 0,
        "height": 0
      },
      "classification": {
        "content": "unknown",
        "text_pixels": "unknown",
        "restoration_eligibility": "review_required",
        "evidence": []
      },
      "epub": {
        "role": "unresolved",
        "placement": "unresolved",
        "document_id": null,
        "alt_text": ""
      },
      "restoration": {
        "status": "not_requested",
        "approved": null
      }
    }
  ]
}
```

Allowed `classification.text_pixels` values are `none`, `printed`, `handwriting`,
`mixed`, and `unknown`. Only a reviewed, non-text asset may be automatically proposed
for image restoration. Printed text, handwriting, captions, signatures, and seals
remain source evidence and must not be reconstructed by a generated image.

`restoration_eligibility` is one of `prohibited`, `review_required`, `eligible`, or
`manual_exception`. A scanned page with printed or handwritten text may enter a
restored EPUB only as `manual_exception`, with an explicit exception reason and a
human approval record. It remains a non-canonical visual derivative.

An approved restoration is always a separate file under `restoration/approved`. Its
record must contain the original asset SHA-256, derivative SHA-256, tool, prompt,
reviewer, approval time, and the derivative `media_type`. The media type must match
the approved file suffix and readable image bytes. It never replaces the original
asset or a PDF render.

An extracted image remains `unresolved` until a reviewer supplies a non-unknown
classification, evidence, an explicit EPUB `document_id`, and an allowed placement of
`after_title` or `end`. The exporter never infers a figure position from a source page.
An EPUB-origin image declared as the source cover is the only exception: it may use
`role: "cover"` and `placement: "source_cover"` with source-package evidence.

## `text-ledger.json`

Each mapped page has exactly one ledger record:

```json
{
  "logical_page": 1,
  "status": "verified",
  "source_file": "source/pages/page-0001.txt",
  "source_sha256": "",
  "transcribed_by": "codex",
  "verified_by": "codex",
  "notes": ""
}
```

Allowed statuses are `verified`, `blank`, and `excluded`. `verified` requires an existing non-empty source file and matching SHA-256. Blank and excluded pages require explicit justification in the map or ledger.
`book_map_sha256` must equal the SHA-256 of the exact `book-map.json` being verified.

Before EPUB export, `chapter_outputs` binds each `front-*` or mapped chapter TXT to
the verified source pages it was assembled from:

```json
{
  "id": "chapter-01",
  "source_file": "source/chapters/chapter-01-title.txt",
  "source_sha256": "",
  "source_pages": [
    {
      "logical_page": 1,
      "source_sha256": ""
    }
  ],
  "verified_by": "codex"
}
```

Chapter records cover exactly their mapped verified pages. Front-matter records cover
exactly the remaining verified pages, and a fallback `book` output covers all verified
pages.

## `epub-layout.json`

The original-text EPUB layout is a presentation map, never a replacement for
`text/source`. It contains ordered `paragraph`, `dialogue`, `verse`, `heading`, and `note`
blocks. Each block references inclusive line ranges from verified source-page files.
Every non-empty verified page line must appear exactly once across the layout, in source
order. This allows chapter transitions inside a printed page without editing source text.

```json
{
  "schema_version": "1.0",
  "text_edition": "original",
  "book_map_sha256": "",
  "text_ledger_sha256": "",
  "documents": [
    {
      "id": "chapter-01",
      "blocks": [
        {
          "kind": "heading",
          "level": 1,
          "spans": [
            {
              "source_file": "text/source/pages/page-0001.txt",
              "source_sha256": "",
              "start_line": 1,
              "end_line": 2
            }
          ]
        }
      ]
    }
  ]
}
```

`heading` requires a level from 1 to 6. A `note` has a book-unique safe `id` and
book-unique source marker such as `2`, `*`, `†`, or `‡`; the exporter emits a semantic EPUB footnote
and links an attached body marker when one exists. All other block kinds omit `level`. A semantic
original EPUB manifest records this layout as `{mode, path, sha256}`. Legacy original
manifests may omit it and use the legacy renderer explicitly. Translated PT-BR EPUBs do
not reuse the original line map.

## `narrator-changes.json`

Keep narrator changes traceable:

```json
{
  "schema_version": "2.0",
  "source_book_sha256": "",
  "book_map_sha256": "",
  "base_edition": "source",
  "base_ledger_sha256": "",
  "mode": "faithful",
  "archaic_assessment": {
    "status": "not_applicable",
    "reviewed_by": "",
    "evidence": [
      {
        "logical_page": 1,
        "source_sha256": "",
        "source_span": "",
        "reason": ""
      }
    ]
  },
  "outputs": [
    {
      "id": "book",
      "kind": "full-book",
      "locutor_file": "locutor/book.txt",
      "locutor_sha256": "",
      "base_outputs": [
        {
          "id": "chapter-01",
          "base_file": "source/chapters/chapter-01-title.txt",
          "base_sha256": ""
        }
      ],
      "reviewed_by": "codex"
    }
  ],
  "changes": [
    {
      "output_id": "book",
      "kind": "punctuation",
      "logical_pages": [1],
      "base_output_id": "chapter-01",
      "base_span": "",
      "locutor_span": "",
      "reason": "Clarified an unambiguous spoken pause.",
      "reviewed_by": "codex"
    }
  ]
}
```

The narrator file is derived output. It must never overwrite or replace the source
file. `base_edition` is `source` unless the optional translation stage produced a
complete approved PT-BR ledger, in which case it is `translated-pt-br`. Every
`base_output` pins the exact source or translated chapter hash used by a locutor file.

Allowed modes are `faithful`, `archaic-modernized`, and `translated-pt-br`. A
`faithful` or `archaic-modernized` locutor derives from source. The latter requires
`archaic_assessment.status: "confirmed"` plus page-level source spans, reasons, and
review. Each evidence record binds one verified source page by SHA-256 and must quote a
span from that page. Each archaic change carries that page hash and must match one
assessment record by normalized base span, its single logical page, and SHA-256. It must
not be selected merely because a book is old. A non-Portuguese source
must use the translated mode after the translation ledger passes.

Each change is granular: record the exact base and locutor snippets, output and base
IDs, pages, reason, and reviewer. Number expansion, punctuation for speech,
page-furniture removal, approved figure descriptions, reviewed archaic modernization,
an approved `editorial_correction`, and `note_relocation` all require records.

## `narrator-review.json`

Every new narrator render uses `faithful-natural-v1`. The review is a separate
quality gate: it binds the selected locutor output and its current
`narrator-changes.json`, records the result of the semantic speech review, and prevents
unresolved quality findings from reaching a `--require-quality` render.

```json
{
  "schema_version": "1.0",
  "profile": "faithful-natural-v1",
  "status": "approved",
  "reviewed_by": "codex",
  "output_file": "locutor/book.txt",
  "output_sha256": "",
  "narrator_changes_sha256": "",
  "review_scope": {
    "categories": ["heading", "prose", "dialogue", "quotation", "verse", "note", "list"],
    "logical_pages": [1]
  },
  "findings": [],
  "pronunciation_review": {
    "status": "approved",
    "reviewed_by": "codex",
    "entries": []
  }
}
```

Each finding records its deterministic ID, kind, severity, locutor span, line and
column, reviewed locution category, logical pages, decision, reason, and reviewer.
`suggested_category`, when present in a draft, is non-authoritative and must not replace
the reviewer-selected `category`.
Remaining review-level findings may be preserved only with an explicit rationale. Roman
headings and labelled Roman numerals are blocking findings and must be converted to an
approved spoken form.
Pronunciation entries record the term class, selected spoken form or preservation, cited
pages, rationale, and reviewer. The review never substitutes for
`narrator-changes.json`: every actual text transformation remains a granular narrator
change.

## `translation-ledger.json`

Translation is optional and only for a whole source book written in another language.
A Portuguese book with intentional isolated English or other foreign words remains a
source-faithful Portuguese book and does not create this ledger.

`translation_decision.evidence` covers every verified source page with a page hash and
quoted source span. This is a reviewed whole-work language decision, not automatic
translation triggered by isolated foreign words.

```json
{
  "schema_version": "1.0",
  "book_map_sha256": "",
  "text_ledger_sha256": "",
  "source_language": "",
  "target_language": "pt-BR",
  "translation_decision": {
    "scope": "whole-book",
    "reason": "The complete source work is in English.",
    "reviewed_by": "codex",
    "evidence": [
      {
        "logical_page": 1,
        "source_sha256": "",
        "source_span": "",
        "reason": "Verified as part of the whole-work language decision."
      }
    ]
  },
  "edition": {
    "book": {
      "title": "Titulo em PT-BR",
      "subtitle": ""
    },
    "document_titles": [
      {
        "id": "chapter-01",
        "title": "Capitulo Um"
      }
    ]
  },
  "pages": [
    {
      "logical_page": 1,
      "status": "verified",
      "source_file": "source/pages/page-0001.txt",
      "source_sha256": "",
      "translation_file": "translation/pt-BR/pages/page-0001.txt",
      "translation_sha256": "",
      "translated_by": "codex",
      "reviewed_by": "codex",
      "notes": ""
    }
  ],
  "chapter_outputs": [
    {
      "id": "chapter-01",
      "source_file": "source/chapters/chapter-01-title.txt",
      "source_sha256": "",
      "translation_file": "translation/pt-BR/chapters/chapter-01-title.txt",
      "translation_sha256": "",
      "source_pages": [
        {
          "logical_page": 1,
          "source_sha256": ""
        }
      ],
      "translated_by": "codex",
      "reviewed_by": "codex"
    }
  ]
}
```

The translated EPUB is a separate semantic PT-BR edition. Its text and metadata may
be PT-BR, but source image pixels remain unchanged. Approved restored images are still
derivatives selected by export mode; they never become translation evidence.

## `epub-manifest.json`

The EPUB manifest is created only after `text-ledger.json` passes. It defines semantic
reading order and binds a chapter or front-matter document to verified source text.
Its non-cover documents must preserve the canonical front-matter and chapter order
derived from the validated source tree and `book-map.json`; the manifest cannot reorder
them.
The canonical EPUB always uses `text/source`; `text/locutor` is never an implicit EPUB
input. A translated EPUB uses `text/translation/pt-BR` only when
`metadata/translation-ledger.json` is complete and approved. Its manifest is
`metadata/epub-manifest.pt-br.json`, with `text_edition: "translated-pt-br"`,
translation hashes, PT-BR document titles, and each document's original
`source_file` plus its selected `translation_file`.

```json
{
  "schema_version": "1.0",
  "book_map_sha256": "",
  "text_ledger_sha256": "",
  "assets_manifest_sha256": "",
  "layout": {
    "mode": "semantic",
    "path": "metadata/epub-layout.json",
    "sha256": ""
  },
  "language": "pt-BR",
  "visual_profile": {
    "name": "antique-paper",
    "cover": {
      "mode": "editorial"
    }
  },
  "documents": [
    {
      "id": "chapter-01",
      "kind": "chapter",
      "title": "Chapter title",
      "source_file": "text/source/chapters/chapter-01-title.txt",
      "source_sha256": "",
      "asset_ids": []
    }
  ]
}
```

`asset_ids` may reference an original asset or an approved restoration only through
the export mode. Figures remain optional when their exact anchor is uncertain; do not
invent a visual placement or caption merely to fill the EPUB.

An EPUB-origin image explicitly declared as its source cover may create a
`source_cover` document when the source spine has no corresponding title-page XHTML.
That document has `source_file` and `source_sha256` set to `null`, references at least
one original cover asset, appears at most once, and is the first manifest document
(immediately after the generated editorial cover in the EPUB spine).

`visual_profile` is optional for legacy manifests. New manifests use `antique-paper`,
which packages the plugin-bundled IM FELL English Regular and Italic together with its
OFL license, applies black text on white reading pages and a white editorial cover, and
generates that cover deterministically from verified book metadata. It does not replace the original
cover/title-page asset, which remains in the source reading order.

The original-text export writes `exports/epub/<book>-fiel-classico.epub` for original
images and may write `exports/epub/<book>-restaurada-classico.epub` only when every
selected derivative is approved. The translated-text export writes
`exports/epub/<book>-pt-br-classico.epub`, or
`exports/epub/<book>-pt-br-restaurada-classico.epub` with approved restored images.
Legacy manifests without `visual_profile` retain the earlier filenames.

## Audio Manifest

For each segment, record source and narrator hashes, voice, output path, duration, sample rate, and generation time. A later render may reuse only a segment with the same narrator hash and synthesis settings. Publication cadence is a delivery transformation, not a TTS setting, so it must not invalidate verified segment reuse.

`audio-render-journal.json` is an atomic, incomplete-or-complete companion record used
while a Chatterbox render is in progress. It records each finished WAV with its narrator
hash, WAV hash, duration, seed, and render identity. A resumed render reuses only records
whose text, model, renderer, voice, generation settings, and WAV checksum still match.
Untracked WAVs are never adopted automatically.

`metadata/audio-manifest.json` is canonical even though the wave segments and final
audio stay under `audio/`. A book render additionally binds
`metadata/narration-plan.json`: its ordered segment IDs/text hashes and per-boundary
pause durations are the assembly identity. A Chatterbox PT-BR render additionally records
model hashes, CUDA/CPU device, reference-voice SHA-256, the resolved profile, renderer
hash, installed Chatterbox package version, line-delimited narrator policy, and the
resolved variable boundary pauses.
`metadata/audio-chapters-manifest.json` is the review-oriented companion artifact. It
records each contiguous narration-plan chapter, its verified segment identities, the
immutable 1.0x master under
`audio/<profile>/chapters/original/<chapter-id>.wav`, and the direct canonical delivery
outputs under `audio/<profile>/chapters/final/<chapter-id>.wav` and `.mp3`.
Temporary listening variants belong under `audio/<profile>/chapters/temp/`. Its
`publication` record names the pitch-preserving processor and selected tempo. Chapter
outputs omit the final inter-chapter pause; the full-book mount applies that pause when
joining chapter boundaries.
`metadata/audio-manifest.json` stores the immutable 1.0x master as
`raw/audiobook.master.wav` and retains `raw/audiobook.wav` as the canonical delivery
WAV for compatibility with existing consumers. It records both hashes, the selected
tempo, and final publishable audio. A new render uses the default `1.20x` tempo unless
`--publication-tempo` overrides it. `--remount` rebuilds delivery artifacts from a
complete verified journal without TTS synthesis.
An official profile additionally records the hash-pinned calibration selection that chose
it. Each Chatterbox segment records its locutor line, character count, and any punctuation
or acronym review warnings produced by that policy.
Every book-root render records `narrator_lineage`: the narrator-change schema and hash,
mode, selected output ID, base edition, and base-ledger hash. Standalone renders are
not publishable book artifacts because `publish_artifacts.py` requires this lineage.

## Publication Manifest

`publish_artifacts.py` copies final artifacts into the book root only after they exist
under their provenance directories. `metadata/publication-manifest.json` records the
root-relative destination and SHA-256 alongside its source artifact. The audio manifest
and EPUB sidecar receive the same publication record.
