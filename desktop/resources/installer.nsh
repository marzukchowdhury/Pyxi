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

!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend
