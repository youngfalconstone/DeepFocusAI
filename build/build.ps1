# Rebuild DeepFocus.exe and refresh the Start Menu / Desktop shortcuts.
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

python build\make_icon.py
python -m PyInstaller build\DeepFocus.spec --noconfirm --clean --distpath dist --workpath build\work

$exe = Join-Path $root "dist\DeepFocus\DeepFocus.exe"
if (-not (Test-Path $exe)) { throw "Build failed: $exe not found" }

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "DeepFocus AI.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = Split-Path $exe
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "DeepFocus AI - block distractions for a set time"
    $lnk.Save()
    "shortcut: $(Join-Path $dir 'DeepFocus AI.lnk')"
}
"built: $exe"
