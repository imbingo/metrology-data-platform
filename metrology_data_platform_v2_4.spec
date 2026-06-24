# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['metrology_config_app_v2_3_pie_delete_process_guard.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pytesseract', 'PIL', 'PIL.Image', 'cv2', 'numpy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='metrology_data_platform_v2_4',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='metrology_data_platform_v2_4',
)
