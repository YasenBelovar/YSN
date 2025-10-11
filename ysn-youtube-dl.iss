; ysn-youtube-dl.iss - Inno Setup script for YSN (youtube-dl)
[Setup]
AppName=YSN (youtube-dl)
AppVersion=1.0
DefaultDirName={pf}\YSN
DefaultGroupName=YSN
OutputDir=dist
OutputBaseFilename=ysn-youtube-dl-setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "src\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "thirdparty\aria2/aria2c.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExists("thirdparty\aria2/aria2c.exe")
Source: "thirdparty\aria2/LICENSE"; DestDir: "{app}\licenses"; Flags: ignoreversion; Check: FileExists("thirdparty\aria2/LICENSE")

[Icons]
Name: "{group}\YSN"; Filename: "{app}\ysn.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{commondesktop}\YSN"; Filename: "{app}\ysn.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; Flags: unchecked
Name: addtoPath; Description: "Add YSN folder to system PATH (requires admin)"; Flags: unchecked
