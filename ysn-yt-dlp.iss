[Setup]
AppName=YSN (yt-dlp)
AppVersion=1.0
DefaultDirName={pf}\YSN-yt-dlp
DefaultGroupName=YSN (yt-dlp)
OutputBaseFilename=YSN-yt-dlp-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=icon.ico
PrivilegesRequired=admin

[Files]
; используем точное имя, которое PyInstaller создаёт: dist\ysn-yt-dlp.exe
Source: "dist\ysn-yt-dlp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\YSN (yt-dlp)"; Filename: "{app}\ysn-yt-dlp.exe"
Name: "{userdesktop}\YSN (yt-dlp)"; Filename: "{app}\ysn-yt-dlp.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\ysn-yt-dlp.exe"; Description: "Run YSN (yt-dlp) after installation"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
