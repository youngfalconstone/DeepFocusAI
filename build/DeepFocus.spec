# PyInstaller spec for DeepFocus AI. Build with:  python -m PyInstaller build\DeepFocus.spec --noconfirm
# Produces dist\DeepFocus\DeepFocus.exe (one-folder build: fast start, no temp extraction).
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
CONSOLE = bool(os.environ.get("DEEPFOCUS_CONSOLE"))  # set to build a console variant for debugging

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "assets" / "deepfocus.ico"), "assets"), (str(ROOT / "assets" / "deepfocus.png"), "assets")],
    hiddenimports=["deepfocus.app", "deepfocus.helper"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[] if os.environ.get("DEEPFOCUS_NOEXCL") else
    ["unittest", "pydoc", "doctest", "lib2to3", "test", "tests", "sqlite3", "curses"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepFocus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    icon=str(ROOT / "assets" / "deepfocus.ico"),
    version=None if os.environ.get("DEEPFOCUS_NOVER") else str(ROOT / "build" / "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DeepFocus")
