import io
import shutil
import time
import zipfile
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import Services
from app.main import create_app
from app.transcript import Segment
from app.youtube import VideoMeta, YouTubeError

from .conftest import needs_ffmpeg
from .fakes import FakeClaude


def fake_meta(url, settings):
    return VideoMeta(
        id="dQw4w9WgXcQ",
        url=url,
        title="Vídeo de teste",
        channel="Canal",
        duration=120,
        description="",
        language="pt",
        thumbnail=None,
        heatmap=[{"start_time": t, "end_time": t + 6, "value": 0.2} for t in range(0, 120, 6)],
    )


def fake_transcript(meta, settings):
    segments = [Segment(float(t), float(t + 4), f"trecho {t} com algumas palavras") for t in range(0, 120, 4)]
    return segments, "legenda automática (pt-orig)"


def fake_download_from(sample):
    def download(url, dest, settings, on_progress=None):
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        target = dest / "source.mp4"
        shutil.copy(sample, target)
        return target

    return download


def make_client(tmp_path, services):
    settings = Settings(data_dir=tmp_path, output_width=360)
    return TestClient(create_app(settings, services))


def wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    pytest.fail("job não terminou a tempo")


def test_rejects_non_youtube_links(tmp_path):
    client = make_client(tmp_path, Services())
    response = client.post("/api/jobs", json={"url": "https://vimeo.com/1"})
    assert response.status_code == 400
    assert "YouTube" in response.json()["detail"]


def test_rejects_clips_longer_than_90_seconds(tmp_path):
    client = make_client(tmp_path, Services())
    response = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "max_duration": 120})
    assert response.status_code == 422


def test_unknown_job_is_404(tmp_path):
    client = make_client(tmp_path, Services())
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404
    assert client.get("/api/jobs/..%2Fetc").status_code == 404


def test_serves_site_and_health(tmp_path):
    client = make_client(tmp_path, Services())
    page = client.get("/")
    assert page.status_code == 200 and "CorteCerto" in page.text
    health = client.get("/api/health").json()
    assert health["claude"] is False and health["max_clip_seconds"] == 90


def test_youtube_error_is_reported(tmp_path):
    def broken(url, settings):
        raise YouTubeError("Este vídeo é privado.")

    client = make_client(tmp_path, Services(fetch_metadata=broken))
    job = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    done = wait(client, job["id"])
    assert done["status"] == "error"
    assert done["error"] == "Este vídeo é privado."


@needs_ffmpeg
def test_full_pipeline_with_claude(tmp_path, sample_video):
    claude = FakeClaude(
        clips=[
            {
                "start": 20, "end": 50, "title": "Primeiro", "hook": "Olha isso", "reason": "Bom gancho.", "score": 90,
                "captions": ["Curiosidade #a", "Direta #b", "Pergunta? #c"],
            },
            {"start": 70, "end": 95, "title": "Segundo", "hook": "E agora", "reason": "Virada.", "score": 75},
        ]
    )
    services = Services(
        fetch_metadata=fake_meta,
        fetch_transcript=fake_transcript,
        download_video=fake_download_from(sample_video),
        claude_factory=lambda: claude,
    )
    client = make_client(tmp_path, services)
    created = client.post(
        "/api/jobs",
        json={"url": "https://www.youtube.com/shorts/dQw4w9WgXcQ", "max_clips": 2, "max_duration": 45},
    )
    assert created.status_code == 202
    job = wait(client, created.json()["id"])
    assert job["status"] == "done", job
    assert job["method"] == "claude"
    assert job["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert [c["title"] for c in job["clips"]] == ["Primeiro", "Segundo"]
    for clip in job["clips"]:
        assert clip["captions"] is True
        assert 15 <= clip["duration"] <= 45
        media = client.get(clip["url"])
        assert media.status_code == 200 and len(media.content) > 1000
        assert client.get(clip["thumbnail_url"]).status_code == 200
        # Download com o título sugerido como nome do arquivo.
        assert clip["download_name"] == clip["title"]
        download = client.get(clip["download_url"])
        assert download.status_code == 200 and download.content == media.content
        disposition = unquote(download.headers["content-disposition"])
        assert disposition.startswith("attachment") and f"{clip['title']}.mp4" in disposition
        srt = client.get(clip["subtitles_url"])
        assert srt.status_code == 200 and f"{clip['title']}.srt" in unquote(srt.headers["content-disposition"])

    bundle = client.get(job["download_all_url"])
    assert bundle.status_code == 200
    assert "Vídeo de teste - cortes.zip" in unquote(bundle.headers["content-disposition"])
    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    assert sorted(archive.namelist()) == [
        "Primeiro.mp4", "Primeiro.srt", "Primeiro.txt", "Segundo.mp4", "Segundo.srt", "Segundo.txt",
    ]
    post_text = archive.read("Primeiro.txt").decode("utf-8")
    assert "Título: Primeiro" in post_text and "Direta #b" in post_text and "3)" in post_text
    assert job["clips"][0]["caption_options"] == ["Curiosidade #a", "Direta #b", "Pergunta? #c"]
    assert job["clips"][1]["caption_options"] == []
    assert client.get(f"/api/jobs/{job['id']}/clips/99/download").status_code == 404
    # O vídeo original é apagado depois dos cortes.
    assert not (tmp_path / "jobs" / job["id"] / "source.mp4").exists()
    # A seleção pediu no máximo 45s.
    assert "between 15 and 45 seconds" in claude.calls[0]["messages"][0]["content"]


@needs_ffmpeg
def test_job_survives_restart(tmp_path, sample_video):
    services = Services(
        fetch_metadata=fake_meta, fetch_transcript=fake_transcript, download_video=fake_download_from(sample_video)
    )
    client = make_client(tmp_path, services)
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "max_clips": 1}).json()["id"]
    assert wait(client, job_id)["status"] == "done"

    restarted = make_client(tmp_path, services)
    job = restarted.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["method"] == "heatmap" and len(job["clips"]) == 1


