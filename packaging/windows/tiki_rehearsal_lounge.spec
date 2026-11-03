# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import sys

block_cipher = None

# Collect heavy packages
datas = []
binaries = []
hiddenimports = [
    'audio',
    'audio.calibration',
    'audio.player',
    'audio.recorder',
    'audio.tone_generator',
    'audio.vocal_analyzer',
    'ai',
    'ai.offline_coach',
    'db',
    'db.bridge',
    'ui',
    'ui.studio_window',
    'config',
    'downloader',
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'PIL',
    'PIL._tkinter_finder',
    'winsound',
    'sounddevice',
    '_sounddevice_data',
]

for pkg in ['yt_dlp', 'numpy', 'scipy', 'PIL', 'sounddevice']:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Root project directory
spec_dir = Path(SPECPATH).resolve()
repo_root = spec_dir.parent.parent

# Add static assets
static_dir = repo_root / 'static'
if static_dir.exists():
    datas.append((str(static_dir), 'static'))

# Stage local helper binaries if present
bin_dir = spec_dir / 'bin'
if bin_dir.exists():
    for f in bin_dir.glob('*.exe'):
        binaries.append((str(f), 'bin'))
    for f in bin_dir.glob('*.dll'):
        binaries.append((str(f), 'bin'))

a = Analysis(
    [str(repo_root / 'main.py')],
    pathex=[str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'unittest'],
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
    name='TikiRehearsalLounge',
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
    icon=str(spec_dir / 'tiki-icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TikiRehearsalLounge',
)
