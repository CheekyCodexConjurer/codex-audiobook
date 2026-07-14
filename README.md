# Codex Audiobook

Native-only audiobook workflow for Codex. It maps PDF or EPUB sources, inventories original
visual assets, produces faithful source text, derives PT-BR narrator text, exports semantic EPUB
editions, and renders local Chatterbox PT-BR audio.

Open `E:\Repositories\codex-audiobook` in Codex before processing a book. Attach the PDF or EPUB
to the task, then use the AHK shortcuts with Scroll Lock enabled. The generic
`codex_prompt_pad.ahk` remains the sole owner of the Scroll Lock toggle.

`NUM0`, `NUM0+1`, `NUM0+2`, and `NUM0+3` depend on the shared global
`codex-workflows` skill installed by `codex-workflows-prompt-pad`.

## Install

```powershell
.\scripts\install.ps1 -RestartAhk
```

Register the local marketplace after the Codex CLI configuration is compatible:

```powershell
.\scripts\install.ps1 -RegisterMarketplace
```

## Library

Book artifacts are stored outside Git:

```text
E:\Pessoal\e-books\<book>\
|- source\original.pdf
|- assets\images\original\
|- metadata\
|  |- book-map.json
|  |- assets-manifest.json
|  |- text-ledger.json
|  |- epub-manifest.json
|  |- audio-manifest.json
|  `- publication-manifest.json
|- pages\
|- text\
|- audio\
|- restoration\
|- exports\epub\
|- <book>-audiobook.mp3
`- <book>-fiel-classico.epub
```

## Shortcuts

```text
NUM0   $codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?
NUM0+1 $codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework
NUM0+2 $codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved
NUM0+3 $codex-workflows mode=RESEARCH.DEEP scope{web|github|repo?} no-edits fanout=adaptive evidence{primary|official|repo} synthesize{solution|roadmap} topic:
NUM0+7 $audiobook-codex stage=MAP native-only source{PDF|EPUB} library-root{E:\Pessoal\e-books} output{book-map.json|assets-manifest.json} visual-fallback{pdf|computer} swarm{bounded}
NUM0+8 $audiobook-codex stage=TRANSCRIBE native-only input{book-map.json|assets-manifest.json} output{text/source|epub-manifest.json} fidelity=strict ledger=required epub-profile{antique-paper}
NUM0+9 $audiobook-codex stage=RENDER native-only input{text/source|epub-manifest.json} output{text/locutor|audio|epub|publish-root} tts{chatterbox-pt-br} voice-profile{feminina-v1} locutor{line-delimited-v1|max=320} language=pt-BR epub-profile{antique-paper} epub-images{original|approved-restored} restoration=review-required
```

`NUM0+9` produces the canonical EPUB from verified `text/source` and original assets.
After the final audio and EPUB pass validation, run `publish_artifacts.py` to copy only
the unified audiobook and selected EPUB into the root of that book's library folder.
The provenance copies remain in `audio/` and `exports/epub/`.

New exports use the `antique-paper` profile: IM FELL English, the bundled OFL license,
the warm paper palette, and a locally generated editorial cover. The original source
cover remains in the reading order. A restored EPUB is separate and requires a recorded
visual approval for every generated derivative.

## Validate

```powershell
.\scripts\validate.ps1
```

Before shipping a Chatterbox renderer or `feminina-v1` change, also run the CUDA
reproducibility gate:

```powershell
.\scripts\validate.ps1 -ChatterboxSmoke
```

## Voice Calibration

Use `$voice-calibration` before changing the local voice reference, a local TTS
engine, or an official narrator profile. It creates a dedicated external workspace,
freezes the three-prompt PT-BR corpus and manually imported target audio with hashes,
then requires cross-prompt ranking, listening review, DSP comparison, and a production
smoke render before promotion.

The current decision is documented in
[`docs/voice-calibration/feminina-v1.md`](docs/voice-calibration/feminina-v1.md).
No separate AHK shortcut is registered: `NUM0+9` remains the production render route
for the already approved `feminina-v1` profile.

## Chatterbox PT-BR

The dedicated local runtime is installed at:

```text
E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe
```

It uses the locally downloaded `ResembleAI/Chatterbox-Multilingual-pt-br` V3 model
and the bundled reference voice
`plugins\audiobook-codex\assets\voices\Feminina.mp3`. Render it through that virtual
environment, then publish the final artifacts. The default `feminina-v1` profile fixes
the calibrated seed, sampling parameters, `min_p=0.114`, and a 0.22-second inter-line
silence. It is selected against the three-prompt calibration corpus and verifies the
bundled voice hash before naming a render `feminina-v1`. Its narrator input is one
complete spoken locution per non-empty line, with a 320-character maximum; the renderer
rejects digits, common abbreviations, URLs, email addresses, and bracketed audio markup.
The Chatterbox process forces Hugging Face and Transformers offline so missing local
assets fail instead of being fetched.

```powershell
$book = 'E:\Pessoal\e-books\O-Espiritismo-A-magia-e-as-sete-linhas-de-umbanda'
$python = 'E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe'

& $python .\plugins\audiobook-codex\scripts\render_chatterbox.py `
  --book-root $book `
  --input-file "$book\text\locutor\book.txt" `
  --output-dir "$book\audio\chatterbox-pt-br" `
  --format mp3

python .\plugins\audiobook-codex\scripts\publish_artifacts.py `
  --book-root $book `
  --audio "$book\audio\chatterbox-pt-br\audiobook.mp3" `
  --epub "$book\exports\epub\<book>-fiel-classico.epub"
```
