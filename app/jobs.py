import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import media
from app.subtitles import build_ass, split_words_into_cues
from app.translate import TranslationError, canonical_language, translate_lines

log = logging.getLogger("dozabaneh")

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
BUSY_STATUSES = {"queued", "preparing", "listening", "translating"}
FONTS_DIR = Path(__file__).parent / "static" / "fonts"

INTERRUPTED = "برنامه در میانه‌ی کار بسته شد. ویدیو را دوباره بارگذاری کنید."
NO_SPEECH = "در این ویدیو گفتاری پیدا نشد."
UNTRANSLATABLE = "ترجمه از زبان این ویدیو پشتیبانی نمی‌شود. زبان زیرنویس را همان زبان گفتار بگذارید."
TRANSLATION_FAILED = "ترجمه انجام نشد. اتصال اینترنت را برای بار اول دریافت مدل بررسی کنید."
PROCESS_FAILED = "پردازش ویدیو انجام نشد. فایل را بررسی کنید و دوباره بارگذاری کنید."
RENDER_FAILED = "ساخت ویدیو انجام نشد. دوباره تلاش کنید."


class JobFailure(Exception):
    pass


class Processor:
    def __init__(self, data_dir, transcribe, make_translator, translate_languages):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.transcribe = transcribe
        self.make_translator = make_translator
        self.translate_languages = set(translate_languages)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._fail_interrupted_jobs()

    def _fail_interrupted_jobs(self):
        for path in self.data_dir.glob("*/job.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            changes = {}
            if job["status"] in BUSY_STATUSES:
                changes.update(status="failed", error=INTERRUPTED)
            if job["render"]["status"] == "running":
                changes["render"] = {"status": "failed", "progress": 0, "error": RENDER_FAILED}
            if changes:
                self.update(job["id"], **changes)

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def new_job_dir(self):
        job_id = uuid.uuid4().hex
        directory = self.data_dir / job_id
        directory.mkdir()
        return job_id, directory

    def job_dir(self, job_id):
        if not JOB_ID.match(job_id):
            return None
        directory = self.data_dir / job_id
        return directory if (directory / "job.json").exists() else None

    def get(self, job_id):
        directory = self.job_dir(job_id)
        if directory is None:
            return None
        with self._lock:
            return json.loads((directory / "job.json").read_text(encoding="utf-8"))

    def _write_json(self, path, value):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def create(self, job):
        with self._lock:
            self._write_json(self.data_dir / job["id"] / "job.json", job)
        self._executor.submit(self._process, job["id"])

    def update(self, job_id, **changes):
        path = self.data_dir / job_id / "job.json"
        with self._lock:
            job = json.loads(path.read_text(encoding="utf-8"))
            job.update(changes)
            self._write_json(path, job)
        return job

    def cues(self, job_id):
        with self._lock:
            return json.loads((self.data_dir / job_id / "cues.json").read_text(encoding="utf-8"))

    def save_cues(self, job_id, cues):
        with self._lock:
            self._write_json(self.data_dir / job_id / "cues.json", cues)

    def _process(self, job_id):
        directory = self.data_dir / job_id
        audio = directory / "audio.wav"
        try:
            job = self.get(job_id)
            self._prepare_preview(job, directory)
            self.update(job_id, status="listening", progress=0)
            media.extract_audio(directory / job["source"], audio)
            words, detected = self.transcribe(
                audio, job["speech"], job["quality"], lambda p: self.update(job_id, progress=p)
            )
            cues = split_words_into_cues(words)
            if not cues:
                raise JobFailure(NO_SPEECH)
            self.update(job_id, status="translating", progress=0, detected=detected)
            self._translate(job, cues, canonical_language(detected))
            self.save_cues(job_id, cues)
            self.update(job_id, status="ready", progress=1)
        except JobFailure as failure:
            self.update(job_id, status="failed", error=str(failure))
        except TranslationError:
            log.exception("translation failed for %s", job_id)
            self.update(job_id, status="failed", error=TRANSLATION_FAILED)
        except Exception:
            log.exception("processing failed for %s", job_id)
            self.update(job_id, status="failed", error=PROCESS_FAILED)
        finally:
            audio.unlink(missing_ok=True)

    def _prepare_preview(self, job, directory):
        extension = Path(job["source"]).suffix
        info = media.probe(directory / job["source"])
        if not media.needs_preview_copy(extension, info):
            return
        self.update(job["id"], status="preparing", progress=0)
        media.make_preview(
            directory / job["source"],
            directory / "preview.mp4",
            info.duration,
            lambda p: self.update(job["id"], progress=p),
        )
        self.update(job["id"], preview="preview.mp4")

    def _translate(self, job, cues, source):
        languages = [lang for lang in (job["lang1"], job["lang2"]) if lang]
        originals = [cue.pop("text") for cue in cues]
        for cue in cues:
            cue["texts"] = {}
        for position, lang in enumerate(languages):
            if canonical_language(lang) == source:
                texts = originals
            else:
                if source not in self.translate_languages:
                    raise JobFailure(UNTRANSLATABLE)

                def report(done, position=position):
                    self.update(job["id"], progress=(position + done) / len(languages))

                send = self.make_translator(source, lang)
                texts = translate_lines(originals, send=send, on_progress=report)
            for cue, text in zip(cues, texts, strict=True):
                cue["texts"][lang] = text

    def start_render(self, job_id):
        self.update(job_id, render={"status": "running", "progress": 0})
        self._executor.submit(self._render, job_id)

    def _render(self, job_id):
        directory = self.data_dir / job_id
        try:
            job = self.get(job_id)
            subtitles = directory / "subtitles.ass"
            subtitles.write_text(
                build_ass(self.cues(job_id), job["lang1"], job["lang2"], job["width"], job["height"]),
                encoding="utf-8",
            )
            media.burn_subtitles(
                directory / job["source"],
                subtitles.name,
                FONTS_DIR,
                directory / "output.mp4",
                job["duration"],
                lambda p: self.update(job_id, render={"status": "running", "progress": p}),
            )
            self.update(job_id, render={"status": "done", "progress": 1})
        except Exception:
            log.exception("render failed for %s", job_id)
            self.update(job_id, render={"status": "failed", "progress": 0, "error": RENDER_FAILED})
