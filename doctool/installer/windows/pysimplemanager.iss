; Установщик PySimpleManager для Windows 10/11 (Inno Setup 6). Собирает installer/build.py:
;   ISCC /DAppVersion=0.7.5 /DSourceDir=<папка PyInstaller> /DOutDir=<куда> [/DIconFile=<.ico>] pysimplemanager.iss
; Ставится для текущего пользователя (без прав администратора) в %LOCALAPPDATA%\Programs\PySimpleManager.
; Данные пользователя (Документы\PySimpleManager) установка, обновление и удаление не трогают.
; Файл в UTF-8 с BOM — иначе Inno Setup прочитает русский текст в кодировке ANSI.

#define AppName "PySimpleManager"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error Не задана папка программы: /DSourceDir=...
#endif
#ifndef OutDir
  #define OutDir "."
#endif

[Setup]
; AppId не менять: по нему новая версия ставится поверх старой
AppId={{E2A25139-9831-4CD9-A53C-43613AC7DDC6}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/Flarezest/PyTextRecognitionTPD
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename={#AppName}-{#AppVersion}-windows-x64-setup
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; файлы прежней версии программы: в новой их может не быть
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\docs"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{userdocs}\{#AppName}"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"
Name: "{group}\Результаты проверок (папка)"; Filename: "{userdocs}\{#AppName}"
Name: "{group}\Расширение Chrome (папка)"; Filename: "{app}\chrome_extension"
Name: "{group}\Как установить расширение Chrome"; Filename: "{app}\chrome_extension\КАК_УСТАНОВИТЬ.txt"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\chrome_extension\КАК_УСТАНОВИТЬ.txt"; Description: "Открыть инструкцию: расширение Chrome"; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\{#AppName}.exe"; Description: "Запустить {#AppName}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; кэш Python рядом с модулями заявлений (создаётся при работе)
Type: filesandordirs; Name: "{app}\_internal"
