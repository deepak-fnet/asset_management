; ---- add to your existing installer.iss ----

[Files]
; Ship the worker exe (build it with PyInstaller --onefile --noconsole)
Source: "dist\RemoteAssistWorker.exe"; DestDir: "{app}"; Flags: ignoreversion
; Ship the org config (Odoo URL + DB) read by the worker
Source: "remote.json"; DestDir: "{commonappdata}\AssetAgent"; Flags: ignoreversion

[Registry]
; Launch the worker in each user's session at logon (user session = has a screen)
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "AssetRemoteAssist"; \
  ValueData: """{app}\RemoteAssistWorker.exe"""; Flags: uninsdeletevalue
