import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

BROWSER_CONTAINERS = {".mp4", ".m4v", ".mov", ".webm"}
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis"}
BROWSER_PIXELS = {"yuv420p", "yuvj420p"}

FFMPEG = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error"]


class MediaError(Exception):
    pass


@dataclass
class MediaInfo:
    width: int
    height: int
    duration: float
    has_audio: bool
    video_codec: str
    audio_codec: str | None
    pix_fmt: str


def parse_probe(data):
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or "width" not in video:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = video["width"], video["height"]
    rotation = next(
        (d["rotation"] for d in video.get("side_data_list", []) if "rotation" in d), 0
    )
    if abs(int(rotation)) % 180 == 90:
        width, height = height, width
    return MediaInfo(
        width=width,
        height=height,
        duration=float(data.get("format", {}).get("duration") or 0),
        has_audio=audio is not None,
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name") if audio else None,
        pix_fmt=video.get("pix_fmt", ""),
    )


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return parse_probe(json.loads(result.stdout))


def needs_preview_copy(extension, info):
    return not (
        extension in BROWSER_CONTAINERS
        and info.video_codec in BROWSER_VIDEO
        and info.pix_fmt in BROWSER_PIXELS
        and (info.audio_codec is None or info.audio_codec in BROWSER_AUDIO)
    )


def escape_filter_value(value):
    # ffmpeg unescapes twice: once for the filter option, once for the filtergraph.
    for char in "\\:'":
        value = value.replace(char, "\\" + char)
    for char in "\\'[],;":
        value = value.replace(char, "\\" + char)
    return value


def _run_with_progress(args, duration, on_progress, cwd=None):
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errors:
        process = subprocess.Popen(
            FFMPEG + ["-progress", "pipe:1", "-nostats"] + args,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            cwd=cwd,
        )
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit() and duration > 0:
                on_progress(min(max(int(value) / 1_000_000 / duration, 0), 1))
        if process.wait() != 0:
            errors.seek(0)
            raise MediaError(errors.read()[-800:])


def extract_audio(source, target):
    result = subprocess.run(
        FFMPEG + ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaError(result.stderr[-800:])


def make_preview(source, target, duration, on_progress):
    args = [
        "-i", str(source),
        "-vf", "scale=-2:'min(720,trunc(ih/2)*2)'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(target),
    ]
    _run_with_progress(args, duration, on_progress)


def burn_subtitles(source, subtitle_name, fonts_dir, target, duration, on_progress):
    """Render the ASS file onto the video. subtitle_name is relative to target's folder."""
    job_dir = os.path.dirname(os.path.abspath(target))
    fonts = escape_filter_value(os.path.relpath(fonts_dir, job_dir))
    subtitles = escape_filter_value(subtitle_name)
    args = [
        "-i", os.path.abspath(source),
        "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,ass=filename={subtitles}:fontsdir={fonts}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        os.path.basename(target),
    ]
    _run_with_progress(args, duration, on_progress, cwd=job_dir)
