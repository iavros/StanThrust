#!/usr/bin/env python
"""Build a no-Python Windows distribution for StanThrust.

The script first builds the standalone PyInstaller executable. If Inno Setup
or NSIS is available, it then wraps that executable in a normal installer.
If neither installer compiler is installed, it creates a portable ZIP instead.
"""

import os
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "StanThrust"
APP_VERSION = "0.1.0"
INSTALLER_EXE_NAME = f"{APP_NAME}-Installer.exe"
DIST_ROOT = PROJECT_ROOT / "dist"
WINDOWS_DIST = DIST_ROOT / "windows"
EXE_PATH = WINDOWS_DIST / f"{APP_NAME}.exe"
INSTALLER_BUILD_DIR = PROJECT_ROOT / "build" / "installer"
INSTALLER_OUTPUT_DIR = DIST_ROOT / "installer"
ICON_FILE = PROJECT_ROOT / "app_icon.ico"


def _print_header(message: str) -> None:
    print("\n" + "=" * 72)
    print(message)
    print("=" * 72)


def _run(command: list[str]) -> None:
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _find_inno_compiler() -> Optional[Path]:
    candidates = [
        os.environ.get("INNO_SETUP_COMPILER", ""),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if candidate and path.exists():
            return path
    return None


def _find_nsis_compiler() -> Optional[Path]:
    candidates = [
        os.environ.get("NSIS_COMPILER", ""),
        r"C:\Program Files (x86)\NSIS\makensis.exe",
        r"C:\Program Files\NSIS\makensis.exe",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if candidate and path.exists():
            return path
    resolved = shutil.which("makensis")
    return Path(resolved) if resolved else None


def _clean_installer_outputs() -> None:
    INSTALLER_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.iss", "*.nsi", "*.exe", "*.zip"):
        for path in INSTALLER_OUTPUT_DIR.glob(pattern):
            path.unlink()


def _build_standalone_exe() -> None:
    _print_header("Building standalone Windows executable")
    _run([sys.executable, str(PROJECT_ROOT / "build_windows_app.py")])
    if not EXE_PATH.exists():
        raise SystemExit(f"Expected PyInstaller output was not found: {EXE_PATH}")


def _write_inno_script() -> Path:
    script_path = INSTALLER_BUILD_DIR / f"{APP_NAME}.iss"
    icon_line = f'SetupIconFile="{ICON_FILE}"' if ICON_FILE.exists() else ""
    script_path.write_text(
        textwrap.dedent(
            f"""
            #define MyAppName "{APP_NAME}"
            #define MyAppVersion "{APP_VERSION}"
            #define MyAppExeName "{APP_NAME}.exe"

            [Setup]
            AppId={{{{9B7D66F5-61D7-4BD3-A3D3-70E73F79FD0B}}}}
            AppName={{#MyAppName}}
            AppVersion={{#MyAppVersion}}
            AppPublisher=StanThrust
            DefaultDirName={{autopf}}\\{{#MyAppName}}
            DefaultGroupName={{#MyAppName}}
            DisableProgramGroupPage=yes
            OutputDir="{INSTALLER_OUTPUT_DIR}"
            OutputBaseFilename={Path(INSTALLER_EXE_NAME).stem}
            Compression=lzma
            SolidCompression=yes
            WizardStyle=modern
            ArchitecturesAllowed=x64compatible
            ArchitecturesInstallIn64BitMode=x64compatible
            {icon_line}

            [Files]
            Source: "{EXE_PATH}"; DestDir: "{{app}}"; Flags: ignoreversion

            [Icons]
            Name: "{{autoprograms}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
            Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

            [Tasks]
            Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

            [Run]
            Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Launch {{#MyAppName}}"; Flags: nowait postinstall skipifsilent
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return script_path


def _write_nsis_script() -> Path:
    script_path = INSTALLER_BUILD_DIR / f"{APP_NAME}.nsi"
    icon_line = f'Icon "{ICON_FILE}"' if ICON_FILE.exists() else ""
    script_path.write_text(
        textwrap.dedent(
            f"""
            Unicode true
            Name "{APP_NAME}"
            OutFile "{INSTALLER_OUTPUT_DIR}\\{INSTALLER_EXE_NAME}"
            InstallDir "$PROGRAMFILES64\\{APP_NAME}"
            RequestExecutionLevel admin
            {icon_line}

            Page directory
            Page instfiles
            UninstPage uninstConfirm
            UninstPage instfiles

            Section "Install"
                SetOutPath "$INSTDIR"
                File "{EXE_PATH}"
                CreateShortcut "$SMPROGRAMS\\{APP_NAME}.lnk" "$INSTDIR\\{APP_NAME}.exe"
                CreateShortcut "$DESKTOP\\{APP_NAME}.lnk" "$INSTDIR\\{APP_NAME}.exe"
                WriteUninstaller "$INSTDIR\\Uninstall.exe"
            SectionEnd

            Section "Uninstall"
                Delete "$INSTDIR\\{APP_NAME}.exe"
                Delete "$INSTDIR\\Uninstall.exe"
                Delete "$SMPROGRAMS\\{APP_NAME}.lnk"
                Delete "$DESKTOP\\{APP_NAME}.lnk"
                RMDir "$INSTDIR"
            SectionEnd
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return script_path


def _build_inno_installer(compiler: Path) -> Path:
    script_path = _write_inno_script()
    _print_header("Building installer with Inno Setup")
    _run([str(compiler), str(script_path)])
    artifact = INSTALLER_OUTPUT_DIR / INSTALLER_EXE_NAME
    if not artifact.exists():
        raise SystemExit(f"Expected installer was not found: {artifact}")
    return artifact


def _build_nsis_installer(compiler: Path) -> Path:
    script_path = _write_nsis_script()
    _print_header("Building installer with NSIS")
    _run([str(compiler), str(script_path)])
    artifact = INSTALLER_OUTPUT_DIR / INSTALLER_EXE_NAME
    if not artifact.exists():
        raise SystemExit(f"Expected installer was not found: {artifact}")
    return artifact


def _build_portable_zip() -> Path:
    zip_path = INSTALLER_OUTPUT_DIR / f"{APP_NAME}-windows-portable.zip"
    _print_header("Creating portable ZIP fallback")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(EXE_PATH, f"{APP_NAME}/{APP_NAME}.exe")
        if (PROJECT_ROOT / "README.md").exists():
            archive.write(PROJECT_ROOT / "README.md", f"{APP_NAME}/README.md")
    return zip_path


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows installers must be built on Windows.")
    _clean_installer_outputs()
    _build_standalone_exe()

    inno = _find_inno_compiler()
    nsis = _find_nsis_compiler()
    if inno:
        artifact = _build_inno_installer(inno)
    elif nsis:
        artifact = _build_nsis_installer(nsis)
    else:
        artifact = _build_portable_zip()

    _print_header("Distribution artifact ready")
    print(artifact)
    print("Recipients do not need Python; the executable runtime is bundled.")
    if artifact.suffix.lower() == ".zip":
        print("Install Inno Setup or NSIS on the build machine to produce a setup .exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
