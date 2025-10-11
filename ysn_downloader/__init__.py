# ysn_downloader/__init__.py
from .downloader import detect_external_downloader, ensure_aria2_on_windows

__all__ = ["detect_external_downloader", "ensure_aria2_on_windows"]
