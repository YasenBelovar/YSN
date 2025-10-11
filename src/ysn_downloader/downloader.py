# ysn_downloader/downloader.py
"""
Downloader helper for YSN.

Provides:
- detect_external_downloader(preferred=None) -> Optional[str]
- ensure_aria2_on_windows(log_fn=None) -> Optional[str]

Used by ysn.py to detect/use aria2c or axel and to optionally auto-download aria2 on Windows.
"""

import os
import sys
import shutil
import tempfile
import zipfile
import time
from typing import Optional, Callable

# requests used only for auto-download of aria2 on Windows; import lazily
try:
    import requests
except Exception:
    requests = None

GITHUB_API_RELEASES = "https://api.github.com/repos/aria2/aria2/releases/latest"


def detect_external_downloader(preferred=None) -> Optional[str]:
    """
    Detects installed external downloaders. Returns the first found executable name or full path,
    e.g. 'aria2c' or 'aria2c.exe' (on Windows), or 'axel'.

    Search order:
    - Checks items from `preferred` (defaults to ['aria2c', 'axel'])
    - Looks in PATH via shutil.which
    - Looks next to the running script (sys.argv[0] directory)
    """
    if preferred is None:
        preferred = ["aria2c", "axel"]

    # On Windows, executables typically end with .exe
    is_windows = sys.platform.startswith("win")

    # 1) search in PATH
    for name in preferred:
        exe_name = name + (".exe" if is_windows else "")
        path = shutil.which(exe_name)
        if path:
            return path

    # 2) search next to the running script
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for name in preferred:
        exe_name = name + (".exe" if is_windows else "")
        local_path = os.path.join(base_dir, exe_name)
        if os.path.isfile(local_path):
            return local_path

    return None


def _download_file_requests(url: str, target_path: str, progress_fn: Optional[Callable[[int, int], None]] = None):
    """
    Simple downloader using requests. Streams to file with optional progress callback.
    progress_fn(received_bytes, total_bytes)
    """
    if requests is None:
        raise RuntimeError("requests is required for auto-download. Install via: pip install requests")

    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        received = 0
        with open(target_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    received += len(chunk)
                    if progress_fn:
                        try:
                            progress_fn(received, total)
                        except Exception:
                            pass


def ensure_aria2_on_windows(log_fn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    On Windows: attempts to download aria2 portable release (aria2c.exe) from GitHub releases and
    place aria2c.exe next to the running script (sys.argv[0] directory).
    Returns full path to aria2c.exe on success, or None on failure / not supported.

    IMPORTANT:
    - This function requires 'requests' to be installed to perform the download.
    - It should be called only after explicit user consent in GUI.
    """
    if not sys.platform.startswith("win"):
        if log_fn:
            log_fn("[info] Not running on Windows; skipping aria2 auto-install.")
        return None

    exe_name = "aria2c.exe"
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    final_path = os.path.join(base_dir, exe_name)

    # Already present?
    if os.path.isfile(final_path):
        if log_fn:
            log_fn(f"[info] aria2c already present at {final_path}")
        return final_path

    if requests is None:
        if log_fn:
            log_fn("[warn] 'requests' library not installed; cannot auto-download aria2. Install via: pip install requests")
        return None

    try:
        if log_fn:
            log_fn("[info] Querying GitHub API for aria2 latest release...")
        resp = requests.get(GITHUB_API_RELEASES, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        assets = data.get("assets", [])

        # Prefer asset names that include 'win' and end with .zip
        candidate = None
        for a in assets:
            name = (a.get("name") or "").lower()
            if "win" in name and name.endswith(".zip"):
                candidate = a
                break

        # fallback: first .zip
        if not candidate:
            for a in assets:
                if (a.get("name") or "").lower().endswith(".zip"):
                    candidate = a
                    break

        if not candidate:
            if log_fn:
                log_fn("[warn] No suitable aria2 Windows ZIP asset found in release.")
            return None

        download_url = candidate.get("browser_download_url")
        if not download_url:
            if log_fn:
                log_fn("[warn] No browser_download_url for aria2 asset.")
            return None

        # download to temp
        tmp_fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(tmp_fd)
        try:
            if log_fn:
                log_fn(f"[info] Downloading aria2 asset: {candidate.get('name')}")
            _download_file_requests(download_url, tmp_zip, progress_fn=lambda r, t: None)

            # extract aria2c.exe from zip
            with zipfile.ZipFile(tmp_zip, "r") as z:
                members = z.namelist()
                exe_entry = None
                for m in members:
                    if m.lower().endswith("aria2c.exe"):
                        exe_entry = m
                        break
                if not exe_entry:
                    # sometimes archive may contain nested directories; try to find any exe with aria2c pattern
                    for m in members:
                        if "aria2c" in m.lower() and m.lower().endswith(".exe"):
                            exe_entry = m
                            break
                if not exe_entry:
                    if log_fn:
                        log_fn("[warn] aria2c.exe not found inside downloaded ZIP.")
                    return None

                # extract into a temp dir then move exe to base_dir
                temp_extract_dir = tempfile.mkdtemp()
                z.extract(exe_entry, temp_extract_dir)
                extracted_path = os.path.join(temp_extract_dir, exe_entry)
                # move to final_path (create parent dirs if any)
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                shutil.move(extracted_path, final_path)

                # cleanup extracted dirs (attempt)
                try:
                    shutil.rmtree(temp_extract_dir)
                except Exception:
                    pass

            if log_fn:
                log_fn(f"[info] aria2c installed to {final_path}")
            return final_path
        finally:
            try:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
            except Exception:
                pass
    except Exception as e:
        if log_fn:
            log_fn(f"[error] Failed to auto-install aria2: {e}")
        return None
