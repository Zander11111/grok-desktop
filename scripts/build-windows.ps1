# Build GrokDesktop.exe and the per-user installer on a real Windows PC (no admin needed).
# Needs Python 3.12+ on PATH and Inno Setup 6 (winget install JRSoftware.InnoSetup --scope user).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .venv-win
.\.venv-win\Scripts\python -m pip install -q PySide6-Essentials pyinstaller markdown pygments
if (-not (Test-Path packaging\icon.ico)) { .\.venv-win\Scripts\python scripts\make_icon.py }
.\.venv-win\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\grok-desktop.spec
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
& $iscc /Q packaging\installer.iss
Get-Item dist\GrokDesktop-Setup-*.exe
