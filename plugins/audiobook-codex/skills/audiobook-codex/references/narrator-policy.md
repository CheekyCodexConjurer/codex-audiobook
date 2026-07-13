# Narrator Policy

Apply this policy only after source text has passed its page ledger.

## Allowed

- Expand numbers, dates, abbreviations, and symbols when necessary for natural PT-BR speech.
- Add conservative pauses and punctuation that do not change meaning.
- Remove page furniture already marked as excluded in `book-map.json`.
- Introduce short, objective descriptions for informative figures approved in the map.
- Translate non-Portuguese source into PT-BR only in the narrator artifact.

## Forbidden

- Change source wording silently.
- Summarize, censor, interpret, moralize, explain, or invent content.
- Repair an uncertain word by guessing.
- Merge unrelated fragments across a page boundary.
- Delete meaningful source text without a map-backed exclusion.
- Reuse EPUB alt text or a generated restoration prompt as narrator content. EPUB accessibility annotations and image derivatives are separate artifacts.

## Required Change Records

Record a change when it expands a number, translates text, describes a figure, removes approved page furniture, or changes punctuation beyond a trivial whitespace fix. Keep each record tied to source pages and source/narrator hashes.
