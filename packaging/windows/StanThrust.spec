# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

project_root = Path.cwd()
sys.path.insert(0, str(project_root))
from liquid_engine_studio import __version__ as APP_VERSION

asset_dir = project_root / "assets"
icon_path = asset_dir / "app_icon.ico"
cantera_datas = collect_data_files("cantera")
cantera_binaries = collect_dynamic_libs("cantera")

analysis_datas = cantera_datas + [
    (str(asset_dir / "Logo.png"), "assets"),
    (str(asset_dir / "Logo.svg"), "assets"),
    (str(project_root / "liquid_engine_studio" / "data" / "default_mechanism.json"), "liquid_engine_studio/data"),
    (str(project_root / "liquid_engine_studio" / "data" / "rocket_mech_equilibrium.yaml"), "liquid_engine_studio/data"),
    (str(project_root / "liquid_engine_studio" / "data" / "rocket_mech.yaml"), "liquid_engine_studio/data"),
    (str(project_root / "liquid_engine_studio" / "data" / "rocket_mech_minimal.yaml"), "liquid_engine_studio/data"),
]
if icon_path.exists():
    analysis_datas.append((str(icon_path), "."))

analysis_hiddenimports = [
    *collect_submodules("cantera"),
    "cantera",
    "sklearn.gaussian_process",
    "sklearn.gaussian_process.kernels",
    "sklearn.preprocessing",
    "sklearn.pipeline",
    "scipy",
    "scipy.optimize",
    "scipy.interpolate",
    "numpy",
]

block_cipher = None

a = Analysis(
    [str(project_root / "app.py")],
    pathex=[str(project_root)],
    binaries=cantera_binaries,
    datas=analysis_datas,
    hiddenimports=analysis_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "matplotlib",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "tkinter",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebChannel",
        "PyQt5.QtQuick",
        "PyQt5.QtQuickWidgets",
        "PyQt5.QtQml",
        "PyQt5.QtPositioning",
        "PySide2",
        "PySide6",
        "PyQt6",
        "dask",
        "distributed",
        "pyarrow",
        "bokeh",
        "panel",
        "plotly",
        "skimage",
        "astropy",
        "xarray",
        "torch",
        "cupy",
    ],
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
    name="StanThrust",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_path) if icon_path.exists() else None,
    version=None,
)
