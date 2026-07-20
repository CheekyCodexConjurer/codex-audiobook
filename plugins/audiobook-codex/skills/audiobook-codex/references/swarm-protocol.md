# Swarm Protocol

Use a claim-scoped, sliding-window swarm whenever independent units make it faster
without weakening fidelity. The public stage selectors remain minimal; this protocol
owns all pool sizing, queueing, messaging, staging, verification, and merge behavior.

## Coordinator

The main agent is the single coordinator and canonical-state writer. It owns:

- `book-map.json`, canonical ledgers, layout and EPUB manifests;
- whole-book brief, glossary, fluid style, narrator metadata, audio manifests;
- claim planning, conflict detection, queue admission, merge, global gates, exports,
  publication, and final quality.

The coordinator must keep the critical path moving while workers are active. Do not
wait for a whole batch when another independent claim can be admitted, verified, or
merged.

## Roles

- Structure scout: read-only layout, TOC, offsets, chapters, visual anomalies,
  source-image classification, and EPUB block semantics.
- Transcriber: writes only assigned source-page files plus one text-ledger shard.
- Translator: writes only assigned PT-BR chapter/page outputs plus one translation
  shard from a frozen brief, glossary revision, complete chapter, and neighboring
  context.
- Editor: writes only assigned fluid chapter outputs plus one fluid shard from a
  frozen whole-book style and glossary revision.
- Narrator: writes only assigned `text/locutor/chapters` files plus narrator-change
  and narrator-review shards.
- Verifier: read-only independent source, translation, fluid, narrator, EPUB, or
  asset verification for an explicitly assigned claim.

Use the dedicated `audiobook-structure`, `audiobook-transcriber`,
`audiobook-translator`, `audiobook-editor`, `audiobook-narrator`, and
`audiobook-verifier` profiles when installed.

## Claim Contract

Every writable assignment requires a validated immutable claim map under
`assembly/metadata/work/claims/`. Use schema `1.0` and record:

- `claim_id`, stage, state, immutable `claim_order`, priority, dependencies,
  attempt, producer, and verifier;
- immutable `read_set` paths and SHA-256 values;
- exclusive `write_set`, canonical targets, and `no_touch` paths;
- owned unit IDs, read-only context IDs, and deterministic claim order;
- frozen brief/style/glossary/context hashes where applicable;
- claim-scoped validation commands and lease metadata.

Stage claim maps must freeze the current canonical inputs they depend on:

- transcription: `metadata/book-map.json`;
- translation: `metadata/book-map.json`, `metadata/text-ledger.json`, and the
  frozen `metadata/translation-ledger.json` carrying the translation decision,
  brief, glossary, and quality contract;
- fluid editing: `metadata/book-map.json`, `metadata/text-ledger.json`,
  `metadata/fluid-style.json`, and `metadata/translation-ledger.json` whenever
  the style selects `translated-pt-br` as its base edition.

Before dispatch, reject unknown claim stages, missing required read-set entries,
overlapping canonical targets, empty writable target sets, or write scopes on
read-only `MAP` claims. Supported claim stages are the closed set `MAP`,
`TRANSCRIBE`, `TRANSLATE`, `FLUID`, and `RENDER`; `NARRATE`, typos, and other
ad-hoc values are invalid.
A worker writes only to its claim staging paths and never directly edits a shared
canonical JSON.
Each shard stores `claim_sha256` from the immutable claim contract: the digest excludes
only lifecycle-mutating `status` and `lease`, while producer, verifier, inputs, scope,
targets, no-touch paths, dependencies, `claim_order`, priority, attempt, and validation
commands remain hash-bound. Lifecycle advancement therefore never requires rewriting a
produced shard. Before promotion, recheck every input hash and canonical target against
the current book root. A changed dependency supersedes the claim instead of silently
merging stale output.

Claim states are monotonic:

```text
planned → leased → in_progress → ready_for_verification → verified → merged
```

Terminal side states are `blocked` and `abandoned`. Retry with a new claim attempt;
never overwrite evidence from an earlier attempt.

## Messages

Use messages to keep a warm role pool supplied with work instead of spawning one agent
per page. Every worker result or progress message identifies its claim and attempt.
Supported intents are:

- `claim.accepted`, `claim.progress`, `claim.blocked`, `claim.produced`;
- `context.request`, `scope.violation`, `verification.result`;
- `claim.retry`, `claim.supersede`, and `claim.cancel`.

Brief, glossary, style, and context snapshots are immutable within a claim. A new
revision applies to future claims or explicitly supersedes affected active claims; do
not mutate a worker's contract in place.

## Staging and Merge

Worker outputs and ledger shards live under `assembly/metadata/work/`. Canonical
ledgers remain single-writer artifacts.

```text
metadata/work/
  claims/
  text-ledger.d/
  translation-ledger.d/
  fluid-ledger.d/
  narrator-changes.d/
  narrator-review.d/
  glossary-proposals.d/
  ambiguity-records.d/
```

For each claim:

1. validate the claim and its immutable inputs;
2. produce files and one ledger/review shard;
3. run claim-scoped structural validation;
4. assign an independent verifier;
5. accept or reject the complete claim;
6. move accepted claims to `verified`, then merge only their hash-bound shards
   deterministically and atomically with the claim map and book root present;
7. run the chapter or stage fan-in validation;
8. release dependent claims.

Any non-empty shard merge without the corresponding claim map and book root is invalid.
Merge must validate the claim map against that book root immediately before atomic
promotion, verify that every referenced claim exists, is `verified`, has the exact
stage for its shard kind (`text` -> `TRANSCRIBE`, `translation` -> `TRANSLATE`,
`fluid` -> `FLUID`), matches the shard producer, verifier, immutable claim digest,
and `claim_order`, and then assemble in claim-map order rather than caller or
shard-list order. Claim-scoped ledger validation
must derive owned logical pages from `book-map.json` plus
`scope.unit_ids`; shard page records and chapter `source_pages` must cover exactly
those owned pages. `context_unit_ids` are read-only and never valid shard outputs.

