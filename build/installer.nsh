!macro customInstall
  File /oname=$PLUGINSDIR\tiangong-preinstall-backup.ps1 "${BUILD_RESOURCES_DIR}\preinstall-backup.ps1"
  DetailPrint "Creating a verified, non-destructive recovery snapshot..."
  ExecWait '"$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\tiangong-preinstall-backup.ps1" -InstallDirectory "$INSTDIR"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "安装前数据备份失败（错误码 $0）。为保护现有数据，安装已停止；应用数据未被删除。"
    Abort
  ${EndIf}

  DetailPrint "Installing Tiangong V3 verified runtime components..."
  CreateDirectory "$INSTDIR\resources\update-baseline"
  CopyFiles /SILENT "$EXEPATH" "$INSTDIR\resources\update-baseline\TiangongV3-current.exe"
!macroend

!macro customUnInstall
  DetailPrint "Removing Tiangong V3 application files; user data and recovery snapshots are preserved."
!macroend
