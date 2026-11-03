# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Sendline desktop worker (one-folder layout).

Build:
  cd automate
  .\\scripts\\build-worker.ps1

Output: dist/sendline-worker/sendline-worker.exe (+ dependencies)
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
root = Path(SPEC).resolve().parent

worker_version = os.environ.get("WORKER_VERSION", "dev")

hidden = (
    collect_submodules("selenium")
    + [
        "pyperclip",
        "egress",
        "pace",
        "db",
        "vault",
        "cryptography",
        "argon2",
    ]
)

a = Analysis(
    [str(root / "worker.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["flask", "app"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sendline-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="sendline-worker",
)

# Write VERSION beside the exe after build (build-worker.ps1 also writes this).
print(f"WORKER_VERSION={worker_version}")
