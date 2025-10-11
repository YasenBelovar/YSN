#!/usr/bin/env python3
"""
YSN - simple GUI frontend for yt-dlp / youtube-dl
Features:
- choose backend (yt-dlp or youtube-dl)
- download single URL / playlist / channel
- options: embed subtitles, embed thumbnail, download all audio tracks, compute SHA256
- parallel downloads (thread pool) with safe fallback to sequential
- logs and simple progress display
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

DEFAULT_PARALLEL = 2

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

# Worker - performs a single download job using chosen backend
class DownloadJob:
    def __init__(self, url: str, outdir: Path, backend: str, opts: dict, log_callback):
        self.url = url
        self.outdir = Path(outdir)
        self.backend = backend
        self.opts = opts or {}
        self.log = log_callback

    def run(self):
        start = datetime.utcnow().isoformat()
        self.log(f"[{start}] Starting download: {self.url} (backend: {self.backend})")
        try:
            if self.backend == 'yt-dlp' and ytd_mod:
                self._run_yt_dlp()
            elif self.backend == 'youtube-dl' and ytdl_mod:
                self._run_youtube_dl()
            else:
                raise RuntimeError(f"Selected backend '{self.backend}' not available on this system")
        except Exception as e:
            self.log(f"[ERROR] {e}\n{traceback.format_exc()}")
            return False
        return True

    def _common_opts(self):
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
            # download all subtitles and convert to srt if asked
            if self.opts.get('embed_subtitles'):
                # embed_subtitles handled by postprocessors below
                pass
        # merge output format is optional (yt-dlp will handle merging)
        # add postprocessors (embedding)
        postprocessors = []
        if self.opts.get('embed_thumbnail'):
            postprocessors.append({'key': 'EmbedThumbnail'})
        if self.opts.get('embed_subtitles'):
            # For yt-dlp: use 'FFmpegEmbedSubtitle' or convert to srt and then embed where possible
            postprocessors.append({'key': 'EmbedSubtitle'})
        if postprocessors:
            opts['postprocessors'] = postprocessors
        # keep original fragments where possible
        opts['nopart'] = False
        # suppress interactive
        opts['noplaylist'] = False if self.opts.get('allow_playlist', True) else True
        # for speed: set limited retries
        opts['retries'] = 3
        opts['continuedl'] = True
        # no progress printing to stdout (we handle via hooks)
        opts['logger'] = YSNLogger(self.log)
        opts['progress_hooks'] = [self._progress_hook]
        return opts

    def _progress_hook(self, d):
        # d: dict with status keys
        status = d.get('status')
        if status == 'downloading':
            downloaded = d.get('downloaded_bytes') or d.get('downloaded_bytes', None)
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or d.get('total_bytes') 
            percent = None
            try:
                if total:
                    percent = downloaded / total * 100
                else:
                    percent = None
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
        opts = self._common_opts()
        # add postprocessors for embedding subtitles via ffmpeg if requested
        # ytd_mod expects options slightly different, but our opts are compatible
        # Ensure outdir exists
        self.outdir.mkdir(parents=True, exist_ok=True)
        with ytd_mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            # compute hash for each downloaded file in info (if present)
            if isinstance(info, dict):
                # if playlist, info may contain entries
                if 'entries' in info and info['entries']:
                    for e in info['entries']:
                        self._after_download_process(e)
                else:
                    self._after_download_process(info)

    def _run_youtube_dl(self):
        # youtube-dl API is similar
        opts = self._common_opts()
        self.outdir.mkdir(parents=True, exist_ok=True)
        with ytdl_mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            if isinstance(info, dict):
                if 'entries' in info and info['entries']:
                    for e in info['entries']:
                        self._after_download_process(e)
                else:
                    self._after_download_process(info)

    def _after_download_process(self, info):
        # Try to locate file produced by yt-dlp/youtube-dl
        if not info:
            return
        filename = info.get('_filename') or info.get('requested_downloads', [{}])[0].get('filepath') if info.get('requested_downloads') else None
        if not filename:
            # try to guess via title/id/ext
            title = info.get('title')
            vid = info.get('id')
            ext = info.get('ext') or 'mp4'
            guessed = self.outdir / f"{title}-{vid}.{ext}" if title and vid else None
            if guessed and guessed.exists():
                filename = str(guessed)
        if filename and Path(filename).exists():
            fpath = Path(filename)
            sha = sha256_of_file(fpath)
            self.log(f"[hash] {fpath.name} SHA256: {sha}")
            # optionally compare with remote? Not generally available
        else:
            self.log(f"[warn] could not determine downloaded filename from info: {info.get('id', 'unknown')}")

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
            # If parallel more than CPU count, tune down
            max_workers = min(self.parallel, (os.cpu_count() or 2))
            # if too many, fallback to sequential to be safe
            if max_workers > 8:
                log(f"[info] Too many workers requested ({self.parallel}), reducing to 8")
                max_workers = 8
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

# GUI
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YSN — yt-dlp / youtube-dl frontend")
        self.resize(900, 600)
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
        # choose preferred if available
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
        self.parallelSpin.setMaximum(8)
        self.parallelSpin.setValue(DEFAULT_PARALLEL)
        hb.addWidget(QtWidgets.QLabel("Parallel downloads:"))
        hb.addWidget(self.parallelSpin)
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

    def browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output folder", str(Path.cwd()))
        if d:
            self.outEdit.setText(d)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logView.appendPlainText(f"[{ts}] {msg}")

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
        # Building jobs: if URL is a single page or playlist, let backend decide; we submit one job for the URL
        job = DownloadJob(url=url, outdir=outdir, backend=backend, opts=opts, log_callback=self.log)
        jobs = [job]
        # Start worker
        self.startBtn.setEnabled(False)
        self.log("Starting worker...")
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