def test_backgrounds_listing_and_validation(tmp_path):
    client = make_client(tmp_path, Services())
    items = client.get("/api/backgrounds").json()
    assert [i["id"] for i in items] == ["fractal", "vida", "gradiente"]
    response = client.post(
        "/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "format": "split", "background": "nao-existe"}
    )
    assert response.status_code == 400
    bad = client.post("/api/backgrounds", files={"file": ("x.txt", b"oi", "text/plain")})
    assert bad.status_code == 400
    assert client.delete("/api/backgrounds/upload_nao_existe").status_code == 404


@needs_ffmpeg
def test_upload_background_and_split_pipeline(tmp_path, sample_video):
    services = Services(
        fetch_metadata=fake_meta, fetch_transcript=fake_transcript, download_video=fake_download_from(sample_video)
    )
    client = make_client(tmp_path, services)
    with sample_video.open("rb") as fh:
        uploaded = client.post(
            "/api/backgrounds", files={"file": ("meu-gameplay.mp4", fh, "video/mp4")}, data={"name": "Meu gameplay"}
        )
    assert uploaded.status_code == 201, uploaded.text
    background = uploaded.json()
    assert background["name"] == "Meu gameplay" and background["kind"] == "upload"
    assert client.get(background["thumbnail_url"]).status_code == 200
    assert background["id"] in [b["id"] for b in client.get("/api/backgrounds").json()]

    job = client.post(
        "/api/jobs",
        json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "max_clips": 1,
            "max_duration": 20,
            "format": "split",
            "background": background["id"],
            "mirror": True,
            "remove_silence": True,
        },
    ).json()
    done = wait(client, job["id"])
    assert done["status"] == "done", done
    [clip] = done["clips"]
    assert "kind=srt" in clip["subtitles_url"]
    assert client.get(clip["subtitles_url"]).text.startswith("1\n")
    assert clip["caption"] == clip["caption_options"][0]  # legendas do post (modo automático)
    assert len(clip["caption_options"]) == 3
    assert clip["final_duration"] <= 20

    assert client.delete(f"/api/backgrounds/{background['id']}").status_code == 204
    assert background["id"] not in [b["id"] for b in client.get("/api/backgrounds").json()]


def test_downloaded_source_is_removed_when_job_fails(tmp_path, sample_video):
    def download_then_fail(url, dest, settings, on_progress=None):
        shutil.copy(sample_video, dest / "source.mp4")
        raise YouTubeError("Falha simulada depois do download.")

    services = Services(fetch_metadata=fake_meta, fetch_transcript=fake_transcript, download_video=download_then_fail)
    client = make_client(tmp_path, services)
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()["id"]
    done = wait(client, job_id)
    assert done["status"] == "error" and done["error"] == "Falha simulada depois do download."
    assert not list((tmp_path / "jobs" / job_id).glob("source.*"))


def test_accepts_up_to_20_clips(tmp_path):
    def broken(url, settings):
        raise YouTubeError("sem rede nos testes")

    client = make_client(tmp_path, Services(fetch_metadata=broken))
    assert client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "max_clips": 20}).status_code == 202
    assert client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "max_clips": 21}).status_code == 422


def test_zip_only_when_done(tmp_path):
    def slow(url, settings):
        time.sleep(1)
        raise YouTubeError("ainda processando")

    client = make_client(tmp_path, Services(fetch_metadata=slow))
    job = client.post("/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    assert job["download_all_url"] is None
    assert client.get(f"/api/jobs/{job['id']}/download.zip").status_code == 409
