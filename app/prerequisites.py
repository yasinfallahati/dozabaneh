import io
import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tqdm.std import tqdm

from app.translate import MODEL_FILES, MODEL_REPO

log = logging.getLogger("dozabaneh")

WHISPER_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
DOWNLOAD_FAILED = "دانلود انجام نشد. اینترنت را بررسی کنید و دوباره دکمه را بزنید."


@dataclass(frozen=True)
class Prerequisite:
    id: str
    label: str
    size_mb: int | None
    is_installed: Callable[[], bool]
    install: Callable | None = None
    command: str | None = None


def byte_progress_bar(on_progress, min_total=0):
    """A tqdm class that reports the furthest byte count among its bars.

    Xet downloads move the network bar first and only fill the disk bar at the end.
    min_total keeps the small files, which finish first, from reading as 100%.
    """
    fractions = {}

    class ByteProgress(tqdm):
        def __init__(self, *args, **kwargs):
            # Without a terminal tqdm disables itself and stops counting.
            kwargs.update(disable=False, file=io.StringIO())
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            super().update(n)
            if self.unit == "B" and self.total:
                fractions[id(self)] = min(self.n / max(self.total, min_total), 1)
                on_progress(max(fractions.values()))

    return ByteProgress


def _model(item_id, label, size_mb, repo, files):
    from huggingface_hub import snapshot_download

    def is_installed():
        try:
            path = snapshot_download(repo, allow_patterns=files, local_files_only=True)
        except Exception:
            return False
        return (Path(path) / "model.bin").exists()

    def install(on_progress):
        bar = byte_progress_bar(on_progress, min_total=size_mb * 900_000)
        snapshot_download(repo, allow_patterns=files, tqdm_class=bar)

    return Prerequisite(item_id, label, size_mb, is_installed, install)


def default_prerequisites():
    return [
        Prerequisite(
            "ffmpeg",
            "ابزار پردازش ویدیو (ffmpeg)",
            None,
            lambda: bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            command="sudo apt install ffmpeg",
        ),
        _model(
            "speech-accurate",
            "تشخیص گفتار دقیق",
            1600,
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            WHISPER_FILES,
        ),
        _model("speech-fast", "تشخیص گفتار سریع", 486, "Systran/faster-whisper-small", WHISPER_FILES),
        _model("translation", "ترجمه‌ی آفلاین", 640, MODEL_REPO, MODEL_FILES),
    ]


class PrerequisiteInstaller:
    def __init__(self, items):
        self.items = items
        self._lock = threading.Lock()
        self._running = False
        self._progress = {item.id: {"state": "idle", "progress": 0, "error": None} for item in items}

    def _set(self, item_id, **changes):
        with self._lock:
            self._progress[item_id].update(changes)

    def state(self):
        with self._lock:
            running = self._running
            progress = {key: dict(value) for key, value in self._progress.items()}
        items = [
            {
                "id": item.id,
                "label": item.label,
                "size_mb": item.size_mb,
                "installed": item.is_installed(),
                "command": item.command,
                **progress[item.id],
            }
            for item in self.items
        ]
        missing = [item for item in items if not item["installed"]]
        if running:
            status = "running"
        elif any(item["state"] == "failed" for item in items):
            status = "failed"
        elif any(item["command"] is None for item in missing):
            status = "missing"
        else:
            status = "manual" if missing else "complete"
        return {"status": status, "items": items}

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._install_all, daemon=True).start()

    def _install_all(self):
        try:
            pending = [item for item in self.items if item.install and not item.is_installed()]
            for item in pending:
                self._set(item.id, state="waiting", progress=0, error=None)
            for item in pending:
                self._install(item)
        finally:
            with self._lock:
                self._running = False

    def _install(self, item):
        self._set(item.id, state="installing")
        try:
            item.install(lambda progress: self._set(item.id, progress=progress))
        except Exception:
            log.exception("could not install %s", item.id)
            self._set(item.id, state="failed", error=DOWNLOAD_FAILED)
        else:
            self._set(item.id, state="done", progress=1)
