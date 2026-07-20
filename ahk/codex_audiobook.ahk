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
Numpad0 & Numpad3::PastePrompt("$codex-workflows mode=RESEARCH.DEEP")
Numpad0 & Numpad7::PastePrompt("$audiobook-codex stage=PHASE-1")
Numpad0 & Numpad9::PastePrompt("$audiobook-codex stage=PHASE-2")

#HotIf
