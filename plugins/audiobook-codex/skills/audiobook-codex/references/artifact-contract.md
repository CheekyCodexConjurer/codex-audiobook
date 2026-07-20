# Artifact Contract

Default library root: `E:\Pessoal\Library`.

Each public book root is named `Nome do Livro - Ano - Autor`. `--book-root` always
means this public root. The public root contains `assembly/`, the canonical EPUB/PDF
pair, any separately named published fluid EPUB/PDF pair, and the final MP3.
Manifests, sidecars, and ledger paths are relative to `assembly/`, not to the public
root.

The `assembly/` directory contains exactly these top-level directories:
`assets`, `audio`, `exports`, `metadata`, `pages`, `source`, and `text`.

```text
Nome do Livro - Ano - Autor/
|- assembly/
|  |- assets/
|  |  |- images/
|  |  |  `- original/
|  |  `- restoration/
|  |     |- candidates/
|  |     `- approved/
|  |- audio/
|  |  |- segments/
|  |  `- chapters/
|  |     |- original/
|  |     |- final/
|  |     `- temp/
|  |- exports/
|  |  |- epub/
|  |  |  |- <book>-fiel.epub
|  |  |  `- <book>-fiel.epub.json
|  |  `- pdf/
|  |     |- <book>-fiel.pdf
|  |     `- <book>-fiel.pdf.json
|  |- metadata/
|  |  |- work/
|  |  |  |- claims/
|  |  |  |- text-ledger.d/
|  |  |  |- translation-ledger.d/
|  |  |  |- fluid-ledger.d/
|  |  |  |- narrator-changes.d/
|  |  |  |- narrator-review.d/
|  |  |  |- glossary-proposals.d/
|  |  |  `- ambiguity-records.d/
|  |  |- book-map.json
|  |  |- assets-manifest.json
|  |  |- text-ledger.json
|  |  |- epub-layout.json
|  |  |- translation-ledger.json (optional)
|  |  |- epub-layout.pt-br.json (optional)
|  |  |- fluid-style.json (optional)
|  |  |- fluid-edition-ledger.json (optional)
|  |  |- epub-layout.fluid.json (optional)
|  |  |- epub-manifest.json
|  |  |- epub-manifest.pt-br.json (optional)
|  |  |- epub-manifest.fluid.json (optional)
|  |  |- publication-selection.json
|  |  |- narrator-changes.json
|  |  |- narrator-review.json
|  |  |- audio-render-journal.json
|  |  |- audio-chapters-manifest.json
|  |  |- audio-manifest.json
|  |  `- publication-manifest.json
|  |- pages/
|  |  |- physical/
|  |  `- logical/
|  |- source/
|  |  `- original.pdf | original.epub
|  `- text/
|     |- source/pages/
|     |- source/chapters/
|     |- translation/pt-BR/pages/
|     |- translation/pt-BR/chapters/
|     |- fluid/pt-BR/chapters/
|     |- fluid/pt-BR/book.txt
|     |- locutor/pages/
|     `- locutor/chapters/
|- Nome do Livro - Ano - Autor.mp3
|- Nome do Livro - Ano - Autor.epub (complete or `both`)
|- Nome do Livro - Ano - Autor.pdf (complete or `both`)
|- <titulo>.epub (selected `fluid`)
|- <titulo>.pdf (selected `fluid`)
|- <titulo>-fluida.epub (optional `both`)
`- <titulo>-fluida.pdf (optional `both`)
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

`source.path` is always the assembly-relative path to the immutable stored input, normally
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

An approved restoration is always a separate file under `assets/restoration/approved`. Its
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

## Swarm Work Artifacts

`metadata/work/` contains non-canonical claim, shard, and queue evidence. It is never a
publication input and never replaces a canonical ledger or manifest. Workers may write
only their assigned work paths; the coordinator is the sole promoter to canonical
paths.

A schema `1.0` claim map binds one immutable unit of work:

