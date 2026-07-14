# Swarm Protocol

Use a bounded swarm only when it reduces elapsed time without weakening fidelity.

## Roles

- Main agent: owns `book-map.json`, asset and EPUB manifests, final TXT files, exports, merges, and quality gates.
- Structure scout: read-only survey of layout, TOC, offsets, chapters, visual anomalies, and source-image classification.
- Transcriber: writes only assigned source-page files and ledger drafts.
- Verifier: independently compares assigned pages against the source and approves or rejects ledger entries and EPUB source/asset links.

## Boundaries

- Give every transcriber an exclusive logical-page range. Supply one boundary page as context but prohibit output for that context page.
- Do not let workers edit `book-map.json`, chapter TXT, or audio manifests.
- Do not let a worker overwrite `source/`, `pages/`, or `assets/images/original/`.
- Keep restoration candidates outside the canonical asset tree. Only the main agent may promote a reviewed candidate to `restoration/approved/` and update its provenance record.
- Merge and validate after every batch.
- Use at most one Computer Use worker at a time.
- Use at most one native image restoration worker at a time. It may inspect and create only an assigned candidate file; it cannot approve or publish it.
- Keep Chatterbox rendering serial unless the runtime is explicitly proven safe for concurrent inference.

## Gates

1. Map valid before transcription.
2. Every source page verified before narrator text.
3. Every EPUB document must reference verified `text/source` and an asset with valid lineage.
4. Every approved restored asset must retain its original SHA-256, prompt, reviewer, and approval timestamp.
5. Every narrator segment traceable before audio.
6. Every published audio file present and duration-checked before completion.
