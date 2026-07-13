#Requires AutoHotkey v2.0
#SingleInstance Force

SendMode "Input"
SetWorkingDir A_ScriptDir
SetScrollLockState "Off"

ShowStatus(text) {
    ToolTip text
    SetTimer () => ToolTip(), -900
}

PastePrompt(text) {
    savedClipboard := ClipboardAll()
    A_Clipboard := text

    if !ClipWait(1) {
        A_Clipboard := savedClipboard
        ShowStatus "Clipboard indisponivel"
        return
    }

    Send "^v"
    Sleep 80
    A_Clipboard := savedClipboard
}

#HotIf GetKeyState("ScrollLock", "T")

Numpad0::PastePrompt("$codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?")
Numpad0 & Numpad1::PastePrompt("$codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework")
Numpad0 & Numpad2::PastePrompt("$codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved")
Numpad0 & Numpad3::PastePrompt("$codex-workflows mode=RESEARCH.DEEP scope{web|github|repo?} no-edits fanout=adaptive evidence{primary|official|repo} synthesize{solution|roadmap} topic: ")
Numpad0 & Numpad7::PastePrompt("$audiobook-codex stage=MAP native-only source{PDF|EPUB} library-root{E:\Pessoal\e-books} output{book-map.json} visual-fallback{pdf|computer} swarm{bounded}")
Numpad0 & Numpad8::PastePrompt("$audiobook-codex stage=TRANSCRIBE native-only input{book-map.json} output{text/source} fidelity=strict ledger=required")
Numpad0 & Numpad9::PastePrompt("$audiobook-codex stage=RENDER native-only input{text/source} output{text/locutor|audio} tts=kokoro language=pt-BR")

#HotIf
