"""Download the EPUBCheck release archive selected from GitHub release metadata."""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

from archive_security import safe_extract_archive

RELEASE_API = "https://api.github.com/repos/w3c/epubcheck/releases/latest"
ASSET_PATTERN = "epubcheck-*.zip"


def select_epubcheck_asset(assets: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        asset for asset in assets
        if isinstance(asset.get("name"), str)
        and fnmatch.fnmatchcase(asset["name"].lower(), ASSET_PATTERN)
        and asset.get("browser_download_url")
    ]
    if not matches:
        raise ValueError(f"No release asset matches {ASSET_PATTERN}")
    return sorted(matches, key=lambda asset: asset["name"].lower())[-1]


def ensure_epubcheck(destination: str | Path) -> Path:
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing = next(root.rglob("epubcheck.jar"), None)
    if existing is not None:
        return existing
    request = urllib.request.Request(RELEASE_API, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    asset = select_epubcheck_asset(release.get("assets", []))
    archive_path = root / asset["name"]
    urllib.request.urlretrieve(asset["browser_download_url"], archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        safe_extract_archive(archive, root)
    archive_path.unlink()
    jar = next(root.rglob("epubcheck.jar"), None)
    if jar is None:
        raise FileNotFoundError("The selected EPUBCheck release contained no epubcheck.jar")
    return jar


def run_epubcheck(jar_path: str | Path, epub_path: str | Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Execute epubcheck via subprocess with shell=False, sanitized UUID temp path, and strict timeout."""
    jar = Path(jar_path).resolve()
    target_epub = Path(epub_path).resolve()
    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_copy = Path(tmp_dir) / f"{uuid.uuid4().hex}.epub"
        shutil.copy2(target_epub, safe_copy)
        cmd = ["java", "-jar", str(jar), str(safe_copy)]
        return subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=timeout)
