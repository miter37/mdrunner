# PyInstaller spec for mdrunner.
#
# Build:
#   Linux:  uv run --extra build pyinstaller --clean installer/mdrunner.spec
#   Win:    pyinstaller --clean installer\mdrunner.spec
#
# Output: dist/mdrunner (or dist/mdrunner.exe on Windows).

# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# Resolve paths relative to the spec file's location, not cwd.
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
REPO_ROOT = os.path.dirname(SPEC_DIR)
sys.path.insert(0, REPO_ROOT)

# When PyInstaller bundles a package, `__main__.py` becomes a top-level
# script and relative imports fail. Point the entry at `mdrunner.cli:main`
# instead — that module uses only absolute imports and is safe to invoke
# from a frozen zip. (The CLI module itself detects headless vs GUI mode.)

block_cipher = None

a = Analysis(
    [os.path.join(REPO_ROOT, 'installer', 'mdrunner-launcher.py')],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "mdrunner",
        "mdrunner.agents",
        "mdrunner.scheduler",
        "mdrunner.scheduler.linux",
        "mdrunner.scheduler.windows",
        "mdrunner.ui",
        "mdrunner.ui.main_window",
        "mdrunner.ui.task_dialog",
        "mdrunner.ui.settings_dialog",
        "mdrunner.ui.log_view",
        "mdrunner.ui.workers",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc_data",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='mdrunner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
