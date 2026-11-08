import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

LANGUAGES = ["en", "fa", "de", "zh"]


@pytest.fixture(scope="session")
def video_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=10",
            "-f", "lavfi", "-i", "sine=f=440:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture
def translated_to():
    return []


@pytest.fixture
def client(tmp_path, translated_to):
    def transcribe(audio_path, language, quality, on_progress):
        on_progress(1.0)
        words = [
            {"start": 0.1, "end": 0.8, "text": " Hello."},
            {"start": 1.9, "end": 2.0, "text": " World"},
        ]
        return words, language if language != "auto" else "en"

    def make_translator(source, target):
        translated_to.append(target)
        return lambda lines: [f"{target}:{line}" for line in lines]

    app = create_app(
        data_dir=tmp_path,
        transcribe=transcribe,
        make_translator=make_translator,
        translate_languages=LANGUAGES,
        speech_languages=["en", "fa"],
    )
    with TestClient(app) as test_client:
        yield test_client


def upload(client, data, **params):
    query = {"name": "my clip.mp4", "speech": "auto", "lang1": "fa", "quality": "fast"} | params
    return client.post("/api/jobs", params=query, content=data)


def wait_for(client, job_id, predicate):
    deadline = time.time() + 30
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if predicate(job):
            return job
        time.sleep(0.05)
    raise AssertionError(f"timed out: {job}")


def ready_job(client, data, **params):
    response = upload(client, data, **params)
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    job = wait_for(client, job_id, lambda j: j["status"] in ("ready", "failed"))
    assert job["status"] == "ready", job
    return job


def test_languages_are_listed(client):
    body = client.get("/api/languages").json()
    assert body == {"translate": LANGUAGES, "speech": ["en", "fa"]}


def test_upload_produces_subtitles_in_two_languages(client, video_bytes):
    job = ready_job(client, video_bytes, lang2="de")
    cues = client.get(f"/api/jobs/{job['id']}/cues").json()
    assert cues[0]["texts"] == {"fa": "fa:Hello.", "de": "de:Hello."}
    assert cues[1]["texts"] == {"fa": "fa:World", "de": "de:World"}


def test_one_language_is_enough(client, video_bytes):
    job = ready_job(client, video_bytes)
    cues = client.get(f"/api/jobs/{job['id']}/cues").json()
    assert set(cues[0]["texts"]) == {"fa"}


def test_language_matching_speech_is_not_translated(client, video_bytes, translated_to):
    job = ready_job(client, video_bytes, speech="en", lang1="en", lang2="fa")
    cues = client.get(f"/api/jobs/{job['id']}/cues").json()
    assert cues[0]["texts"] == {"en": "Hello.", "fa": "fa:Hello."}
    assert translated_to == ["fa"]


def test_job_reports_video_size_and_duration(client, video_bytes):
    job = ready_job(client, video_bytes)
    assert (job["width"], job["height"]) == (320, 180)
    assert job["duration"] == pytest.approx(2, abs=0.2)


def test_same_language_twice_is_rejected(client, video_bytes):
    response = upload(client, video_bytes, lang1="fa", lang2="fa")
    assert response.status_code == 400


def test_unsupported_language_is_rejected(client, video_bytes):
    assert upload(client, video_bytes, lang1="klingon").status_code == 400


def test_missing_first_language_is_rejected(client, video_bytes):
    assert upload(client, video_bytes, lang1="").status_code == 400


def test_file_that_is_not_a_video_is_rejected(client):
    response = upload(client, b"this is not a video")
    assert response.status_code == 400
    assert "ویدیو" in response.json()["detail"]


def test_no_speech_fails_with_clear_message(tmp_path, video_bytes):
    app = create_app(
        data_dir=tmp_path,
        transcribe=lambda audio, language, quality, on_progress: ([], "en"),
        make_translator=lambda source, target: lambda lines: lines,
        translate_languages=LANGUAGES,
        speech_languages=["en"],
    )
    with TestClient(app) as test_client:
        job_id = upload(test_client, video_bytes).json()["id"]
        job = wait_for(test_client, job_id, lambda j: j["status"] == "failed")
    assert "گفتار" in job["error"]


def test_translation_failure_is_reported(tmp_path, video_bytes):
    def broken(lines):
        raise RuntimeError("model missing")

    app = create_app(
        data_dir=tmp_path,
        transcribe=lambda audio, language, quality, on_progress: (
            [{"start": 0, "end": 1, "text": " Hi"}],
            "en",
        ),
        make_translator=lambda source, target: broken,
        translate_languages=LANGUAGES,
        speech_languages=["en"],
    )
    with TestClient(app) as test_client:
        job_id = upload(test_client, video_bytes).json()["id"]
        job = wait_for(test_client, job_id, lambda j: j["status"] == "failed")
    assert "ترجمه" in job["error"]


