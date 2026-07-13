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
|  |- epub-manifest.json
|  |- narrator-changes.json
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
|  |- locutor/pages/
|  `- locutor/chapters/
|- audio/
|  |- segments/
|  `- chapters/
|- exports/
|  `- epub/
|- <book>-audiobook.m4a
`- <book>-fiel-classico.epub
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

## `narrator-changes.json`

Keep narrator changes traceable:

```json
{
  "schema_version": "1.0",
  "source_book_sha256": "",
  "changes": [
    {
      "logical_pages": [1],
      "source_sha256": "",
      "locutor_sha256": "",
      "kind": "punctuation",
      "reason": "Clarified an unambiguous spoken pause."
    }
  ]
}
```

The narrator file is derived output. It must never overwrite or replace the source file.

## `epub-manifest.json`

The EPUB manifest is created only after `text-ledger.json` passes. It defines semantic
reading order and binds a chapter or front-matter document to verified source text.
The canonical EPUB always uses `text/source`; `text/locutor` is never an implicit EPUB
input.

```json
{
  "schema_version": "1.0",
  "book_map_sha256": "",
  "text_ledger_sha256": "",
  "assets_manifest_sha256": "",
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
one original cover asset, and is placed immediately after the generated editorial cover.

`visual_profile` is optional for legacy manifests. New manifests use `antique-paper`,
which packages the plugin-bundled IM FELL English Regular and Italic together with its
OFL license, applies the fixed paper palette, and generates a deterministic editorial
cover from verified book metadata. It does not replace the original cover/title-page
asset, which remains in the source reading order.

The export writes `exports/epub/<book>-fiel-classico.epub` for original images and may
write `exports/epub/<book>-restaurada-classico.epub` only when every selected derivative
is approved. Legacy manifests without `visual_profile` retain the earlier filenames.

## Audio Manifest

For each segment, record source and narrator hashes, voice, speed, output path, duration, sample rate, and generation time. A later render may reuse only a segment with the same narrator hash and audio settings.

`metadata/audio-manifest.json` is canonical even though the wave segments and final
audio stay under `audio/`. A Chatterbox PT-BR render additionally records model hashes,
CUDA/CPU device, and reference-voice SHA-256.

## Publication Manifest

`publish_artifacts.py` copies final artifacts into the book root only after they exist
under their provenance directories. `metadata/publication-manifest.json` records the
root-relative destination and SHA-256 alongside its source artifact. The audio manifest
and EPUB sidecar receive the same publication record.
