# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import importlib
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

def collect_delvewheel_libs(*distributions):
    """Collect the private DLL folders that delvewheel places beside a package.

    Wheels repaired with delvewheel keep their bundled DLLs in a sibling
    ``<name>.libs`` directory and register it with ``os.add_dll_directory`` from
    the package's ``__init__``. That sibling directory does not exist inside a
    frozen bundle, so the DLLs have to be collected into the bundle root
    explicitly. Without this, importing CoolProp only succeeds when another
    package has already loaded a compatible runtime, which makes the packaged
    build depend on import order.
    """
    collected = []
    for distribution in distributions:
        module = importlib.import_module(distribution)
        libs_dir = Path(module.__file__).resolve().parent.parent / f"{distribution.lower()}.libs"
        if not libs_dir.is_dir():
            continue
        collected.extend((str(dll), ".") for dll in sorted(libs_dir.glob("*.dll")))
    return collected


project_root = Path.cwd()
sys.path.insert(0, str(project_root))
from stanthrust import __version__ as APP_VERSION

asset_dir = project_root / "assets"
icon_path = asset_dir / "app_icon.icns"
cantera_datas = collect_data_files("cantera")
cantera_binaries = collect_dynamic_libs("cantera")
coolprop_datas = collect_data_files("CoolProp")
coolprop_binaries = collect_dynamic_libs("CoolProp")
vendored_binaries = collect_delvewheel_libs("cantera", "CoolProp")

analysis_datas = cantera_datas + coolprop_datas + [
    (str(asset_dir / "Logo.png"), "assets"),
    (str(asset_dir / "Logo.svg"), "assets"),
    (str(project_root / "stanthrust" / "data" / "rocket_mech_equilibrium.yaml"), "stanthrust/data"),
]
if icon_path.exists():
    analysis_datas.append((str(icon_path), "."))

analysis_hiddenimports = [
    *collect_submodules("cantera"),
    *collect_submodules("CoolProp"),
    "cantera",
    "CoolProp",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_qt5agg",
]

a = Analysis(
    [str(project_root / "app.py")],
    pathex=[str(project_root)],
    binaries=cantera_binaries + coolprop_binaries + vendored_binaries,
    datas=analysis_datas,
    hiddenimports=analysis_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
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
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StanThrust",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="StanThrust",
)
app = BUNDLE(
    coll,
    name="StanThrust.app",
    icon=str(icon_path) if icon_path.exists() else None,
    bundle_identifier="edu.stanford.stanthrust",
    info_plist={
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHumanReadableCopyright": "Copyright StanThrust",
    },
)
