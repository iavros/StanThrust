# Packaging

Native installers are built on the same operating system they target.

- `windows/`: PyInstaller executable build and Windows installer wrapper.
- `macos/`: PyInstaller app bundle build and DMG wrapper.

Common commands from the repository root:

```powershell
python packaging\windows\build_installer.py
```

```bash
python packaging/macos/build_installer.py
```

The GitHub Actions workflow at `.github/workflows/package.yml` runs both platform builds on native runners.

