; Mustang AI — NSIS installer hooks (inserted into Tauri's installer template).
;
; User data is never touched here: conversations, settings and logs live in
; %LOCALAPPDATA%\MustangAI and models in the user's chosen folder, both outside $INSTDIR
; and outside the bundle-identifier folders that the uninstaller's "delete app data"
; option clears (WebView2 cache only).

!macro MUSTANG_QUIT_RUNNING
  ; Ask a running Mustang AI to quit gracefully (saves a partial reply, pauses downloads,
  ; unloads the model) before its files are replaced or removed. Tauri's own
  ; running-application check still follows as a fallback.
  IfFileExists "$INSTDIR\${MAINBINARYNAME}.exe" 0 mustang_quit_done
    nsis_tauri_utils::FindProcess "${MAINBINARYNAME}.exe"
    Pop $R0
    StrCmp $R0 0 0 mustang_quit_done
    DetailPrint "Asking Mustang AI to close..."
    Exec '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
    StrCpy $R1 0
    mustang_quit_wait:
      Sleep 500
      IntOp $R1 $R1 + 1
      nsis_tauri_utils::FindProcess "${MAINBINARYNAME}.exe"
      Pop $R0
      StrCmp $R0 0 0 mustang_quit_done
      IntCmp $R1 40 mustang_quit_done mustang_quit_wait mustang_quit_done
  mustang_quit_done:
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro MUSTANG_QUIT_RUNNING
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro MUSTANG_QUIT_RUNNING
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  IfSilent mustang_post_uninstall_done
    MessageBox MB_OK|MB_ICONINFORMATION "Mustang AI was removed.$\r$\n$\r$\nYour conversations, settings and logs were kept in:$\r$\n$LOCALAPPDATA\MustangAI$\r$\n$\r$\nYour model files were not touched. Delete these folders yourself if you no longer need them."
  mustang_post_uninstall_done:
!macroend
