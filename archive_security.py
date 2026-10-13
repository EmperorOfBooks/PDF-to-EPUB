"""Archive security utilities: Zip Slip prevention and decompression bomb defense."""

from __future__ import annotations

import os
from pathlib import Path
import zipfile

MAX_TOTAL_UNCOMPRESSED = 250 * 1024 * 1024  # 250 MB total
MAX_ENTRY_UNCOMPRESSED = 50 * 1024 * 1024   # 50 MB per individual entry
MAX_COMPRESSION_RATIO = 100.0               # 100:1 ratio
CHUNK_SIZE = 64 * 1024                      # 64 KB bounded reads


class ArchiveSecurityError(ValueError):
    """Raised when an archive fails security checks (Zip Slip, zip bomb, traversal)."""
    pass


def validate_archive_entry_path(entry_name: str, dest_dir: str | Path | None = None) -> str:
    """Validate entry path against directory traversal (Zip Slip)."""
    if not entry_name or entry_name.startswith(("/", "\\")):
        raise ArchiveSecurityError(f"Entry contains absolute path: {entry_name}")

    normalized = entry_name.replace("\\", "/")
    if (len(normalized) >= 2 and normalized[1] == ":") or normalized.startswith("/"):
        raise ArchiveSecurityError(f"Entry contains drive or root specifier: {entry_name}")

    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        raise ArchiveSecurityError(f"Entry contains parent directory markers: {entry_name}")

    if dest_dir is not None:
        dest_dir_abs = os.path.abspath(str(dest_dir))
        target_path = os.path.abspath(os.path.join(dest_dir_abs, entry_name))
        if os.path.commonpath([dest_dir_abs, target_path]) != dest_dir_abs:
            raise ArchiveSecurityError(f"Entry target path traverses outside destination directory: {entry_name}")
        return target_path

    return entry_name


def validate_zip_archive(archive_path: str | Path) -> None:
    """Audit zip archive headers and bounded contents against bombs and traversal."""
    path = Path(archive_path)
    if not path.exists() or not zipfile.is_zipfile(path):
        return

    with zipfile.ZipFile(path, "r") as archive:
        total_size = 0
        for info in archive.infolist():
            validate_archive_entry_path(info.filename)
            if info.is_dir():
                continue

            if info.file_size > MAX_ENTRY_UNCOMPRESSED:
                raise ArchiveSecurityError(
                    f"Entry '{info.filename}' uncompressed size {info.file_size} exceeds {MAX_ENTRY_UNCOMPRESSED} bytes"
                )

            if info.compress_size > 0 and info.file_size > 1024:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ArchiveSecurityError(
                        f"Entry '{info.filename}' compression ratio {ratio:.1f} exceeds {MAX_COMPRESSION_RATIO}"
                    )

            total_size += info.file_size
            if total_size > MAX_TOTAL_UNCOMPRESSED:
                raise ArchiveSecurityError(
                    f"Archive total uncompressed size exceeds {MAX_TOTAL_UNCOMPRESSED} bytes"
                )

            # Read bounded stream to verify against deceptive headers
            with archive.open(info) as stream:
                entry_read = 0
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    entry_read += len(chunk)
                    if entry_read > MAX_ENTRY_UNCOMPRESSED:
                        raise ArchiveSecurityError(f"Entry exceeded size limit during decompression: {info.filename}")


def safe_extract_archive(archive: zipfile.ZipFile, dest_dir: str | Path) -> None:
    """Safely extract all archive entries to dest_dir with Zip Slip and bomb defenses."""
    dest_dir_abs = os.path.abspath(str(dest_dir))
    os.makedirs(dest_dir_abs, exist_ok=True)
    total_uncompressed = 0

    for info in archive.infolist():
        target_path = validate_archive_entry_path(info.filename, dest_dir_abs)
        if info.is_dir() or info.filename.endswith(("/", "\\")):
            os.makedirs(target_path, exist_ok=True)
            continue

        if info.file_size > MAX_ENTRY_UNCOMPRESSED:
            raise ArchiveSecurityError(f"Entry size exceeds limit: {info.filename}")

        if info.compress_size > 0 and info.file_size > 1024:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise ArchiveSecurityError(f"Entry ratio exceeds limit: {info.filename}")

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        entry_uncompressed = 0
        with archive.open(info) as src, open(target_path, "wb") as dst:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                entry_uncompressed += len(chunk)
                total_uncompressed += len(chunk)
                if entry_uncompressed > MAX_ENTRY_UNCOMPRESSED:
                    raise ArchiveSecurityError(f"Entry '{info.filename}' exceeded uncompressed size quota")
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                    raise ArchiveSecurityError("Archive exceeded total uncompressed size quota")
                dst.write(chunk)

