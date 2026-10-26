# Builds dist\DynamicIsland-Setup-<version>.exe. Needs: pip install -r requirements.txt pyinstaller, and Inno Setup 6.
param([string]$Version = (Select-String -Path island.py -Pattern "^VERSION = '(.+)'").Matches[0].Groups[1].Value)
$ErrorActionPreference = 'Stop'

python -m PyInstaller --noconfirm --windowed --name DynamicIsland --icon icon.ico --exclude-module tkinter island.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
# software OpenGL fallback (20 MB) and Qt translations: the island draws without OpenGL and has its own text
Remove-Item dist\DynamicIsland\_internal\PySide6\opengl32sw.dll, dist\DynamicIsland\_internal\PySide6\translations `
    -Recurse -ErrorAction SilentlyContinue

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