```json
{
  "schema_version": "1.0",
  "claims": [
    {
      "claim_id": "transcribe:chapter-01:v1",
      "stage": "TRANSCRIBE",
      "status": "ready_for_verification",
      "claim_order": 1,
      "priority": 50,
      "depends_on": [],
      "producer": "audiobook-transcriber",
      "verifier": "audiobook-verifier",
      "read_set": [
        {
          "path": "metadata/book-map.json",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
        }
      ],
      "write_set": [
        "text/source/pages/page-0001.txt",
        "text/source/chapters/chapter-01.txt",
        "metadata/work/text-ledger.d/chapter-01.json"
      ],
      "canonical_targets": [
        "text/source/pages/page-0001.txt",
        "text/source/chapters/chapter-01.txt"
      ],
      "no_touch": [
        "metadata/book-map.json",
        "metadata/text-ledger.json"
      ],
      "scope": {
        "unit_kind": "chapter",
        "unit_ids": ["chapter-01"],
        "context_unit_ids": ["chapter-02"]
      },
      "context": {},
      "validation": {
        "requires_verification": true,
        "commands": [
          "python plugins/audiobook-codex/scripts/verify_text_ledger.py --mode claim --book-map metadata/book-map.json --text-root text --claim-map metadata/work/claims/transcribe-chapter-01.json --claim-id transcribe:chapter-01:v1 --shard metadata/work/text-ledger.d/chapter-01.json"
        ]
      },
      "lease": {
        "holder": "",
        "issued_at": "",
        "expires_at": ""
      }
    }
  ]
}
```

All paths are assembly-relative, normalized, non-absolute, and may not contain `..`.
The claim's `read_set` freezes every material input by SHA-256. Claim `stage`
uses the closed set `MAP`, `TRANSCRIBE`, `TRANSLATE`, `FLUID`, or `RENDER`; typos,
`NARRATE`, and other ad-hoc stage values are invalid. Known dependency-bearing
writable stages (`TRANSCRIBE`, `TRANSLATE`, and `FLUID`) must include all required
read-set dependency entries even before a book root is available, and a promotion
with shards must recheck those hashes against the current book root. `write_set`
and `canonical_targets` must be non-empty for writable stages and may not overlap
another active claim. `MAP` claims are read-only and must not declare write
targets. Context units are read-only and must not appear as owned outputs. During
claim-scoped validation, ordinary output and shard paths must exactly match an
authorized target; descendant paths are allowed only for explicit shard-directory
targets such as `metadata/work/text-ledger.d`, `metadata/work/translation-ledger.d`,
or `metadata/work/fluid-ledger.d`. A target below the checked path never authorizes
writing the checked path's ancestor.

Claims move monotonically through:

```text
planned → leased → in_progress → ready_for_verification → verified → merged
```

`blocked` and `abandoned` are explicit terminal side states. A retry creates a new
claim or lease attempt without overwriting prior evidence. If a read-set hash,
scope, or canonical target changes before promotion, the existing shard is rejected
or superseded by a new immutable claim.

Ledger shards bind `claim_id`, the claim SHA-256, deterministic `order`, producer,
verifier, and only the records owned by that claim. The claim map's immutable
`claim_order` is the sole canonical merge order; `shard.order` must equal it but
cannot define ordering by itself. Accepted shards merge in claim-map order. Merge
rejects duplicate claims, missing claim-order positions, inverted shard order,
incompatible claim stages for the shard kind (`text` requires `TRANSCRIBE`,
`translation` requires `TRANSLATE`, and `fluid` requires `FLUID`), overlapping
pages, duplicate output IDs or block positions, stale claim hashes, and legacy
full-claim hashes. Canonical writes use a temporary sibling plus atomic replace.

## `epub-layout.json`

