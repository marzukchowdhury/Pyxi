; Pyxi's installer: always for the current user, with no "who is this for?" page, so it opens
; straight onto the progress bar (the assisted installer shows real progress while it unpacks,
; the one-click one only a spinner).

; The assisted installer would name a new install's folder after the product ("Programs\Pyxi");
; keep the folder every earlier Pyxi used, %LOCALAPPDATA%\Programs\pyxi-desktop, unless this PC
; already has Pyxi somewhere else (the installer then reuses that folder, as before).
!macro preInit
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
  ${if} $0 == ""
    WriteRegExpandStr HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation "$LOCALAPPDATA\Programs\pyxi-desktop"
  ${endif}
!macroend

; A Pyxi brain left running (Pyxi crashed, or was ended in Task Manager) keeps its files open,
; and the installer then leaves Pyxi half installed: it won't start ("No module named pyxi").
; Stop that brain first. Only Pyxi's own: its bundled python.exe running "-m pyxi serve".
!macro customInit
  nsExec::Exec `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $$_.Name -eq 'python.exe' -and $$_.ExecutablePath -like '*\resources\python\python.exe' -and $$_.CommandLine -like '*-m pyxi serve*' } | Invoke-CimMethod -MethodName Terminate | Out-Null"`
  Pop $0
  Sleep 1000
!macroend

; An update (Pyxi runs the installer with --updated) is one small window: "Updating Pyxi" with
; its progress bar, then Pyxi opens again by itself. No Finish page to click. A new install keeps
; the usual Finish page with "Run Pyxi".
!macro customPageAfterChangeDir
  !define MUI_PAGE_CUSTOMFUNCTION_SHOW pyxiProgressShow
  Function pyxiProgressShow
    ${if} ${isUpdated}
      !insertmacro MUI_HEADER_TEXT "Updating Pyxi" "Pyxi opens again by itself in a moment."
    ${endif}
  FunctionEnd
!macroend

!macro customFinishPage
  Function pyxiStartApp
    ${if} ${isUpdated}
      StrCpy $1 "--updated"
    ${else}
      StrCpy $1 ""
    ${endif}
    ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
  FunctionEnd

  Function pyxiFinishPre
    ${if} ${isUpdated}
      Call pyxiStartApp
      Abort
    ${endif}
  FunctionEnd

  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_FUNCTION "pyxiStartApp"
  !define MUI_PAGE_CUSTOMFUNCTION_PRE pyxiFinishPre
  !insertmacro MUI_PAGE_FINISH
!macroend

!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend
