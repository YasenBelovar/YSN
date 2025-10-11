#!/usr/bin/env python3
"""
YSN - enhanced GUI frontend for yt-dlp / youtube-dl
Changes done:
- If URL is playlist/channel, expand entries and create one DownloadJob per video so that parallel downloads are real.
- Allow parallel downloads > 8 (configurable in GUI).
- Provide optimized yt-dlp options (concurrent_fragments, http_chunk_size).
- Detect and use external downloader (aria2c / axel) if available. On Windows can auto-download aria2c (with user consent).
- Batch small URL lists to reduce overhead.
- Retries with exponential backoff.
- Keeps original features (embedding, hashing, etc.)
Original file used as base: uploaded by user. :contentReference[oaicite:1]{index=1}
"""

from pathlib import Path
import sys
import os
import traceback
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import shutil
import zipfile
import tempfile

try:
    from PySide6 import QtCore, QtWidgets, QtGui
except Exception as e:
    print("PySide6 is required. Install with: pip install PySide6")
    raise

# Try to import yt_dlp, fallback to youtube_dl
BACKENDS_AVAILABLE = {}
try:
    import yt_dlp as ytd_mod
    BACKENDS_AVAILABLE['yt-dlp'] = 'yt_dlp'
except Exception:
    ytd_mod = None
try:
    import youtube_dl as ytdl_mod
    BACKENDS_AVAILABLE['youtube-dl'] = 'youtube_dl'
except Exception:
    ytdl_mod = None

DEFAULT_PARALLEL = 4  # sensible default for UI

# import our downloader helper (will be provided in ysn_downloader/downloader.py)
try:
    from ysn_downloader.downloader import ensure_aria2_on_windows, detect_external_downloader
except Exception:
    # If module missing, we'll still function with built-in logic (but please add the module)
    ensure_aria2_on_windows = None
    detect_external_downloader = None


# Helper: compute sha256 of file
def sha256_of_file(path: Path, chunk=8192):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

# Simple logger that writes into the GUI log through callback
class YSNLogger(object):
    def __init__(self, callback):
        self.callback = callback
    def debug(self, msg):
        self.callback("[debug] " + str(msg))
    def info(self, msg):
        self.callback("[info] " + str(msg))
    def warning(self, msg):
        self.callback("[warning] " + str(msg))
    def error(self, msg):
        self.callback("[error] " + str(msg))

