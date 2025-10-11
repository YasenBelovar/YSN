# ysn_downloader/downloader.py
"""
Downloader helper for YSN.
- detect_external_downloader(): checks PATH and app folder for aria2c/axel
- ensure_aria2_on_windows(log_fn): if on Windows, optionally download aria2 portable and extract aria2c.exe
This module does not automatically trigger downloads without explicit user consent in the GUI.
"""

import os
import sys
import shutil
import tempfile
import zipfile
import json
import time
from typing import Optional, Callable
from urllib.parse import urljoin

# We import requests lazily to avoid hard dependency unless user uses auto-install
try:
    import requests
except Exception:
    requests = None

GITHUB_API_RELEASES = "https://api.github.com/repos/aria2/aria2/releases/latest"

def detect_external_downloader(preferred=None) -> Optional[str]:
    """
    Detects installed external downloaders. Returns first found name (like 'aria2c' or 'axel'), or None.
    Looks in PATH and next to the running script.
    """
    if preferred is None:
        preferred = ["aria2c", "axel"]
    # search PATH
    for name in preferred:
        exe_name = name + (".exe" if sys.platform.startswith("win") else "")
        if shutil.which(exe_name):
            return exe_name
    # search script dir
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for name in preferred:
        exe_name = name + (".exe" if sys.platform.startswith("win") else "")
        local_path = os.path.join(base_dir, exe_name)
        if os.path.exists(local_path):
            return local_path
    return None


def _download_file(url: str, target_path: str, progress_fn: Optional[Callable[[int, int], None]] = None):
    """
    Simple downloader using requests. progress_fn(received_bytes, total_bytes)
    """
    if requests is None:
        raise RuntimeError("requests module not available (pip install requests)")

    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get('Content-Length') or 0)
        received = 0
        with open(target_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    received += len(chunk)
                    if progress_fn:
                        progress_fn(received, total)


def ensure_aria2_on_windows(log_fn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    On Windows: attempts to download aria2 portable binary from GitHub releases and extract aria2c.exe
    Places aria2c.exe next to the running script (os.path.dirname(sys.argv[0])).
    Returns full path to aria2c.exe on success, None on failure.
    Requires 'requests' library to be installed. This function should be called only if user consented.
    """
    if not sys.platform.startswith("win"):
        if log_fn:
            log_fn("[info] Not Windows platform; skipping aria2 auto-install.")
        return None
    if requests is None:
        if log_fn:
            log_fn("[warn] 'requests' not installed; cannot auto-download aria2. Install via pip install requests")
        return None

    # Check already present
    exe_name = "aria2c.exe"
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    local_path = os.path.join(base_dir, exe_name)
    if os.path.exists(local_path):
        if log_fn:
            log_fn(f"[info] aria2c already exists at {local_path}")
        return local_path

    # Query GitHub API for latest release
    try:
        resp = requests.get(GITHUB_API_RELEASES, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        assets = data.get("assets", [])
        # find Windows (zip) asset: prefer names containing 'win' or 'windows' and 'zip'
        candidate = None
        for a in assets:
            an = a.get("name", "").lower()
            if "win" in an and an.endswith(".zip"):
                candidate = a
                break
        if not candidate:
            # fallback: take first .zip
            for a in assets:
                if a.get("name","").lower().endswith(".zip"):
                    candidate = a
                    break
        if not candidate:
            if log_fn:
                log_fn("[warn] No suitable aria2 Windows zip asset found in release.")
            return None

        download_url = candidate.get("browser_download_url")
        if not download_url:
            if log_fn:
                log_fn("[warn] No download URL for aria2 asset.")
            return None

        # download into temp file
        fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        if log_fn:
            log_fn(f"[info] Downloading aria2 asset {candidate.get('name')} ...")
        _download_file(download_url, tmp_zip, progress_fn=lambda r,t: None)
        # extract aria2c.exe
        with zipfile.ZipFile(tmp_zip, 'r') as z:
            members = z.namelist()
            # find aria2c.exe entry (case-insensitive)
            exe_entry = None
            for m in members:
                if m.lower().endswith("aria2c.exe"):
                    exe_entry = m
                    break
            if not exe_entry:
                if log_fn:
                    log_fn("[warn] aria2c.exe not found inside the downloaded zip.")
                os.remove(tmp_zip)
                return None
            # extract to base_dir
            if log_fn:
                log_fn("[info] Extracting aria2c.exe ...")
            z.extract(exe_entry, base_dir)
            extracted_path = os.path.join(base_dir, exe_entry)
            # if nested dirs, move the exe to base_dir root
            final_path = os.path.join(base_dir, exe_name)
            shutil.move(extracted_path, final_path)
            # cleanup: remove any created directories if empty
            # Note: extraction may create nested dirs; we don't aggressively delete them
        os.remove(tmp_zip)
        if log_fn:
            log_fn(f"[info] aria2c installed to {final_path}")
        return final_path
    except Exception as e:
        if log_fn:
            log_fn(f"[error] Failed to auto-install aria2: {e}")
        return None
