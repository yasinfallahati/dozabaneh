import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import media
from app.jobs import Processor
from app.prerequisites import PrerequisiteInstaller, default_prerequisites
from app.subtitles import build_srt
from app.transcribe import MODELS, WhisperTranscriber, speech_language_codes
from app.translate import NLLB_CODES, NllbTranslator, canonical_language

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
MP4_PREVIEWS = {".mp4", ".m4v", ".mov"}


class CueEdit(BaseModel):
    texts: dict[str, str]


def create_app(
    data_dir=DEFAULT_DATA_DIR,
    transcribe=None,
    make_translator=None,
    translate_languages=None,
    speech_languages=None,
    prerequisites=None,
):
    translate_languages = sorted(NLLB_CODES) if translate_languages is None else translate_languages
    if speech_languages is None:
        speech_languages = [
            code for code in speech_language_codes() if canonical_language(code) in NLLB_CODES
        ]
    processor = Processor(
        data_dir,
        transcribe or WhisperTranscriber(),
        make_translator or NllbTranslator(),
        translate_languages,
    )

    installer = PrerequisiteInstaller(
        default_prerequisites() if prerequisites is None else prerequisites
    )

    @asynccontextmanager
    async def lifespan(_):
        yield
        processor.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.processor = processor

    def require_job(job_id):
        job = processor.get(job_id)
        if job is None:
            raise HTTPException(404, "این کار پیدا نشد.")
        return job

    def require_ready(job_id):
        job = require_job(job_id)
        if job["status"] != "ready":
            raise HTTPException(404, "زیرنویس هنوز آماده نیست.")
        return job

    def stem(job):
        return Path(job["name"]).stem or "video"

    @app.get("/api/languages")
    def languages():
        return {"translate": translate_languages, "speech": speech_languages}

    @app.get("/api/setup")
    def setup_status():
        return installer.state()

    @app.post("/api/setup")
    def install_prerequisites():
        installer.start()
        return installer.state()

    @app.post("/api/jobs")
    async def create_job(
        request: Request, name: str, speech: str, lang1: str, quality: str, lang2: str = ""
    ):
        if lang1 not in translate_languages:
            raise HTTPException(400, "زبان زیرنویس اول را انتخاب کنید.")
        if lang2 and lang2 not in translate_languages:
            raise HTTPException(400, "زبان زیرنویس دوم پشتیبانی نمی‌شود.")
        if lang1 == lang2:
            raise HTTPException(400, "دو زیرنویس باید دو زبان متفاوت باشند.")
        if speech != "auto" and speech not in speech_languages:
            raise HTTPException(400, "زبان گفتار پشتیبانی نمی‌شود.")
        if quality not in MODELS:
            raise HTTPException(400, "کیفیت انتخاب‌شده معتبر نیست.")

        extension = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
            extension = ".bin"
        job_id, directory = processor.new_job_dir()
        source = directory / f"source{extension}"
        # The raw body goes straight to disk so large videos never sit in memory.
        with source.open("wb") as target:
            async for chunk in request.stream():
                target.write(chunk)

        info = await run_in_threadpool(media.probe, source)
        problem = None
        if info is None:
            problem = "این فایل ویدیو نیست یا خراب است."
        elif not info.has_audio:
            problem = "این ویدیو صدا ندارد و گفتاری برای زیرنویس نیست."
        if problem:
            source.unlink(missing_ok=True)
            directory.rmdir()
            raise HTTPException(400, problem)

        job = {
            "id": job_id,
            "name": Path(name).name,
            "source": source.name,
            "preview": source.name,
            "speech": speech,
            "detected": None,
            "lang1": lang1,
            "lang2": lang2 or None,
            "quality": quality,
            "status": "queued",
            "progress": 0,
            "error": None,
            "width": info.width,
            "height": info.height,
            "duration": info.duration,
            "render": {"status": "none", "progress": 0},
        }
        processor.create(job)
        return job

    @app.get("/api/jobs/{job_id}")
    def read_job(job_id: str):
        return require_job(job_id)

    @app.get("/api/jobs/{job_id}/video")
    def preview_video(job_id: str):
        job = require_job(job_id)
        path = processor.job_dir(job_id) / job["preview"]
        media_type = "video/mp4" if path.suffix in MP4_PREVIEWS else "video/webm"
        return FileResponse(path, media_type=media_type)

    @app.get("/api/jobs/{job_id}/cues")
    def read_cues(job_id: str):
        require_ready(job_id)
        return processor.cues(job_id)

    @app.put("/api/jobs/{job_id}/cues")
    def save_cues(job_id: str, edits: list[CueEdit]):
        job = require_ready(job_id)
        if job["render"]["status"] == "running":
            raise HTTPException(409, "تا پایان ساخت ویدیو نمی‌توان زیرنویس را تغییر داد.")
        cues = processor.cues(job_id)
        if len(edits) != len(cues):
            raise HTTPException(400, "تعداد زیرنویس‌ها تغییر کرده است.")
        for cue, edit in zip(cues, edits):
            cue["texts"] = {lang: edit.texts.get(lang, text) for lang, text in cue["texts"].items()}
        processor.save_cues(job_id, cues)
        (processor.job_dir(job_id) / "output.mp4").unlink(missing_ok=True)
        processor.update(job_id, render={"status": "none", "progress": 0})
        return {"saved": len(cues)}

    @app.get("/api/jobs/{job_id}/srt/{lang}")
    def download_srt(job_id: str, lang: str):
        job = require_ready(job_id)
        if lang not in (job["lang1"], job["lang2"]):
            raise HTTPException(404, "این زبان در زیرنویس‌ها نیست.")
        body = build_srt(processor.cues(job_id), lang).encode("utf-8-sig")
        filename = quote(f"{stem(job)}.{lang}.srt")
        return Response(
            body,
            media_type="application/x-subrip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )

    @app.post("/api/jobs/{job_id}/render")
    def render(job_id: str):
        job = require_ready(job_id)
        if job["render"]["status"] not in ("running", "done"):
            processor.start_render(job_id)
        return processor.get(job_id)

    @app.get("/api/jobs/{job_id}/output")
    def download_output(job_id: str):
        job = require_ready(job_id)
        if job["render"]["status"] != "done":
            raise HTTPException(404, "ویدیو هنوز ساخته نشده است.")
        return FileResponse(
            processor.job_dir(job_id) / "output.mp4",
            media_type="video/mp4",
            filename=f"{stem(job)}-subtitled.mp4",
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
