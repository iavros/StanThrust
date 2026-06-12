#!/usr/bin/env python
"""Build the StanThrust desktop app into a native macOS .app bundle.

This must be run on macOS. PyInstaller builds are platform-specific, so the
Windows `StanThrust.exe` cannot be reused as a macOS application.
"""

import os
import shutil
import site
import subprocess
import sys
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "StanThrust"
ENTRY_POINT = PROJECT_ROOT / "app.py"
SPEC_FILE = PROJECT_ROOT / f"{APP_NAME}_macos.spec"
DIST_ROOT = PROJECT_ROOT / "dist"
DIST_DIR = DIST_ROOT / "macos"
BUILD_DIR = PROJECT_ROOT / "build" / "macos"
ICON_FILE = PROJECT_ROOT / "app_icon.icns"
LOGO_PNG = PROJECT_ROOT / "Logo.png"


def _print_header(message: str) -> None:
    print("\n" + "=" * 72)
    print(message)
    print("=" * 72)


def clean_build_artifacts() -> None:
    """Remove stale macOS build output before rebuilding."""
    for path in (BUILD_DIR, DIST_DIR / f"{APP_NAME}.app", DIST_DIR / APP_NAME):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()


def validate_project() -> None:
    """Ensure the build is being run from the project root on macOS."""
    if sys.platform != "darwin":
        raise SystemExit("ERROR: macOS app bundles must be built on macOS.")
    if not ENTRY_POINT.exists():
        raise SystemExit("ERROR: app.py not found. Run this script from the project root.")
    if not SPEC_FILE.exists():
        raise SystemExit(f"ERROR: {SPEC_FILE.name} not found. Keep the spec file tracked with the repo.")


def ensure_icon_asset() -> Optional[Path]:
    """Return a usable ICNS path for PyInstaller, generating one from Logo.png when possible."""
    if ICON_FILE.exists():
        return ICON_FILE
    if not LOGO_PNG.exists():
        return None

    try:
        from PIL import Image

        img = Image.open(str(LOGO_PNG)).convert("RGBA")
        img.save(
            str(ICON_FILE),
            format="ICNS",
            sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)],
        )
        print(f"Generated icon: {ICON_FILE}")
        return ICON_FILE
    except Exception as exc:
        print(f"Icon generation failed; continuing without custom icon. Reason: {exc}")
        return None


def run_pyinstaller() -> int:
    """Invoke PyInstaller using the tracked macOS spec file."""
    icon_path = ensure_icon_asset()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--workpath",
        str(BUILD_DIR),
        "--distpath",
        str(DIST_DIR),
        str(SPEC_FILE),
    ]
    env = dict(os.environ)
    try:
        user_site = site.getusersitepackages()
    except Exception:
        user_site = ""
    if user_site:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = user_site if not existing else user_site + os.pathsep + existing
    cache_dir = BUILD_DIR / "pyinstaller_config"
    mpl_cache_dir = BUILD_DIR / "matplotlib_config"
    cache_dir.mkdir(parents=True, exist_ok=True)
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    env["PYINSTALLER_CONFIG_DIR"] = str(cache_dir)
    env["MPLCONFIGDIR"] = str(mpl_cache_dir)

    _print_header(f"Building {APP_NAME} for macOS with PyInstaller")
    print(f"Spec file: {SPEC_FILE}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"ICNS asset: {icon_path if icon_path else 'not available; app will use default icon'}")
    print(f"Output app: {DIST_DIR / f'{APP_NAME}.app'}")
    print(f"PyInstaller cache: {cache_dir}")
    print(f"User site: {user_site or 'not detected'}")
    print("Command:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    return subprocess.run(command, cwd=str(PROJECT_ROOT), env=env).returncode


def report_success() -> None:
    app_path = DIST_DIR / f"{APP_NAME}.app"
    _print_header("Build successful")
    if app_path.exists():
        print(f"Application bundle: {app_path}")
        print("Open it from Finder or run:")
        print(f"open {app_path}")
    else:
        print(f"Expected app bundle not found at {app_path}")


def main() -> int:
    validate_project()
    clean_build_artifacts()
    result = run_pyinstaller()
    if result == 0:
        report_success()
    else:
        _print_header("Build failed")
    return result


if __name__ == "__main__":
    sys.exit(main())