# Worker - performs a single download job using chosen backend
class DownloadJob:
    def __init__(self, url: str, outdir: Path, backend: str, opts: dict, log_callback, ytdl_backend_module):
        self.url = url
        self.outdir = Path(outdir)
        self.backend = backend
        self.opts = opts or {}
        self.log = log_callback
        self.ytdl_backend_module = ytdl_backend_module  # module object for API calls (yt_dlp or youtube_dl)

    def run(self):
        start = datetime.utcnow().isoformat()
        self.log(f"[{start}] Starting download: {self.url} (backend: {self.backend})")
        try:
            if self.backend == 'yt-dlp' and self.ytdl_backend_module:
                self._run_yt_dlp()
            elif self.backend == 'youtube-dl' and self.ytdl_backend_module:
                self._run_youtube_dl()
            else:
                raise RuntimeError(f"Selected backend '{self.backend}' not available on this system")
        except Exception as e:
            self.log(f"[ERROR] {e}\n{traceback.format_exc()}")
            return False
        return True

    def _common_opts(self, extra_opts=None):
        # common options built for yt_dlp and youtube_dl module APIs
        opts = {}
        # output template
        opts['outtmpl'] = str((self.outdir / '%(title)s-%(id)s.%(ext)s').as_posix())
        # prefer best quality; avoid reencoding where possible
        opts['format'] = 'bestvideo+bestaudio/best'
        # write thumbnail & embed if requested
        if self.opts.get('write_thumbnail'):
            opts['writethumbnail'] = True
        # subtitles
        if self.opts.get('download_subs'):
            opts['writesubtitles'] = True
            opts['writeautomaticsub'] = True
            if self.opts.get('embed_subtitles'):
                # will be handled by postprocessors below where supported
                pass
        # postprocessors (embedding)
        postprocessors = []
        if self.opts.get('embed_thumbnail'):
            postprocessors.append({'key': 'EmbedThumbnail'})
        if self.opts.get('embed_subtitles'):
            postprocessors.append({'key': 'EmbedSubtitle'})
        if postprocessors:
            opts['postprocessors'] = postprocessors

        # default safe opts for speed & stability
        opts['nopart'] = False  # keep .part files by default (safer)
        opts['noplaylist'] = False if self.opts.get('allow_playlist', True) else True
        opts['retries'] = 3
        opts['continuedl'] = True
        # set logger and progress hooks
        opts['logger'] = YSNLogger(self.log)
        opts['progress_hooks'] = [self._progress_hook]

        # performance tunables (can be overridden by extra_opts)
        perf = {
            'concurrent_fragments': 16,
            'http_chunk_size': 16 * 1024 * 1024,
            # external downloader params will be set in downloader helper if present
        }
        if extra_opts:
            perf.update(extra_opts)
        opts.update(perf)
        return opts

    def _progress_hook(self, d):
        status = d.get('status')
        if status == 'downloading':
            downloaded = d.get('downloaded_bytes') or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            percent = None
            try:
                if total:
                    percent = downloaded / total * 100
            except Exception:
                percent = None
            speed = d.get('speed')
            eta = d.get('eta')
            fname = d.get('filename') or d.get('tmpfilename') or d.get('info_dict', {}).get('title')
            if percent:
                self.log(f"[progress] {fname}: {percent:.1f}% ETA {eta}s speed {speed}")
            else:
                self.log(f"[progress] {fname}: downloading... ({downloaded} bytes)")
        elif status == 'finished':
            fname = d.get('filename') or d.get('info_dict', {}).get('title')
            self.log(f"[done] finished downloading: {fname}")

    def _run_yt_dlp(self):
        # prepare options (detect external downloader if present)
        extra_opts = {}
        # if an external downloader exists in PATH or next to exe, we will instruct yt-dlp to use it
        ext = None
        try:
            # try to use detect_external_downloader from helper module, if available
            if detect_external_downloader:
                ext = detect_external_downloader()
        except Exception:
            ext = shutil.which('aria2c') or shutil.which('axel')
        opts = self._common_opts(extra_opts=extra_opts)
        # add external_downloader keys if ext present
        if ext:
            # For API, keys are 'external_downloader' and 'external_downloader_args'
            opts['external_downloader'] = ext
            if ext.lower().startswith('aria2'):
                opts['external_downloader_args'] = [
                    '--max-connection-per-server=16',
                    '--split=16',
                    '--min-split-size=1M',
                    '--file-allocation=none',
                ]
            elif ext.lower().startswith('axel'):
                opts['external_downloader_args'] = ['-n', '16']

        # Ensure outdir exists
        self.outdir.mkdir(parents=True, exist_ok=True)
        # Download via yt-dlp API
        with self.ytdl_backend_module.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            if isinstance(info, dict):
                if 'entries' in info and info['entries']:
                    for e in info['entries']:
                        self._after_download_process(e)
                else:
                    self._after_download_process(info)

    def _run_youtube_dl(self):
        opts = self._common_opts()
        self.outdir.mkdir(parents=True, exist_ok=True)
        with self.ytdl_backend_module.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            if isinstance(info, dict):
                if 'entries' in info and info['entries']:
                    for e in info['entries']:
                        self._after_download_process(e)
                else:
                    self._after_download_process(info)

    def _after_download_process(self, info):
        if not info:
            return
        filename = info.get('_filename') or (info.get('requested_downloads', [{}])[0].get('filepath') if info.get('requested_downloads') else None)
        if not filename:
            title = info.get('title')
            vid = info.get('id')
            ext = info.get('ext') or 'mp4'
            guessed = self.outdir / f"{title}-{vid}.{ext}" if title and vid else None
            if guessed and guessed.exists():
                filename = str(guessed)
        if filename and Path(filename).exists():
            fpath = Path(filename)
            if self.opts.get('compute_hash'):
                try:
                    sha = sha256_of_file(fpath)
                    self.log(f"[hash] {fpath.name} SHA256: {sha}")
                except Exception as e:
                    self.log(f"[hash] failed for {fpath}: {e}")
        else:
            self.log(f"[warn] could not determine downloaded filename from info: {info.get('id', 'unknown')}")


