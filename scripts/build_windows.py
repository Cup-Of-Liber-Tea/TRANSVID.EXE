from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import tomllib


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
APP_ENTRYPOINT = PROJECT_ROOT / "app.py"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
APP_NAME = "TRANSVID"
SPEC_PATH = PROJECT_ROOT / f"{APP_NAME}.spec"


def load_project_version() -> str:
    with PYPROJECT_PATH.open("rb") as file_handle:
        pyproject_data = tomllib.load(file_handle)
    return str(pyproject_data["project"]["version"])


def remove_previous_outputs() -> None:
    for path in (BUILD_DIR, DIST_DIR, ARTIFACTS_DIR):
        if path.exists():
            shutil.rmtree(path)
    if SPEC_PATH.exists():
        SPEC_PATH.unlink()


def run_pyinstaller() -> tuple[Path, Path]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--specpath",
        str(BUILD_DIR),
        "--name",
        APP_NAME,
        str(APP_ENTRYPOINT),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    app_dir = DIST_DIR / APP_NAME
    exe_path = app_dir / f"{APP_NAME}.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"빌드 산출물을 찾지 못했습니다: {exe_path}")
    return app_dir, exe_path


def package_zip(app_dir: Path, version: str) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARTIFACTS_DIR / f"{APP_NAME}-{version}-windows-x64.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as zip_file:
        for file_path in app_dir.rglob("*"):
            if file_path.is_file():
                zip_file.write(file_path, arcname=file_path.relative_to(DIST_DIR))
    return archive_path


def main() -> None:
    version = load_project_version()
    remove_previous_outputs()
    app_dir, exe_path = run_pyinstaller()
    archive_path = package_zip(app_dir, version)
    print(f"Built app directory: {app_dir}")
    print(f"Built EXE: {exe_path}")
    print(f"Release archive: {archive_path}")


if __name__ == "__main__":
    main()
