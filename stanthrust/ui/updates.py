"""GitHub Releases update check for the packaged desktop application."""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

from stanthrust import __version__ as APP_VERSION
from stanthrust.ui.formatting import is_newer_version

RELEASE_API_URL = "https://api.github.com/repos/iavros/StanThrust/releases"
RELEASES_PAGE_URL = "https://github.com/iavros/StanThrust/releases"

_USER_AGENT = "StanThrust/{0}".format(APP_VERSION)
_METADATA_TIMEOUT_SECONDS = 15
_DOWNLOAD_TIMEOUT_SECONDS = 90

#: Installer asset name fragments, most preferred first, keyed by platform.
_ASSET_PREFERENCES = {
    "win32": ("installer.exe", ".msi", "windows-portable.zip", ".zip"),
    "darwin": (".dmg", ".pkg", ".zip"),
}
_DEFAULT_ASSET_PREFERENCES = (".zip", ".tar.gz", ".tgz")


def fetch_latest_release() -> Dict[str, object]:
    """Return the newest non-draft release payload from GitHub."""
    request = urllib.request.Request(
        RELEASE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=_METADATA_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        for release in payload:
            if isinstance(release, dict) and not release.get("draft"):
                return dict(release)
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def select_release_asset(release: Dict[str, object]) -> Optional[Dict[str, object]]:
    """Pick the installer asset that matches the running platform."""
    assets: List[Dict[str, object]] = [
        dict(asset) for asset in release.get("assets", []) if isinstance(asset, dict)
    ]
    preferences = _ASSET_PREFERENCES.get(sys.platform, _DEFAULT_ASSET_PREFERENCES)
    for marker in preferences:
        for asset in assets:
            name = str(asset.get("name") or "").lower()
            if marker in name and asset.get("browser_download_url"):
                return asset
    return None


def download_release_asset(asset: Dict[str, object]) -> Path:
    """Download an asset into the user's Downloads folder without overwriting."""
    url = str(asset.get("browser_download_url") or "")
    if not url:
        raise ValueError("Release asset is missing a download URL.")
    downloads_dir = Path.home() / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / Path(str(asset.get("name") or "StanThrust-update")).name
    stem = target.stem
    suffix = "".join(target.suffixes)
    counter = 1
    while target.exists():
        target = downloads_dir / "{0}-{1}{2}".format(stem, counter, suffix)
        counter += 1
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        with target.open("wb") as output:
            shutil.copyfileobj(response, output)
    return target


def reveal_in_file_manager(path: Path) -> None:
    """Open the folder containing ``path`` in the platform file manager."""
    folder = path.parent
    if sys.platform == "win32":
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
            return
        except OSError:
            pass
    webbrowser.open(folder.as_uri())


def check_for_update(parent: QWidget) -> str:
    """Run the interactive update check and return a log line describing it."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        release = fetch_latest_release()
        latest_tag = str(release.get("tag_name") or release.get("name") or "").strip()
        latest_version = latest_tag.lstrip("vV")
        if not latest_version:
            QMessageBox.information(parent, "Update Check", "No release version was found.")
            return "Update check found no published release."
        if not is_newer_version(latest_version, APP_VERSION):
            QMessageBox.information(
                parent,
                "StanThrust Is Up To Date",
                "Installed version: {0}\nLatest release: {1}".format(APP_VERSION, latest_tag),
            )
            return "Update check: {0} is current.".format(APP_VERSION)

        asset = select_release_asset(release)
        release_url = str(release.get("html_url") or RELEASES_PAGE_URL)
        if not asset:
            answer = QMessageBox.question(
                parent,
                "Update Available",
                "StanThrust {0} is available, but no installer asset matches this "
                "platform.\n\nOpen the release page?".format(latest_tag),
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                webbrowser.open(release_url)
            return "Update {0} available with no matching installer asset.".format(latest_tag)

        asset_name = str(asset.get("name") or "StanThrust update")
        answer = QMessageBox.question(
            parent,
            "Update Available",
            "StanThrust {0} is available.\n\nDownload {1} to your Downloads folder?".format(
                latest_tag, asset_name
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return "Update {0} available; download declined.".format(latest_tag)

        downloaded_path = download_release_asset(asset)
        QMessageBox.information(
            parent,
            "Update Downloaded",
            "Downloaded:\n{0}\n\nRun the installer to update StanThrust.".format(downloaded_path),
        )
        reveal_in_file_manager(downloaded_path)
        return "Downloaded update installer: {0}".format(downloaded_path)
    except (urllib.error.URLError, TimeoutError) as exc:
        QMessageBox.warning(
            parent, "Update Check Failed", "Could not reach GitHub Releases.\n\n{0}".format(exc)
        )
        return "Update check could not reach GitHub Releases: {0}".format(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user in a dialog
        QMessageBox.warning(
            parent,
            "Update Check Failed",
            "StanThrust could not complete the update check.\n\n{0}".format(exc),
        )
        return "Update check failed: {0}".format(exc)
    finally:
        QApplication.restoreOverrideCursor()
