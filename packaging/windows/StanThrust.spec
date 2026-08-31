# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import importlib
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

def write_version_resource(destination, version):
    """Write a PyInstaller version-info file so the .exe reports its version.

    Windows shows these fields in the file properties dialog, and code-signing
    and reputation tooling expects them to be present.
    """
    parts = [int(piece) for piece in version.split(".") if piece.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    numbers = tuple(parts[:4])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'StanThrust'),
        StringStruct('FileDescription', 'StanThrust liquid engine sizing'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'StanThrust'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'StanThrust.exe'),
        StringStruct('ProductName', 'StanThrust'),
        StringStruct('ProductVersion', '{version}'),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""",
        encoding="utf-8",
    )
    return destination


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
icon_path = asset_dir / "app_icon.ico"
cantera_datas = collect_data_files("cantera")
cantera_binaries = collect_dynamic_libs("cantera")
coolprop_datas = collect_data_files("CoolProp")
coolprop_binaries = collect_dynamic_libs("CoolProp")
vendored_binaries = collect_delvewheel_libs("cantera", "CoolProp")
version_resource = write_version_resource(
    project_root / "build" / "windows" / "version_info.txt", APP_VERSION
)

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
    version=str(version_resource),
)
