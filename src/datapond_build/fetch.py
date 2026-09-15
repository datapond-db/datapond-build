"""Streaming downloads and zip extraction, shared by the build scripts."""

from __future__ import annotations

import subprocess
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path

import requests

CHUNK = 1 << 20  # 1 MiB


def download_file(url: str, dest: Path | str, *, desc: str = "", retries: int = 3,
                  timeout: int = 120, skip_if_exists: bool = True,
                  headers: dict | None = None, quiet: bool = False) -> bool:
    """Download ``url`` to ``dest``. Returns True on success, False after ``retries``.

    Writes to ``<dest>.part`` and renames on completion so a truncated file is
    never mistaken for a finished download. Verifies Content-Length when the
    server sends one.
    """
    dest = Path(dest)
    if skip_if_exists and dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    label = desc or dest.name
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                received = 0
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(CHUNK):
                        fh.write(chunk)
                        received += len(chunk)
                if total and received != total:
                    raise IOError(f"truncated: {received}/{total} bytes")
            part.rename(dest)
            if not quiet:
                print(f"  downloaded {label} ({received / 1e6:.1f} MB)")
            return True
        except Exception as e:  # noqa: BLE001 - report and retry
            print(f"  WARNING: download attempt {attempt}/{retries} for {label}: {e}")
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return False


def remote_last_modified(url: str, timeout: int = 30) -> str | None:
    """The Last-Modified header of ``url`` (HEAD request), or None."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.headers.get("last-modified")
    except requests.RequestException:
        return None


def extract_zip(zip_path: Path | str, dest_dir: Path | str, *,
                members: Iterable[str] | None = None, fallback_unzip: bool = True) -> Path:
    """Extract ``zip_path`` into ``dest_dir`` (optionally only ``members``).

    Some government zips (EOIR's, notably) trip Python's zipfile; when that
    happens and ``fallback_unzip`` is set, the system ``unzip`` is used.
    """
    zip_path, dest_dir = Path(zip_path), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = list(members) if members else zf.namelist()
            for name in names:
                zf.extract(name, dest_dir)
        return dest_dir
    except (zipfile.BadZipFile, NotImplementedError, OSError) as e:
        if not fallback_unzip:
            raise
        print(f"  zipfile failed ({e}); falling back to unzip")
        cmd = ["unzip", "-o", "-q", str(zip_path), "-d", str(dest_dir)]
        if members:
            cmd[3:3] = list(members)
        subprocess.run(cmd, check=True)
        return dest_dir