The original-text EPUB layout is a presentation map, never a replacement for
`text/source`. It contains ordered `paragraph`, `quotation`, `dialogue`, `verse`, `heading`,
and `note` blocks. Each block references inclusive line ranges from verified source-page
files. A `quotation` is a semantically separate direct quotation or displayed citation and
must render with bilateral indentation in both EPUB and PDF; do not use it for ordinary
dialogue turns.
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
          "base_sha256": "",
          "locutor_file": "locutor/chapters/chapter-01-title.txt"
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
    },
    {
      "output_id": "book",
      "kind": "footnote_exclusion",
      "logical_pages": [1],
      "base_output_id": "chapter-01",
      "note_id": "note-1",
      "note_part": "content",
      "base_span": "1 Complete footnote text.",
      "locutor_span": "",
      "reason": "Semantic footnotes remain in the textual edition but are not narrated.",
      "reviewed_by": "codex"
    },
    {
      "output_id": "book",
      "kind": "supplementary_matter_exclusion",
      "matter_kind": "references",
      "logical_pages": [2],
      "base_output_id": "chapter-01",
      "base_span": "References AUTHOR. Work. 2020.",
      "locutor_span": "",
      "reason": "Trailing references remain in the textual edition but are not narrated.",
      "reviewed_by": "codex"
    }
  ]
}
```

The narrator file is derived output. It must never overwrite or replace the source,
translation, or fluid reading file. `base_edition` is `source`, `translated-pt-br`, or
`fluid-pt-br` according to the explicitly selected approved reading edition. Every
`base_output` pins the exact source, translated, or fluid chapter hash used by a
locutor file.
Each base output may declare an explicit `locutor_file` under `locutor/chapters/`.
Use it whenever the reviewed locutor chapter does not share the base chapter filename;
legacy records without it retain the same-basename fallback.

Allowed modes are `faithful`, `archaic-modernized`, `translated-pt-br`, and
`fluid-pt-br`. A
`faithful` or `archaic-modernized` locutor derives from source. The latter requires
`archaic_assessment.status: "confirmed"` plus page-level source spans, reasons, and
review. Each evidence record binds one verified source page by SHA-256 and must quote a
span from that page. Each archaic change carries that page hash and must match one
assessment record by normalized base span, its single logical page, and SHA-256. It must
not be selected merely because a book is old. A non-Portuguese source
must use the translated mode after the translation ledger passes. A fluid locutor
derives only from an independently approved `fluid-edition-ledger.json`.

Each change is granular: record the exact base and locutor snippets, output and base
IDs, pages, reason, and reviewer. Number expansion, punctuation for speech,
page-furniture removal, approved figure descriptions, reviewed archaic modernization,
an approved `editorial_correction`, every `footnote_exclusion`, and every partial-output
`supplementary_matter_exclusion` all require records.
`note_relocation` remains valid only for legacy metadata.

Semantic footnotes remain complete in the source, translation or fluid reading edition,
and EPUB/PDF. They are omitted from `text/locutor`. Record the note body as
`kind: "footnote_exclusion"`, its semantic layout `note_id`,
`note_part: "content"`, the exact unique `base_span`, and an empty `locutor_span`.
Remove an attached reference marker through a separate record for the same `note_id`
with `note_part: "marker"`; its `locutor_span` may contain the cleaned surrounding
phrase when the marker cannot be represented as a unique standalone span.

A full-book narrator output normally declares every validated base output. It may omit
one complete base output only when every page of that output is covered by
`book-map.json` `ranges.narration_excluded`. The omission requires exactly one
`mapped_exclusion` change with the omitted base-output ID, all of its logical pages, a
unique identifying `base_span`, an explicitly empty `locutor_span`, the map-backed
reason, and a reviewer. A partial chapter cannot be omitted this way.

Non-content supplementary back matter remains complete in source, translation, fluid,
EPUB, and PDF artifacts but is omitted from audio. A complete mapped output uses
`mapped_exclusion`. If references/bibliography, a glossary, an index, a
further-reading/source list, a colophon, or contiguous mixed back matter begins inside
the final page of an otherwise narrated base output, record one
`supplementary_matter_exclusion`. It requires an exact unique `base_span` that reaches
the normalized end of that base output, an explicitly empty `locutor_span`, the
affected logical pages, a reviewed reason, and `matter_kind` set to `bibliography`,
`references`, `glossary`, `index`, `further_reading`, `source_list`, `colophon`, or
`mixed_back_matter`. It cannot remove an interior span or silently classify a
potentially substantive appendix or authorial section as apparatus.

An isolated Arabic printed folio that appears as its own base block may be omitted with
`kind: "page_furniture_exclusion"`. Its exact `base_span` must be the numeric folio,
its `locutor_span` must be the empty string, and the record must name the logical page
that contains it. It cannot remove a numeral embedded in prose, a heading, a list, a
note, or a citation.

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

Schema `1.1` requires `translation_quality`. The selected profile is
`faithful-contextual-ptbr-v1`; logical pages remain lineage units while translators use
the complete chapter and neighboring-scene context. The book brief and glossary are
shared across all translation batches. Material ambiguities record their source span,
question, status, resolution, reviewer, and any research evidence. Non-book research
records its access date. `needs-review` and `unresolved` entries are valid draft states
but fail the final translation gate.

```json
{
  "schema_version": "1.1",
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
  "translation_quality": {
    "profile": "faithful-contextual-ptbr-v1",
    "context_policy": "whole-chapter-with-neighbors-v1",
    "research_policy": "context-first-evidence-recorded-v1",
    "brief": {
      "genre": "Romance",
      "period": "Seculo XIX",
      "setting": "Londres vitoriana",
      "narrator_voice": "Terceira pessoa ironica e observadora",
      "register": "Literario formal sem arcaizacao artificial",
      "style_goals": "Preservar ironia, contraste social e ritmo dos dialogos",
      "names_policy": "Preservar nomes proprios; traduzir titulos por decisao registrada",
      "foreign_fragments_policy": "Preservar fragmentos deliberadamente estrangeiros",
      "reviewed_by": "codex"
    },
    "glossary": [
      {
        "source_term": "ward",
        "target_term": "tutelada",
        "reason": "O contexto juridico e familiar exige o termo especifico.",
        "status": "approved",
        "reviewed_by": "codex"
      }
    ],
    "ambiguities": [
      {
        "id": "ambiguity-0001",
        "source_pages": [1],
        "source_span": "source expression",
        "category": "archaic",
        "question": "Qual sentido historico se aplica neste contexto?",
        "status": "resolved",
        "resolution": "Use the documented period-specific sense.",
        "resolved_by": "codex",
        "research": [
          {
            "source_type": "dictionary",
            "reference": "Historical dictionary entry for the source expression",
            "accessed_on": "2026-01-15",
            "finding": "The period-specific sense matches the surrounding scene."
          }
        ]
      }
    ],
    "review": {
      "semantic_fidelity": "approved",
      "literary_naturalness": "approved",
      "whole_book_consistency": "approved",
      "independent": true,
      "reviewed_by": "codex"
    }
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

Glossary entries are optional when the work has no recurring non-obvious term, but the
array is always present. Ambiguity entries are created only for material alternatives;
do not manufacture them for routine wording. Allowed research source types are
`book-context`, `dictionary`, `primary`, `official`, `scholarly`, and `other`. Every
non-`book-context` record requires `accessed_on` in `YYYY-MM-DD` form. A publishable
ledger requires all glossary entries and the semantic-fidelity, literary-naturalness,
and whole-book-consistency gates to be approved by an independent reviewer.

The translated EPUB is a separate semantic PT-BR edition. Its text and metadata may
be PT-BR, but source image pixels remain unchanged. Approved restored images are still
derivatives selected by export mode; they never become translation evidence.
The canonical translated reader layout is `metadata/epub-layout.pt-br.json`. It uses
`text_edition: "translated-pt-br"`, binds both `text_ledger_sha256` and
`translation_ledger_sha256`, and each block references one approved
`text/translation/pt-BR/chapters/` file with its translation hash and one-based
`block_index`. It must cover every approved translated chapter block exactly once in
validated document order. `join_with_previous` is reserved for fluid layouts and is
invalid in the translated layout.

## `fluid-style.json` and `fluid-edition-ledger.json`

`fluid-faithful-ptbr-v1` is an optional separate PT-BR reading edition. New style and
ledger files use schema `1.2`; schemas `1.0` and `1.1` remain readable only for already
approved legacy editions. Its base is either a Portuguese `source` ledger or a complete
approved `translated-pt-br` ledger.
The style file freezes one whole-book profile, register, tone, cadence, terminology
policy, title policy, and reviewed glossary. Required rules preserve meaning, forbid
additions and unsupported omissions, preserve examples, arguments, and authorial
stance, and allow only clarity, fluency, redundancy reduction, comprehensive archaic
modernization, and the explicit editorial exclusions below. Schemas `1.1` and `1.2`
require modernization of all genuinely archaic language, historical quotations,
orthography, and diacritics. Quotation status cannot authorize blanket literal
preservation of obsolete surface forms. Schema `1.2` additionally requires removal of
parenthetical bibliographic references, immediate duplicate-translation paragraphs,
and their translation labels.

The fluid ledger binds the book map, source ledger, selected base ledger, style hash,
ordered chapter outputs, and `text/fluid/pt-BR/book.txt`. In schema `1.2`, every base
block remains covered exactly once and in order. An `included` block records its
sequential `fluid_position` and actual fluid hash; an `excluded` block records null
fluid position/hash and `citation_reference_exclusion`,
`duplicate_translation_exclusion`, or `translation_label_exclusion`. A complete block
may use `citation_reference_exclusion` only when it consists solely of citation
apparatus. Included records must cover every actual fluid block exactly once and in
order. Inline bibliographic apparatus removed from semantic prose remains an included
block and is also recorded as `citation_reference_exclusion`. Chapter outputs record separate
`base_block_count` and `fluid_block_count` values. Legacy schemas `1.0` and `1.1`
retain equal base/fluid counts, one-to-one block hashes, and `block_count`.

`edited_by` identifies the editor; the top-level and review `reviewed_by` identities
must match each other and differ from the editor. Approval requires semantic fidelity,
no additions, no unsupported omissions, fluency, whole-book consistency, and an
independent reviewer. Here `review.no_omissions: "approved"` means that no content was
omitted outside the explicit schema `1.2` editorial exclusions. Schemas `1.1` and
`1.2` additionally require `review.archaic_modernization: "approved"`; schema `1.2`
also requires `review.editorial_exclusions: "approved"`.

The optional semantic layout is `metadata/epub-layout.fluid.json`. Each block references
one verified `text/fluid/pt-BR/chapters/` file, its current hash, and a one-based
`block_index`; the layout must cover every fluid chapter block exactly once in document
order. A later fluid `paragraph` or `quotation` block may set `join_with_previous: true`
only when reviewed source evidence proves that the base boundary is a page-break
continuation inside that same semantic block. Both blocks remain independently hashed and
covered, while the EPUB/PDF presentation renders them as one paragraph or quotation. The
raw layout may contain only contiguous semantic `note` blocks between those fragments; the
presentation completes the block before rendering those notes and preserves their relative
order. Heading, dialogue, verse, and every other block kind remain hard join barriers. The
manifest is
`metadata/epub-manifest.fluid.json`, uses
`text_edition: "fluid-pt-br"`, records the style and fluid-ledger hashes, and preserves
the full source and optional translation lineage.

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
translation hashes, PT-BR document titles, the canonical
`metadata/epub-layout.pt-br.json` descriptor, and each document's original
`source_file` plus its selected `translation_file`.
A fluid EPUB/PDF pair uses `text/fluid/pt-BR` only after its style and fluid ledger pass,
records `fluid_file` plus the selected base lineage, and never replaces the faithful
original or translated export.

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

`asset_ids` may reference an original asset or an approved restoration under `assets/restoration/approved/` only through
the export mode. Figures remain optional when their exact anchor is uncertain; do not
invent a visual placement or caption merely to fill the EPUB.

An EPUB-origin image explicitly declared as its source cover may create a
`source_cover` document when the source spine has no corresponding title-page XHTML.
That document has `source_file` and `source_sha256` set to `null`, references at least
one original cover asset, appears at most once, and is the first manifest document
in the source reading order.

New EPUB/PDF reader editions use only the generated ABNT-style title page: title,
subtitle when present, author, place, and year. They do not add a separate editorial
cover image. `visual_profile: antique-paper` is accepted only to read and validate
historical manifests; do not select it for new books or regenerated editions.

The original-text export writes `exports/epub/<book>-fiel.epub` for original images
and may write `exports/epub/<book>-restaurada.epub` only when every selected
derivative is approved. The translated-text export writes
`exports/epub/<book>-pt-br.epub`, or
`exports/epub/<book>-pt-br-restaurada.epub` with approved restored images. Historical
manifests that retain `visual_profile` keep their `-classico` filenames.

## PDF Export Sidecar

The paired PDF export is a non-facsimile reader edition generated from the validated
EPUB manifest, layout, text edition, and selected image edition. It must not be built
from source page screenshots or treated as transcription evidence.

PDF table-of-contents and outline labels use the canonical manifest document titles,
even when the visible source heading preserves a chapter number, kicker, or alternate
capitalization.

`export_pdf.py` writes PDFs physically under `assembly/exports/pdf/` with the same edition
labels as the EPUB export. Persisted assembly-relative paths are, for example,
`exports/pdf/<book>-fiel.pdf` or
`exports/pdf/<book>-pt-br.pdf`. It also writes a required sidecar beside the PDF
using `.pdf.json`. The sidecar records the PDF path and hash, page count, text and
image editions, language, source manifest hashes, ReportLab renderer identity, and used
assets. Run it with the Codex bundled Python so ReportLab is available, then validate
the output with `validate_pdf_export.py`.

## Audio Manifest

For each segment, record source and narrator hashes, voice, output path, duration, sample rate, and generation time. A later render may reuse only a segment with the same narrator hash and synthesis settings. Publication cadence is a delivery transformation, not a TTS setting, so it must not invalidate verified segment reuse.

`audio-render-journal.json` is an atomic, incomplete-or-complete companion record used
while a Chatterbox render is in progress. It records each finished WAV with its narrator
hash, WAV hash, duration, seed, and render identity. A resumed render reuses only records
whose text, model, renderer, voice, generation settings, and WAV checksum still match.
Untracked WAVs are never adopted automatically.

`metadata/audio-manifest.json` is canonical even though the wave segments and final
audio are physically stored under `assembly/audio/`. Persisted paths remain relative to
`assembly/`. A book render additionally binds
`metadata/narration-plan.json`: its ordered segment IDs/text hashes and per-boundary
pause durations are the assembly identity. A Chatterbox PT-BR render additionally records
model hashes, CUDA/CPU device, reference-voice SHA-256, the resolved profile, renderer
hash, installed Chatterbox package version, line-delimited narrator policy, and the
resolved seed strategy and variable boundary pauses. A profile-specific seed strategy
is part of the segment render identity; changing it invalidates reuse instead of silently
adopting WAVs generated under another strategy.
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
`audio/raw/audiobook.master.wav` and retains `audio/raw/audiobook.wav` as the canonical delivery
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

## `publication-selection.json`

`metadata/publication-selection.json` is the per-book internal Phase 2 flag:

```json
{
  "schema_version": "1.0",
  "target": "complete",
  "updated_by": "",
  "reason": ""
}
```

Preflight creates it with `target: "complete"`. Allowed targets are:

- `complete`: generate and publish only the approved complete reading edition and its
  matching audio;
- `fluid`: generate and publish only the approved `fluid-pt-br` EPUB/PDF pair and
  matching audio;
- `both`: run both tracks serially, keeping their EPUB/PDF and audio provenance
  distinct.

Change it only after an explicit user request using
`scripts/publication_selection.py`. `export_reader_pair.py`, Chatterbox rendering, and
publication reject an EPUB/PDF or narrator base outside the selected target. This
selection never changes `text/source`, `text/translation/pt-BR`, or
`text/fluid/pt-BR`; it selects which already approved edition may become public.
Legacy books that predate this file retain compatibility as `both` until an explicit
selection is recorded.

`publish_artifacts.py` copies final artifacts into the public book root only after they
exist under their assembly provenance directories. Use `--pdf` alongside `--epub` when
publishing the paired reader edition. With `target: both`, complete editions publish as
`Nome do Livro - Ano - Autor.epub|pdf` and the `fluid-pt-br` pair retains its distinct
`-fluida` export filenames in `artifacts.epub_editions` and
`artifacts.pdf_editions`. With `target: fluid`, the fluid pair is the selected public
edition: it uses the unsuffixed book-title export filename, may replace an earlier
fluid public filename, and does not require a complete pair. The fluid
`edition.book.title` must stay the selected base-edition title; use only
`edition.book.subtitle` for a reading label such as `Versão de audiolivro`.
`metadata/publication-manifest.json` records each root-relative destination and SHA-256
alongside its source artifact. The audio manifest and EPUB/PDF sidecars receive the
same publication record.

Every EPUB/PDF publication record includes `reader_pair_identity`, which binds the
text edition, image edition, language, book/text/assets lineage hashes, and any
translation, revision, fluid, profile, or layout lineage present in the export sidecar.
Newly supplied EPUB/PDF sidecars must contain the complete common lineage and match
the current canonical metadata; edition-specific ledger/style hashes are mandatory
when that edition uses them. The canonical manifest must itself bind the current
book-map, text ledger, assets manifest, edition-specific ledgers, language, and exact
layout presence/content before publication may trust it. PT-BR editions require the
canonical PT-BR language plus their edition-specific semantic layout path; an original
legacy edition may omit layout, but any declared original layout must also be canonical.
Export and publication share
one exclusive book transaction lock and recheck inputs, destinations, and the
publication manifest before multi-file promotion.

Before the first replacement, each multi-file promotion persists a recoverable journal
under `metadata/work`. A later process holding the same lock must recover an incomplete
promotion to the previous complete state, or finish cleanup of an already promoted
transaction, before starting new work. Cache hits require the complete deterministic
sidecar contract. A readable sidecar with matching artifact identity and input
fingerprint may have stale deterministic contract fields repaired atomically without
rewriting the EPUB or PDF. A missing, unreadable, or identity-incomplete sidecar forces
a fresh export.
A new-layout book should publish both reader formats together. When one side is updated
and a counterpart record exists, its `reader_pair_identity` must match; conflicting
counterparts block one-sided publication. Legacy books without a counterpart record or
complete pair identity retain one-sided compatibility, but that compatibility cannot
justify a mismatched replacement after both sides are tracked.