Global whole-book gates still run after all applicable claims are frozen. Incremental
validation accelerates work; it never substitutes for final coverage, ordering,
consistency, or publication validation.

## Default Pool Policy

Pool sizes are upper bounds, not spawn requirements. Admit work only while downstream
verification and merge have capacity.

| Pool | Default active limit | Mergeable buffer |
| --- | ---: | ---: |
| Structure | 4-6 | 8 |
| Transcription | 6-10 | 12 |
| Source verification | 3-5 | 8 |
| Translation | 4-6 | 6 |
| Fluid editing | 4-6 | 6 |
| Literary verification | 2-3 | 4 |
| Narrator | 3-4 | 6 |
| Narrator verification | 2 | 4 |
| Computer Use | 1 | 1 |
| Native image restoration | 1 | 1 |
| Chatterbox inference | 1 | 1 |
| Chapter assembly/FFmpeg | 1 | 2 |

Pause dispatch when produced/verifying claims exceed the buffer, verifier lag grows,
the merge queue is full, repeated claims block on one shared decision, or rejection
rate indicates that the frozen context is unstable. Reuse context-fit agents and send
the next claim when a slot closes.

## Stage Routing

### MAP

Run exactly one preflight per book root. Source staging, page rendering, original-asset
extraction, and canonical manifest writes are serial and single-owner. After immutable
pages/assets exist:

- fan out read-only structure ranges with one boundary unit of context;
- fan out asset classification by exclusive asset IDs;
- reconcile TOC, offsets, chapter boundaries, and cross-range conflicts serially;
- atomically freeze `book-map.json` and `assets-manifest.json`;
- run map, asset, and layout validations in parallel on the same snapshot.

Do not run two preflights, refreshes, or canonical map merges concurrently.

### TRANSCRIBE

Partition exclusive contiguous page ranges, normally six to ten pages, aligned to
chapter boundaries when practical. Supply one neighboring page as read-only context.
As soon as a transcription claim is produced, send it to a different verifier; do not
wait for every transcriber.

Accepted page shards fan in by chapter. Assemble chapter/book text deterministically
from verified ledger ownership, never by filename guessing or manual concatenation.
Run the complete source-ledger and semantic-layout gates after global fan-in.

### TRANSLATE

Freeze the whole-book decision, brief, and glossary revision before wide fan-out.
Use one complete chapter per claim; split only an exceptionally large chapter into
contiguous scenes while retaining complete chapter and neighboring-scene context.
Translators emit glossary and ambiguity proposals rather than editing shared global
arrays.

Claim verification checks semantic fidelity and literary PT-BR. After all chapters are
frozen, run the independent whole-book consistency, glossary, ambiguity, and approval
gates. Textual translation work does not depend on the source EPUB export; publication
still requires all EPUB/PDF gates.

### FLUID

Freeze one approved PT-BR base, style, voice, and glossary revision. Use one chapter
per editor claim. Preserve exact ordered coverage of every base block; schema `1.2`
may change fluid block count only through audited editorial exclusions.

Verify meaning, additions, omissions, archaic modernization, editorial exclusions,
fluency, and local consistency per claim. Then assemble the canonical book and run the
independent whole-book voice/terminology and coverage gates.

### RENDER

Prepare and verify locutor chapters through narrator claims before the final narrator
fan-in. Canonical narrator metadata remains single-writer.

Keep Chatterbox inference serial: one model and one `generate()` call at a time unless
a separate reproducibility/VRAM benchmark explicitly proves another configuration.
Overlap only safe CPU/I/O work:

```text
[GPU] render chapter N+1
  ||
[CPU worker] validate, join, apply publication tempo, and transcode chapter N
```

Chapter assembly receives an immutable journal snapshot and never writes the render
journal. One owner updates `audio-chapters-manifest.json`. Drain and verify the assembly
queue before full-book mounting. Build the full-book master from validated immutable
chapter masters plus explicit inter-chapter pauses.

After text/asset inputs freeze, EPUB and PDF export and their independent validations
may run in parallel. Publication remains one final atomic gate.

## Boundaries

- Never let workers edit `book-map.json`, canonical ledgers/manifests, full-book TXT,
  publication artifacts, or audio manifests.
- Never overwrite `assembly/source/`, `assembly/pages/`, or
  `assembly/assets/images/original/`.
- Presentation belongs in semantic layouts, never in faithful source text.
- Translators never change `text/source`; editors never change source or translation;
  narrators never change the selected textual base.
- Only the coordinator promotes a reviewed restoration to
  `assets/restoration/approved/`.
- Computer Use and native image restoration remain single-worker resources.

## Global Gates

1. Map and asset snapshot valid before transcription.
2. Every source unit verified and globally covered before source publication.
3. Translation only for a whole foreign-language work with a complete source ledger
   and reviewed brief.
4. Translation approval requires resolved ambiguities, approved glossary, semantic
   fidelity, literary naturalness, and whole-book consistency.
5. Fluid approval requires exact ordered base coverage, no additions, no unsupported
   omissions, complete archaic modernization, audited exclusions, fluency, and
   whole-book consistency.
6. Every EPUB/PDF document references an approved textual edition and lineage-valid
   asset in canonical order.
7. Every narrator change and exclusion is traceable and independently approved.
8. Every rendered segment, chapter, and full-book artifact matches its plan, identity,
   hashes, duration, and decodable media.
9. Publication is atomic and occurs only after all selected artifacts pass their
   complete global gates.
