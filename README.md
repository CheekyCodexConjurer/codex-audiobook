# Codex Audiobook

Native-only audiobook workflow for Codex. It maps PDF or EPUB sources, produces faithful source
text, derives PT-BR narrator text, and renders local Kokoro audio.

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
|- metadata\book-map.json
|- pages\
|- text\
`- audio\
```

## Shortcuts

```text
NUM0   $codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?
NUM0+1 $codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework
NUM0+2 $codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved
NUM0+3 $codex-workflows mode=RESEARCH.DEEP scope{web|github|repo?} no-edits fanout=adaptive evidence{primary|official|repo} synthesize{solution|roadmap} topic:
NUM0+7 $audiobook-codex stage=MAP native-only source{PDF|EPUB} library-root{E:\Pessoal\e-books} output{book-map.json} visual-fallback{pdf|computer} swarm{bounded}
NUM0+8 $audiobook-codex stage=TRANSCRIBE native-only input{book-map.json} output{text/source} fidelity=strict ledger=required
NUM0+9 $audiobook-codex stage=RENDER native-only input{text/source} output{text/locutor|audio} tts=kokoro language=pt-BR
```

## Validate

```powershell
.\scripts\validate.ps1
```

For a real Kokoro smoke run:

```powershell
$env:KOKORO_PYTHON = "$env:KOKORO_ROOT\venv\Scripts\python.exe"
$env:KOKORO_REAL_SMOKE = '1'
.\scripts\validate.ps1
```
