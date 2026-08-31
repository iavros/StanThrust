#!/usr/bin/env python
"""Build a shareable macOS DMG for StanThrust.

This must be run on macOS. The script builds the PyInstaller .app bundle and
wraps it in a DMG that users can open and drag into Applications.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stanthrust import __version__ as APP_VERSION

APP_NAME = "StanThrust"
DIST_ROOT = PROJECT_ROOT / "dist"
MACOS_DIST = DIST_ROOT / "macos"
APP_PATH = MACOS_DIST / f"{APP_NAME}.app"
INSTALLER_BUILD_DIR = PROJECT_ROOT / "build" / "macos_installer"
INSTALLER_OUTPUT_DIR = DIST_ROOT / "installer"
DMG_PATH = INSTALLER_OUTPUT_DIR / f"{APP_NAME}-macOS.dmg"


def _print_header(message: str) -> None:
    print("\n" + "=" * 72)
    print(message)
    print("=" * 72)


def _run(command: list[str]) -> None:
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _clean_outputs() -> None:
    if INSTALLER_BUILD_DIR.exists():
        shutil.rmtree(INSTALLER_BUILD_DIR)
    INSTALLER_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if DMG_PATH.exists():
        DMG_PATH.unlink()


def _build_app_bundle() -> None:
    _print_header("Building macOS app bundle")
    _run([sys.executable, str(PROJECT_ROOT / "packaging" / "macos" / "build_app.py")])
    if not APP_PATH.exists():
        raise SystemExit(f"Expected app bundle was not found: {APP_PATH}")


def _stage_dmg_contents() -> Path:
    staging_dir = INSTALLER_BUILD_DIR / f"{APP_NAME}-dmg"
    staging_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(APP_PATH, staging_dir / APP_PATH.name, symlinks=True)
    applications_link = staging_dir / "Applications"
    if not applications_link.exists():
        os.symlink("/Applications", applications_link)
    (staging_dir / "UPDATE_NOTES.txt").write_text(
        (
            f"{APP_NAME} {APP_VERSION}\n\n"
            "To install an update, drag StanThrust.app into Applications and choose Replace when macOS asks.\n"
            "The app bundle identifier is edu.stanford.stanthrust, so replacements target the same installed app.\n"
        ),
        encoding="utf-8",
    )
    return staging_dir


def _build_dmg(staging_dir: Path) -> None:
    _print_header("Building macOS DMG")
    command = [
        "hdiutil",
        "create",
        "-volname",
        APP_NAME,
        "-srcfolder",
        str(staging_dir),
        "-ov",
        "-format",
        "UDZO",
        str(DMG_PATH),
    ]
    for attempt in range(1, 5):
        print(" ".join(f'"{part}"' if " " in part else part for part in command))
        completed = subprocess.run(command, cwd=str(PROJECT_ROOT))
        if completed.returncode == 0 and DMG_PATH.exists():
            return
        if DMG_PATH.exists():
            DMG_PATH.unlink()
        if attempt < 4:
            delay_seconds = 5 * attempt
            print(f"hdiutil create failed; retrying in {delay_seconds} seconds.")
            time.sleep(delay_seconds)
    raise SystemExit(f"Expected DMG was not created: {DMG_PATH}")


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("macOS DMG installers must be built on macOS.")
    _clean_outputs()
    _build_app_bundle()
    staging_dir = _stage_dmg_contents()
    _build_dmg(staging_dir)
    _print_header("macOS distribution artifact ready")
    print(DMG_PATH)
    print("Send this DMG to macOS users. They do not need Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
