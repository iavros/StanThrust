#!/usr/bin/env python
"""Build the StanThrust desktop app into a Windows .exe.

This must be run on Windows. PyInstaller builds are platform-specific, so the
macOS `StanThrust.app` cannot be reused as a Windows executable.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "StanThrust"
ENTRY_POINT = PROJECT_ROOT / "app.py"
SPEC_FILE = PROJECT_ROOT / "packaging" / "windows" / f"{APP_NAME}.spec"
DIST_DIR = PROJECT_ROOT / "dist" / "windows"
BUILD_DIR = PROJECT_ROOT / "build" / "windows"
ASSET_DIR = PROJECT_ROOT / "assets"
ICON_FILE = ASSET_DIR / "app_icon.ico"
LOGO_PNG = ASSET_DIR / "Logo.png"


def _print_header(message: str) -> None:
    print("\n" + "=" * 72)
    print(message)
    print("=" * 72)


def clean_build_artifacts() -> None:
    """Remove stale Windows build output before rebuilding."""
    for path in (BUILD_DIR, DIST_DIR / f"{APP_NAME}.exe"):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()


def validate_project() -> None:
    """Ensure the build is being run from the project root on Windows."""
    if sys.platform != "win32":
        raise SystemExit("ERROR: Windows executables must be built on Windows.")
    if not ENTRY_POINT.exists():
        raise SystemExit("ERROR: app.py not found. Run this script from the project root.")
    if not SPEC_FILE.exists():
        raise SystemExit(f"ERROR: {SPEC_FILE.name} not found. Keep the spec file tracked with the repo.")


def ensure_icon_asset() -> Optional[Path]:
    """Return a usable ICO path for PyInstaller, generating one from Logo.png when possible."""
    if ICON_FILE.exists():
        return ICON_FILE
    if not LOGO_PNG.exists():
        return None
    try:
        from PIL import Image

        img = Image.open(str(LOGO_PNG)).convert("RGBA")
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (40, 40), (32, 32), (24, 24), (16, 16)]
        img.save(str(ICON_FILE), format="ICO", sizes=sizes)
        print(f"Generated icon: {ICON_FILE}")
        return ICON_FILE
    except Exception as exc:
        print(f"Icon generation failed; continuing without custom icon. Reason: {exc}")
        return None


def run_pyinstaller() -> int:
    """Invoke PyInstaller using the tracked Windows spec file."""
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
    cache_dir = BUILD_DIR / "pyinstaller_config"
    mpl_cache_dir = BUILD_DIR / "matplotlib_config"
    cache_dir.mkdir(parents=True, exist_ok=True)
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    env["PYINSTALLER_CONFIG_DIR"] = str(cache_dir)
    env["MPLCONFIGDIR"] = str(mpl_cache_dir)

    _print_header(f"Building {APP_NAME} for Windows with PyInstaller")
    print(f"Spec file: {SPEC_FILE}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"ICO asset: {icon_path if icon_path else 'not available; EXE will use default icon'}")
    print(f"Output executable: {DIST_DIR / f'{APP_NAME}.exe'}")
    print(f"PyInstaller cache: {cache_dir}")
    print("Command:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    return subprocess.run(command, cwd=str(PROJECT_ROOT), env=env).returncode


def report_success() -> None:
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    _print_header("Build successful")
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"Executable: {exe_path}")
        print(f"Size: {size_mb:.1f} MB")
        print(f"Bundled ICO source: {ICON_FILE if ICON_FILE.exists() else 'not present'}")
    else:
        print(f"Expected executable not found at {exe_path}")


def validate_executable() -> None:
    """Verify that the bundled Qt and Matplotlib runtime can render offscreen."""
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    trace_path = BUILD_DIR / "desktop-self-test.log"
    if trace_path.exists():
        trace_path.unlink()
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["STANTHRUST_SELF_TEST_LOG"] = str(trace_path)
    _print_header("Validating bundled desktop runtime")
    process = subprocess.Popen([str(exe_path), "--self-test-desktop"], env=env)
    deadline = time.monotonic() + 180
    trace = ""
    while time.monotonic() < deadline:
        trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        if "field-rendered" in trace:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process.wait(timeout=30)
            print("Bundled Qt and Matplotlib render test passed.")
            return
        return_code = process.poll()
        if return_code is not None:
            stages = trace.strip() or "no trace written"
            raise SystemExit(
                f"ERROR: bundled desktop self-test failed with exit code {return_code}. Last stages: {stages}"
            )
        time.sleep(0.25)

    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stages = trace.strip() or "no trace written"
    raise SystemExit(f"ERROR: bundled desktop self-test timed out. Last stages: {stages}")


def main() -> int:
    validate_project()
    clean_build_artifacts()
    result = run_pyinstaller()
    if result == 0:
        report_success()
        validate_executable()
    else:
        _print_header("Build failed")
    return result


if __name__ == "__main__":
    sys.exit(main())
