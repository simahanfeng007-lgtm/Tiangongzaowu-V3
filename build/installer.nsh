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

; =============================================================================
; v3 起源版独立 NSIS 安装器（2026-08-26，凌霜委托 Kimi）
;
; 编译方式（不依赖 electron-builder）：
;   1) 先准备负载目录 dist\payload\{runtime,backend,shell,assets}
;      runtime = app\runtime\python312（provision-embedded-python.ps1 产物）
;      backend = src\ + v3 + _internal
;      shell   = shell\tiangong_shell.py
;      assets  = 图标等（tiangong-logo.ico）
;   2) 仓库根目录执行: makensis build\installer.nsh
;   产物: dist\TiangongV3-Setup-3.0.3.exe
;
; electron-builder 打包完整版时以 include 方式引入本文件且已定义
; BUILD_RESOURCES_DIR，下方独立脚本整块被预处理器跳过，不影响完整版。
; =============================================================================
!ifndef BUILD_RESOURCES_DIR

!include "LogicLib.nsh"

!define APPNAME "天工造物 v3"
!define APPID "TiangongV3"
!define VERSION "3.0.3"
!define PUBLISHER "于泳翔"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"

Name "${APPNAME} ${VERSION}"
OutFile "${__FILEDIR__}\..\dist\TiangongV3-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\TiangongV3"
InstallDirRegKey HKCU "Software\${APPID}" "InstallDir"
RequestExecutionLevel user          ; 纯用户态安装，不需要管理员
Unicode true
SetCompressor /SOLID lzma
CRCCheck on                          ; 启动时内置 CRC 自检（hotfix 完整性第一道）
ManifestDPIAware true
BrandingText "${APPNAME} 起源版"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "3.0.3.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APPNAME} 起源版安装程序"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

; 可选：内测 hotfix 校验和。构建时加 /DWRITE_SHA256=1，
; 编译完成后自动在 exe 旁生成 .sha256（随 hotfix 包发布供比对）。
!ifdef WRITE_SHA256
  !finalize 'powershell.exe -NoProfile -NonInteractive -Command "(Get-FileHash -Algorithm SHA256 ''%1'').Hash.ToLower() | Set-Content -Encoding ascii ''%1.sha256''"'
!endif

Section "Install"
  SetOutPath "$INSTDIR"

  ; --- 0) 覆盖安装前的非破坏性备份（与完整版 customInstall 同一契约） ---
  ; bug-fix: ini 已迁至 shell\ 子目录（与 tiangong_shell.py 读取路径对齐），备份触发条件同步迁移（2026-08-26，凌霜修 UX）
  ${If} ${FileExists} "$INSTDIR\shell\tiangong-launcher.ini"
    File /oname=$PLUGINSDIR\tiangong-preinstall-backup.ps1 "${__FILEDIR__}\preinstall-backup.ps1"
    DetailPrint "Creating a verified, non-destructive recovery snapshot..."
    ExecWait '"$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\tiangong-preinstall-backup.ps1" -InstallDirectory "$INSTDIR"' $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "安装前数据备份失败（错误码 $0）。为保护现有数据，安装已停止；应用数据未被删除。"
      Abort
    ${EndIf}
  ${EndIf}

  ; --- 1) 释放负载 ---
  ; 布局对齐 shell/tiangong_shell.py 默认解析：app_root = shell_dir\..\app
  DetailPrint "Installing embedded Python runtime..."
  SetOutPath "$INSTDIR\app\runtime"
  File /r "${__FILEDIR__}\..\dist\payload\runtime\*.*"

  DetailPrint "Installing backend services..."
  SetOutPath "$INSTDIR\app\backend"
  File /r "${__FILEDIR__}\..\dist\payload\backend\*.*"

  DetailPrint "Installing desktop shell..."
  SetOutPath "$INSTDIR\shell"
  File /r "${__FILEDIR__}\..\dist\payload\shell\*.*"

  SetOutPath "$INSTDIR\assets"
  File /r "${__FILEDIR__}\..\dist\payload\assets\*.*"

  ; --- 2) 生成 tiangong-launcher.ini（5 个 token + 路径，CSPRNG） ---
  SetOutPath "$INSTDIR"
  File /oname=$PLUGINSDIR\write-launcher.ps1 "${__FILEDIR__}\write-launcher.ps1"
  DetailPrint "Generating gateway tokens and launcher configuration..."
  ExecWait '"$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\write-launcher.ps1" -InstallDir "$INSTDIR"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "启动配置生成失败（错误码 $0）。安装已中止。"
    Abort
  ${EndIf}
  ; bug-fix: 校验路径与 write-launcher.ps1 实际写入位置（shell\ 子目录）对齐（2026-08-26，凌霜修 UX）
  ${IfNot} ${FileExists} "$INSTDIR\shell\tiangong-launcher.ini"
    MessageBox MB_ICONSTOP|MB_OK "启动配置未生成。安装已中止。"
    Abort
  ${EndIf}

  ; --- 3) 开始菜单快捷方式 ---
  CreateDirectory "$SMPROGRAMS\天工造物"
  ; 直接以脚本路径启动（比 -m shell.tiangong_shell 稳：不要求包结构与 cwd）
  CreateShortcut "$SMPROGRAMS\天工造物\天工造物 v3.lnk" \
    "$INSTDIR\app\runtime\python312\pythonw.exe" \
    '"$INSTDIR\shell\tiangong_shell.py"' \
    "$INSTDIR\assets\tiangong-logo.ico" 0 SW_SHOWNORMAL "" "天工造物 v3 起源版"
  CreateShortcut "$SMPROGRAMS\天工造物\卸载 天工造物 v3.lnk" \
    "$INSTDIR\uninstall.exe" "" "$INSTDIR\assets\tiangong-logo.ico" 0

  ; --- 4) 卸载注册 ---
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\${APPID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\assets\tiangong-logo.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; bug-fix: 用户数据目录对齐 bootstrap.py 默认 state 根 / write-launcher.ps1 的 workspace_root
  ; —— 统一为 %APPDATA%\tiangong-v3-qiyuan（workspace\ 与 runtime\gateway 同在此处），
  ; 默认保留，弹框询问；token 配置 tiangong-launcher.ini 属安装产物，随 $INSTDIR 一并删除（2026-08-26，凌霜修 UX）
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "是否同时删除用户数据（$APPDATA\tiangong-v3-qiyuan，含工作区与对话记录）？$\n选择“否”将保留，重新安装后可继续使用。" \
    /SD IDNO IDNO keep_data
  RMDir /r "$APPDATA\tiangong-v3-qiyuan"
  keep_data:

  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\天工造物\天工造物 v3.lnk"
  Delete "$SMPROGRAMS\天工造物\卸载 天工造物 v3.lnk"
  RMDir "$SMPROGRAMS\天工造物"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APPID}"
SectionEnd

!endif ; BUILD_RESOURCES_DIR
