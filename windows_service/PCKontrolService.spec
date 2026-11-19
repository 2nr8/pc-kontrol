# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller onedir spec dosyası: PC Kontrol Windows Hizmeti.

Derleme örneği:
    pyinstaller --noconfirm --clean --distpath build_dist --workpath build_work windows_service/PCKontrolService.spec
"""

import os
from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.abspath(os.path.join(SPEC_DIR, ".."))
PC_SERVER_DIR = os.path.join(ROOT, "pc_server")
ENTRY_SCRIPT = os.path.join(SPEC_DIR, "service_runner.py")
ICON_PATH = os.path.join(ROOT, "installer", "pc_kontrol.ico")
APK_PATH = os.path.join(ROOT, "dist", "PC.apk")

datas = [
    (os.path.join(PC_SERVER_DIR, "static"), "static"),
    (os.path.join(PC_SERVER_DIR, "lib"), "lib"),
]
if os.path.isfile(APK_PATH):
    datas.append((APK_PATH, "dist"))

binaries = []
hiddenimports = [
    "psutil",
    "h11",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "server",
]

for pkg_name in ("pythonnet", "clr_loader", "uvicorn"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg_name)
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hiddenimports)

hiddenimports = list(dict.fromkeys(hiddenimports))

block_cipher = None

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[PC_SERVER_DIR, SPEC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="PCKontrolService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH if os.path.isfile(ICON_PATH) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PCKontrolService",
)
