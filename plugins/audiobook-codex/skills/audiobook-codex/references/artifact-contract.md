# Artifact Contract

All paths below are relative to one book output root.

```text
book/
|- source/
|  `- original.pdf | original.epub
|- metadata/
|  |- book-map.json
|  |- text-ledger.json
|  |- narrator-changes.json
|  `- audio-manifest.json
|- pages/
|  |- physical/
|  `- logical/
|- text/
|  |- source/pages/
|  |- source/chapters/
|  |- locutor/pages/
|  `- locutor/chapters/
`- audio/
   |- segments/
   `- chapters/
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

## Audio Manifest

For each segment, record source and narrator hashes, voice, speed, output path, duration, sample rate, and generation time. A later render may reuse only a segment with the same narrator hash and audio settings.