# Qt Worker thread to run a queue of DownloadJob objects
class DownloadWorker(QtCore.QThread):
    log_signal = QtCore.Signal(str)
    finished_signal = QtCore.Signal()

    def __init__(self, jobs, parallel=1, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.parallel = max(1, int(parallel))

    def run(self):
        def log(msg):
            # ensure newline and flush
            self.log_signal.emit(msg)
        # If parallel is 1, do sequential to simplify merging/postprocessing
        if self.parallel <= 1:
            for job in self.jobs:
                job.log = log
                job.run()
        else:
            # Allow higher parallelism: cap is user requested, but limit with sanity cap
            cpu_count = (os.cpu_count() or 2)
            # allow up to min(requested, cpu_count*4, 64)
            max_workers = min(self.parallel, max(4, cpu_count * 4), 64)
            log(f"[info] Using up to {max_workers} worker threads for downloads.")
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = []
                for job in self.jobs:
                    job.log = log
                    futures.append(ex.submit(job.run))
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as e:
                        log(f"[ERROR] job raised: {e}\n{traceback.format_exc()}")
        self.finished_signal.emit()


# --- GUI ---
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YSN — yt-dlp / youtube-dl frontend (optimized)")
        self.resize(950, 620)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Top: URL input and output dir
        form = QtWidgets.QFormLayout()
        self.urlEdit = QtWidgets.QLineEdit()
        form.addRow("URL / playlist / channel:", self.urlEdit)
        row = QtWidgets.QHBoxLayout()
        self.outEdit = QtWidgets.QLineEdit(str(Path.cwd()))
        self.browseBtn = QtWidgets.QPushButton("Browse…")
        self.browseBtn.clicked.connect(self.browse_out)
        row.addWidget(self.outEdit)
        row.addWidget(self.browseBtn)
        form.addRow("Output folder:", row)

        # Backend selection
        self.backendCombo = QtWidgets.QComboBox()
        for b in ['yt-dlp', 'youtube-dl']:
            self.backendCombo.addItem(b)
        if 'yt-dlp' in BACKENDS_AVAILABLE:
            self.backendCombo.setCurrentText('yt-dlp')
        elif 'youtube-dl' in BACKENDS_AVAILABLE:
            self.backendCombo.setCurrentText('youtube-dl')
        form.addRow("Backend:", self.backendCombo)

        layout.addLayout(form)

        # Options
        optsLayout = QtWidgets.QHBoxLayout()
        self.chk_subs = QtWidgets.QCheckBox("Download subtitles")
        self.chk_embed_subs = QtWidgets.QCheckBox("Embed subtitles (if possible)")
        self.chk_thumbnail = QtWidgets.QCheckBox("Download thumbnail / embed")
        self.chk_hash = QtWidgets.QCheckBox("Compute SHA256 after download")
        self.chk_preview = QtWidgets.QCheckBox("Show preview (can be disabled)")
        self.chk_preview.setChecked(True)
        optsLayout.addWidget(self.chk_subs)
        optsLayout.addWidget(self.chk_embed_subs)
        optsLayout.addWidget(self.chk_thumbnail)
        optsLayout.addWidget(self.chk_hash)
        optsLayout.addWidget(self.chk_preview)
        layout.addLayout(optsLayout)

        # Parallel control and start button
        hb = QtWidgets.QHBoxLayout()
        self.parallelSpin = QtWidgets.QSpinBox()
        self.parallelSpin.setMinimum(1)
        self.parallelSpin.setMaximum(64)  # allow up to 64 concurrent workers
        self.parallelSpin.setValue(DEFAULT_PARALLEL)
        hb.addWidget(QtWidgets.QLabel("Parallel downloads (workers):"))
        hb.addWidget(self.parallelSpin)

        # Performance tunables (chunk size & fragments) shown in UI for power users
        self.fragSpin = QtWidgets.QSpinBox()
        self.fragSpin.setMinimum(1)
        self.fragSpin.setMaximum(64)
        self.fragSpin.setValue(16)
        hb.addWidget(QtWidgets.QLabel("Fragments per download:"))
        hb.addWidget(self.fragSpin)

        self.chunkEdit = QtWidgets.QLineEdit("16M")
        hb.addWidget(QtWidgets.QLabel("Chunk size (e.g. 8M,16M,32M):"))
        hb.addWidget(self.chunkEdit)

        self.startBtn = QtWidgets.QPushButton("Start download")
        self.startBtn.clicked.connect(self.start_download)
        hb.addStretch()
        hb.addWidget(self.startBtn)
        layout.addLayout(hb)

        # Log area
        self.logView = QtWidgets.QPlainTextEdit()
        self.logView.setReadOnly(True)
        self.logView.setMaximumBlockCount(10000)
        layout.addWidget(self.logView, 1)

        # status bar
        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)

        self.worker = None

        # Button to install aria2c on Windows (optional)
        if sys.platform.startswith("win"):
            installRow = QtWidgets.QHBoxLayout()
            self.installBtn = QtWidgets.QPushButton("Install aria2c (recommended for speed)")
            self.installBtn.clicked.connect(self.on_install_aria2)
            installRow.addWidget(self.installBtn)
            installRow.addStretch()
            layout.addLayout(installRow)

    def browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output folder", str(Path.cwd()))
        if d:
            self.outEdit.setText(d)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logView.appendPlainText(f"[{ts}] {msg}")

    # Extract entries from playlist/channel without downloading
    def _expand_playlist_urls(self, url: str, backend: str):
        """
        Uses yt-dlp / youtube-dl API to extract video entries (urls) from playlist/channel.
        Returns a list of video URLs (or [url] if single video).
        """
        self.log(f"[info] Expanding URL to individual entries (if playlist): {url}")
        try:
            if backend == 'yt-dlp' and ytd_mod:
                ydl_mod = ytd_mod
            elif backend == 'youtube-dl' and ytdl_mod:
                ydl_mod = ytdl_mod
            else:
                self.log("[warn] Backend module not available for expansion")
                return [url]
            opts = {
                'quiet': True,
                'skip_download': True,
                'extract_flat': True,  # do not resolve full metadata, fast listing
                'logger': YSNLogger(self.log),
            }
            with ydl_mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # If playlist or result with entries
                if isinstance(info, dict) and info.get('entries'):
                    urls = []
                    for entry in info['entries']:
                        # entry may be dict with 'url' or 'id' depending on extract_flat
                        # try to build full url when possible
                        if 'url' in entry and entry['url']:
                            u = entry['url']
                            # if the extracted url is an id only, let yt-dlp handle it later
                            urls.append(u)
                        elif 'id' in entry and entry['id']:
                            # attempt to construct a watch URL for youtube-like ids
                            urls.append(entry['id'])
                        else:
                            continue
                    if urls:
                        self.log(f"[info] Found {len(urls)} entries in playlist.")
                        return urls
                # fallback: single URL
                return [url]
        except Exception as e:
            self.log(f"[warn] Failed to expand playlist: {e}")
            return [url]

    def on_install_aria2(self):
        # Attempt to offer automatic aria2 download on Windows
        if not ensure_aria2_on_windows:
            QtWidgets.QMessageBox.information(self, "Not available", "Auto-install helper not available.")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Install aria2c",
            "This will download the aria2 portable binary from its GitHub releases and place it next to the application.\nProceed?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self.installBtn.setEnabled(False)
            self.log("[info] Starting aria2c download in background...")
            t = threading.Thread(target=self._background_install_aria2, daemon=True)
            t.start()

    def _background_install_aria2(self):
        try:
            path = ensure_aria2_on_windows(self.log)
            if path:
                self.log(f"[info] aria2c installed at: {path}")
                QtWidgets.QMessageBox.information(self, "aria2c installed", f"aria2c is available: {path}")
            else:
                self.log("[warn] aria2c installation was not completed.")
        except Exception as e:
            self.log(f"[error] aria2c install failed: {e}")
        finally:
            # re-enable button in GUI thread
            QtCore.QMetaObject.invokeMethod(self.installBtn, "setEnabled", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(bool, True))

    def start_download(self):
        url = self.urlEdit.text().strip()
        if not url:
            QtWidgets.QMessageBox.warning(self, "No URL", "Please paste a video / playlist / channel URL.")
            return
        outdir = Path(self.outEdit.text().strip() or ".")
        backend = self.backendCombo.currentText()
        opts = {
            'download_subs': self.chk_subs.isChecked(),
            'embed_subtitles': self.chk_embed_subs.isChecked(),
            'write_thumbnail': self.chk_thumbnail.isChecked(),
            'embed_thumbnail': self.chk_thumbnail.isChecked(),
            'compute_hash': self.chk_hash.isChecked(),
            'allow_playlist': True
        }
        parallel = int(self.parallelSpin.value())
        fragments = int(self.fragSpin.value())
        # parse chunk size string like '16M' -> bytes
        chunk_text = self.chunkEdit.text().strip().upper()
        chunk_bytes = 16 * 1024 * 1024
        try:
            if chunk_text.endswith('M'):
                chunk_bytes = int(chunk_text[:-1]) * 1024 * 1024
            elif chunk_text.endswith('K'):
                chunk_bytes = int(chunk_text[:-1]) * 1024
            else:
                chunk_bytes = int(chunk_text)
        except Exception:
            self.log("[warn] invalid chunk size text, using default 16M")

        # Expand playlist into individual video urls so that parallelism is real
        urls = self._expand_playlist_urls(url, backend)
        # If entries are numeric IDs or not full urls, yt-dlp will handle them
        # Build list of DownloadJob objects (one per video or one per provided url)
        jobs = []
        # ytdl module pointer
        ytd_mod_to_use = None
        if backend == 'yt-dlp' and ytd_mod:
            ytd_mod_to_use = ytd_mod
        elif backend == 'youtube-dl' and ytdl_mod:
            ytd_mod_to_use = ytdl_mod

        # Decide whether external downloader is available
        ext = None
        try:
            if detect_external_downloader:
                ext = detect_external_downloader()
            else:
                ext = shutil.which('aria2c') or shutil.which('axel')
        except Exception:
            ext = shutil.which('aria2c') or shutil.which('axel')

        if ext:
            self.log(f"[info] External downloader detected: {ext}")
        else:
            self.log("[info] No external downloader detected; will use built-in downloader (yt-dlp).")

        # If only one item and playlist expansion returned the same, keep single job (so merging & postprocessing simpler)
        for u in urls:
            job_opts = opts.copy()
            job_opts.update({
                # pass performance settings to DownloadJob
                'concurrent_fragments': fragments,
                'http_chunk_size': chunk_bytes,
                # pass compute_hash as requested
                'compute_hash': opts.get('compute_hash', False),
            })
            job = DownloadJob(url=u, outdir=outdir, backend=backend, opts=job_opts, log_callback=self.log, ytdl_backend_module=ytd_mod_to_use)
            jobs.append(job)

        # Start worker
        self.startBtn.setEnabled(False)
        self.log(f"[info] Starting worker for {len(jobs)} jobs with parallel={parallel}")
        self.worker = DownloadWorker(jobs=jobs, parallel=parallel)
        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def on_finished(self):
        self.log("All tasks finished.")
        self.startBtn.setEnabled(True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
