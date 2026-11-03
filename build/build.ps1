# Rebuild DeepFocus.exe and refresh the Start Menu / Desktop shortcuts.
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# Never touch dist\DeepFocus while the app is running: a half-deleted folder leaves an exe that cannot start,
# and the scheduled tasks run that exe every minute during a session.
if (Get-Process -Name DeepFocus -ErrorAction SilentlyContinue) {
    throw "DeepFocus is running. Close it first, then build again."
}

python build\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "make_icon failed" }

# Build into a staging folder and swap it in only when the build succeeded.
$staging = Join-Path $root "build\stage"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
python -m PyInstaller build\DeepFocus.spec --noconfirm --clean --distpath $staging --workpath build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE); dist\DeepFocus was left untouched" }

$exe = Join-Path $root "dist\DeepFocus\DeepFocus.exe"
$dist = Join-Path $root "dist\DeepFocus"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
Move-Item (Join-Path $staging "DeepFocus") $dist
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
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
