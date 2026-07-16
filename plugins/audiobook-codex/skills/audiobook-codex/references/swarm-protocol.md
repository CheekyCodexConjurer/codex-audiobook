# Swarm Protocol

Use a bounded swarm only when it reduces elapsed time without weakening fidelity.

## Roles

- Main agent: owns `book-map.json`, asset, layout and EPUB manifests, final TXT files,
  narrator reviews, exports, merges, and quality gates.
- Structure scout: read-only survey of layout, TOC, offsets, chapters, visual anomalies, source-image classification, and EPUB block semantics.
- Transcriber: writes only assigned source-page files and ledger drafts.
- Translator: writes only assigned whole-book translation units under `text/translation/pt-BR` and ledger drafts after the source ledger is complete.
- Verifier: independently compares assigned pages against the source and approves or
  rejects ledger entries, narrator-review decisions, and EPUB source/asset links.

## Boundaries

- Give every transcriber an exclusive logical-page range. Supply one boundary page as context but prohibit output for that context page.
- Do not let workers edit `book-map.json`, chapter TXT, or audio manifests.
- Do not let a worker overwrite `source/`, `pages/`, or `assets/images/original/`.
- Do not let a worker modify `text/source` to encode presentation; record paragraph, dialogue, verse, and heading positions only in `metadata/epub-layout.json`.
- Do not let a translator change `text/source` or translate isolated foreign words inside a Portuguese source.
- Keep restoration candidates outside the canonical asset tree. Only the main agent may promote a reviewed candidate to `restoration/approved/` and update its provenance record.
- Merge and validate after every batch.
- Use at most one Computer Use worker at a time.
- Use at most one native image restoration worker at a time. It may inspect and create only an assigned candidate file; it cannot approve or publish it.
- Keep Chatterbox rendering serial unless the runtime is explicitly proven safe for concurrent inference.

## Gates

1. Map valid before transcription.
2. Every source page verified before narrator text.
3. Optional translation starts only for a whole foreign-language book with a complete source ledger.
4. Every original-text EPUB layout covers each non-empty verified source-page line exactly once and preserves its document order.
5. Every EPUB document must reference verified source text, or approved translation text for a separate translated edition, and an asset with valid lineage.
6. Every approved restored asset must retain its original SHA-256, prompt, reviewer, and approval timestamp.
7. Every narrator segment traceable and every `faithful-natural-v1` review approved before audio.
8. Every published audio file present and duration-checked before completion.
