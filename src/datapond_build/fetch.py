"""Streaming downloads and zip extraction, shared by the build scripts."""

from __future__ import annotations

import json
import os
import subprocess
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path

import requests

CHUNK = 1 << 20  # 1 MiB


def remote_identity(url: str, timeout: int = 30, headers: dict | None = None) -> dict:
    """ETag, Content-Length and Last-Modified of ``url`` (HEAD, redirects followed); {} on failure."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        r.raise_for_status()
    except requests.RequestException:
        return {}
    h = r.headers
    out = {}
    if h.get("etag"):
        out["etag"] = h["etag"].strip('"').replace("W/", "")
    if h.get("content-length"):
        try:
            out["size"] = int(h["content-length"])
        except ValueError:
            pass
    if h.get("last-modified"):
        out["last_modified"] = h["last-modified"]
    return out


def _meta_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".meta.json")


def read_download_meta(dest: Path | str) -> dict:
    """The identity recorded when ``dest`` was downloaded ({} if none)."""
    try:
        return json.loads(_meta_path(Path(dest)).read_text())
    except (OSError, ValueError):
        return {}


def source_changed(url: str, dest: Path | str, *, headers: dict | None = None) -> bool | None:
    """True/False when the remote file differs from / matches the identity recorded for ``dest``;
    None when there is nothing to compare (no local record, or the server sends no identity)."""
    local = read_download_meta(dest)
    remote = remote_identity(url, headers=headers)
    for key in ("etag", "last_modified", "size"):
        if local.get(key) and remote.get(key):
            return local[key] != remote[key]
    return None


def download_file(url: str, dest: Path | str, *, desc: str = "", retries: int = 3,
                  timeout: int = 120, skip_if_exists: bool = True, refresh: str = "skip",
                  headers: dict | None = None, quiet: bool = False) -> bool:
    """Download ``url`` to ``dest``. Returns True on success (or when nothing had to be
    downloaded), False after ``retries`` failures.

    ``refresh`` decides what to do when ``dest`` already exists:
      ``"skip"``        keep it (resume semantics; the historical ``skip_if_exists=True``)
      ``"if-changed"``  HEAD the URL and re-download only when its ETag / Last-Modified /
                        size differs from ``<dest>.meta.json`` recorded at download time
                        (re-downloads when there is nothing to compare)
      ``"always"``      re-download
    ``skip_if_exists=False`` is the same as ``refresh="always"``.

    Writes to ``<dest>.part`` and replaces the destination atomically on completion
    (``os.replace``, so an existing file is replaced on Windows too) after verifying
    Content-Length when the server sends one. The old file survives every failure.
    """
    dest = Path(dest)
    if not skip_if_exists and refresh == "skip":
        refresh = "always"
    if dest.exists() and dest.stat().st_size > 0:
        if refresh == "skip":
            return True
        if refresh == "if-changed":
            changed = source_changed(url, dest, headers=headers)
            if changed is False:
                if not quiet:
                    print(f"  {desc or dest.name}: unchanged at the source")
                return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    label = desc or dest.name
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                identity = {}
                if r.headers.get("etag"):
                    identity["etag"] = r.headers["etag"].strip('"').replace("W/", "")
                if r.headers.get("last-modified"):
                    identity["last_modified"] = r.headers["last-modified"]
                received = 0
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(CHUNK):
                        fh.write(chunk)
                        received += len(chunk)
                if total and received != total:
                    raise IOError(f"truncated: {received}/{total} bytes")
            identity["size"] = received
            identity["url"] = url
            identity["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            os.replace(part, dest)
            _meta_path(dest).write_text(json.dumps(identity, indent=1))
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
