[Setup]
AppName=YSN (youtube-dl)
AppVersion=1.0
DefaultDirName={pf}\YSN-youtube-dl
DefaultGroupName=YSN (youtube-dl)
OutputBaseFilename=YSN-youtube-dl-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=icon.ico
PrivilegesRequired=admin

[Files]
Source: "dist\ysn-youtube-dl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\YSN (youtube-dl)"; Filename: "{app}\ysn-youtube-dl.exe"
Name: "{userdesktop}\YSN (youtube-dl)"; Filename: "{app}\ysn-youtube-dl.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\ysn-youtube-dl.exe"; Description: "Run YSN (youtube-dl) after installation"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