def test_detected_language_without_translation_support_is_reported(tmp_path, video_bytes):
    app = create_app(
        data_dir=tmp_path,
        transcribe=lambda audio, language, quality, on_progress: (
            [{"start": 0, "end": 1, "text": " Hi"}],
            "xx",
        ),
        make_translator=lambda source, target: lambda lines: lines,
        translate_languages=LANGUAGES,
        speech_languages=["en"],
    )
    with TestClient(app) as test_client:
        job_id = upload(test_client, video_bytes).json()["id"]
        job = wait_for(test_client, job_id, lambda j: j["status"] == "failed")
    assert "ترجمه" in job["error"]


def test_video_without_sound_is_rejected(client, tmp_path):
    path = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=160x90:d=1:r=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    response = upload(client, path.read_bytes())
    assert response.status_code == 400
    assert "صدا" in response.json()["detail"]


def test_edited_text_reaches_the_srt_download(client, video_bytes):
    job = ready_job(client, video_bytes)
    url = f"/api/jobs/{job['id']}"
    cues = client.get(f"{url}/cues").json()
    cues[0]["texts"]["fa"] = "ویرایش شده"
    assert client.put(f"{url}/cues", json=cues).status_code == 200

    response = client.get(f"{url}/srt/fa")
    assert response.status_code == 200
    assert "ویرایش شده" in response.content.decode("utf-8-sig")
    assert "attachment" in response.headers["content-disposition"]


def test_editing_cannot_change_cue_count_or_timing(client, video_bytes):
    job = ready_job(client, video_bytes)
    url = f"/api/jobs/{job['id']}"
    cues = client.get(f"{url}/cues").json()
    assert client.put(f"{url}/cues", json=cues[:1]).status_code == 400
    cues[0]["start"] = 99
    client.put(f"{url}/cues", json=cues)
    assert client.get(f"{url}/cues").json()[0]["start"] != 99


def test_srt_of_language_not_in_job_is_404(client, video_bytes):
    job = ready_job(client, video_bytes)
    assert client.get(f"/api/jobs/{job['id']}/srt/de").status_code == 404


def test_render_creates_a_video_with_subtitles(client, video_bytes):
    job = ready_job(client, video_bytes, lang2="de")
    url = f"/api/jobs/{job['id']}"
    assert client.post(f"{url}/render").status_code == 200
    job = wait_for(client, job["id"], lambda j: j["render"]["status"] in ("done", "failed"))
    assert job["render"]["status"] == "done", job

    response = client.get(f"{url}/output")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content[4:8] == b"ftyp"


def test_editing_after_render_requires_a_new_render(client, video_bytes):
    job = ready_job(client, video_bytes)
    url = f"/api/jobs/{job['id']}"
    client.post(f"{url}/render")
    wait_for(client, job["id"], lambda j: j["render"]["status"] == "done")

    cues = client.get(f"{url}/cues").json()
    cues[0]["texts"]["fa"] = "تازه"
    client.put(f"{url}/cues", json=cues)

    assert client.get(url).json()["render"]["status"] == "none"
    assert client.get(f"{url}/output").status_code == 404


def test_cues_cannot_be_edited_during_render(client, video_bytes):
    job = ready_job(client, video_bytes)
    url = f"/api/jobs/{job['id']}"
    cues = client.get(f"{url}/cues").json()
    client.app.state.processor.update(job["id"], render={"status": "running", "progress": 0})
    assert client.put(f"{url}/cues", json=cues).status_code == 409


def test_render_works_when_folder_name_has_special_characters(tmp_path, video_bytes):
    app = create_app(
        data_dir=tmp_path / "it's: odd, [dir]",
        transcribe=lambda audio, language, quality, on_progress: (
            [{"start": 0, "end": 1, "text": " Hi"}],
            "fa",
        ),
        make_translator=lambda source, target: lambda lines: lines,
        translate_languages=LANGUAGES,
        speech_languages=["fa"],
    )
    with TestClient(app) as test_client:
        job = ready_job(test_client, video_bytes)
        test_client.post(f"/api/jobs/{job['id']}/render")
        job = wait_for(
            test_client, job["id"], lambda j: j["render"]["status"] in ("done", "failed")
        )
    assert job["render"]["status"] == "done", job


def test_unfinished_job_is_marked_failed_after_restart(tmp_path, video_bytes):
    make_app = lambda: create_app(
        data_dir=tmp_path,
        transcribe=lambda audio, language, quality, on_progress: (
            [{"start": 0, "end": 1, "text": " Hi"}],
            "fa",
        ),
        make_translator=lambda source, target: lambda lines: lines,
        translate_languages=LANGUAGES,
        speech_languages=["fa"],
    )
    app = make_app()
    with TestClient(app) as test_client:
        job = ready_job(test_client, video_bytes)
        app.state.processor.update(job["id"], status="listening")

    with TestClient(make_app()) as test_client:
        assert test_client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"


def test_preview_supports_seeking(client, video_bytes):
    job = ready_job(client, video_bytes)
    response = client.get(f"/api/jobs/{job['id']}/video", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    assert len(response.content) == 100


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404


def test_malformed_job_id_is_404(client):
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404


def test_homepage_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'dir="rtl"' in response.text
