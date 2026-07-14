@echo off
setlocal
chcp 65001 >nul

set "SOURCE=%~dp0app"
set "TARGET=%LOCALAPPDATA%\TeamsInterpreter"

if not exist "%SOURCE%\TeamsInterpreter.exe" (
    echo Installation files are incomplete. Please extract the entire ZIP first.
    pause
    exit /b 1
)

echo Installing Teams Interpreter for the current Windows user...
if not exist "%TARGET%" mkdir "%TARGET%"
robocopy "%SOURCE%" "%TARGET%" /E /COPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Failed to copy application files.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $targets = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')); foreach ($folder in $targets) { $s = $w.CreateShortcut([IO.Path]::Combine($folder, 'Teams Interpreter.lnk')); $s.TargetPath = [IO.Path]::Combine($env:LOCALAPPDATA, 'TeamsInterpreter', 'TeamsInterpreter.exe'); $s.WorkingDirectory = [IO.Path]::Combine($env:LOCALAPPDATA, 'TeamsInterpreter'); $s.Description = 'Teams simultaneous interpreter'; $s.Save() }"
if errorlevel 1 (
    echo Application files were installed, but shortcut creation failed.
    pause
    exit /b 1
)

echo Installation completed. Starting Teams Interpreter...
start "" "%TARGET%\TeamsInterpreter.exe"
exit /b 0
