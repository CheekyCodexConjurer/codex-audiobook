# Swarm Protocol

Use a bounded swarm only when it reduces elapsed time without weakening fidelity.

## Roles

- Main agent: owns `book-map.json`, final TXT files, manifests, merges, and quality gates.
- Structure scout: read-only survey of layout, TOC, offsets, chapters, and visual anomalies.
- Transcriber: writes only assigned source-page files and ledger drafts.
- Verifier: independently compares assigned pages against the source and approves or rejects ledger entries.

## Boundaries

- Give every transcriber an exclusive logical-page range. Supply one boundary page as context but prohibit output for that context page.
- Do not let workers edit `book-map.json`, chapter TXT, or audio manifests.
- Merge and validate after every batch.
- Use at most one Computer Use worker at a time.
- Keep Kokoro rendering serial unless the runtime is explicitly proven safe for concurrent inference.

## Gates

1. Map valid before transcription.
2. Every source page verified before narrator text.
3. Every narrator segment traceable before audio.
4. Every published audio file present and duration-checked before completion.
