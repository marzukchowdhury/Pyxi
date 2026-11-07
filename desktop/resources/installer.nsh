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

!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend
