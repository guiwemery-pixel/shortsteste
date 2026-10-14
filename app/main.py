"""Servidor web: API de jobs + arquivos estáticos do site."""

from __future__ import annotations

import logging
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import video, whisper
from .backgrounds import BackgroundError, BackgroundLibrary
from .config import HARD_MAX_CLIP_SECONDS, MAX_CLIPS, MIN_CLIP_SECONDS, Settings, load_dotenv
from .files import safe_filename, unique_names
from .jobs import JobStore, Services
from .youtube import InvalidURL, canonical_url

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class JobRequest(BaseModel):
    url: str = Field(max_length=500)
    max_clips: int = Field(5, ge=1, le=MAX_CLIPS)
    max_duration: int = Field(60, ge=MIN_CLIP_SECONDS, le=HARD_MAX_CLIP_SECONDS)
    format: Literal["vertical_blur", "vertical_crop", "split", "original"] = "vertical_blur"
    captions: bool = True
    mirror: bool = False
    remove_silence: bool = False
    background: str = Field("fractal", max_length=40)


def _download_names(clips: list[dict]) -> list[str]:
    """Nome de cada corte para download: o título sugerido, sem repetir."""
    return unique_names([safe_filename(c.get("title", ""), f"corte-{c['rank']}") for c in clips])


def _post_text(clip: dict) -> str:
    """Texto de apoio para postar: título e as legendas sugeridas."""
    options = clip.get("caption_options") or ([clip["caption"]] if clip.get("caption") else [])
    lines = [f"Título: {clip.get('title', '')}", ""]
    if options:
        lines.append("Legendas sugeridas:")
        for i, text in enumerate(options, start=1):
            lines += ["", f"{i})", text]
    return "\r\n".join("\r\n".join(line.splitlines()) if line else "" for line in lines) + "\r\n"


def _public(job) -> dict:
    data = job.to_dict()
    for clip, name in zip(data["clips"], _download_names(data["clips"])):
        base = f"/api/jobs/{job.id}/clips/{clip['rank']}/download"
        clip["download_name"] = name
        clip["url"] = f"/media/{job.id}/{clip['file']}"
        clip["download_url"] = base
        clip["thumbnail_url"] = f"/media/{job.id}/{clip['thumbnail']}"
        clip["subtitles_url"] = f"{base}?kind=srt" if clip.get("subtitles") else None
    data["download_all_url"] = f"/api/jobs/{job.id}/download.zip" if job.status == "done" and job.clips else None
    return data


def _background_public(item: dict) -> dict:
    return {**item, "thumbnail_url": f"/bg/{item['thumbnail']}" if item.get("thumbnail") else None}


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if services is None:
        services = Services(claude_factory=anthropic.Anthropic if settings.claude_enabled else None)
    backgrounds = BackgroundLibrary(settings.data_dir / "backgrounds", settings.max_background_mb)
    store = JobStore(settings, services, backgrounds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.shutdown()

    app = FastAPI(title="CorteCerto", lifespan=lifespan)
    app.state.store = store

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "ffmpeg": video.ffmpeg_available(),
            "claude": services.claude_factory is not None,
            "whisper": bool(settings.whisper_model and whisper.available()),
            "max_clip_seconds": HARD_MAX_CLIP_SECONDS,
        }

    @app.post("/api/jobs", status_code=202)
    def create_job(request: JobRequest) -> dict:
        try:
            url = canonical_url(request.url)
        except InvalidURL as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if request.format == "split" and not backgrounds.exists(request.background):
            raise HTTPException(status_code=400, detail="Escolha um vídeo de fundo para a tela dividida.")
        options = request.model_dump(exclude={"url"})
        options["min_duration"] = min(MIN_CLIP_SECONDS, request.max_duration)
        job = store.create(url, options)
        return _public(job)

    def job_or_404(job_id: str):
        job = store.get(job_id) if job_id.isalnum() else None
        if job is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado ou expirado.")
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return _public(job_or_404(job_id))

    @app.get("/api/jobs/{job_id}/clips/{rank}/download")
    def download_clip(job_id: str, rank: int, kind: Literal["video", "srt"] = "video") -> FileResponse:
        job = job_or_404(job_id)
        clips = list(job.clips)
        for clip, name in zip(clips, _download_names(clips)):
            if clip["rank"] != rank:
                continue
            file = clip["file"] if kind == "video" else clip.get("subtitles")
            path = store.job_dir(job.id) / file if file else None
            if path and path.is_file():
                return FileResponse(path, filename=f"{name}{path.suffix}")
        raise HTTPException(status_code=404, detail="Corte não encontrado.")

    @app.get("/api/jobs/{job_id}/download.zip")
    def download_all(job_id: str) -> FileResponse:
        job = job_or_404(job_id)
        clips = list(job.clips)
        if job.status != "done" or not clips:
            raise HTTPException(status_code=409, detail="Os cortes ainda não estão prontos.")
        job_dir = store.job_dir(job.id)
        path = job_dir / "cortes.zip"
        if not path.is_file():
            tmp = job_dir / f"cortes-{uuid.uuid4().hex}.tmp"
            # Vídeos já são comprimidos; ZIP_STORED só agrupa, sem gastar tempo comprimindo de novo.
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as archive:
                for clip, name in zip(clips, _download_names(clips)):
                    archive.write(job_dir / clip["file"], f"{name}.mp4")
                    if clip.get("subtitles") and (job_dir / clip["subtitles"]).is_file():
                        archive.write(job_dir / clip["subtitles"], f"{name}.srt")
                    archive.writestr(f"{name}.txt", _post_text(clip))
            tmp.replace(path)
        title = (job.video or {}).get("title") or ""
        return FileResponse(path, filename=safe_filename(f"{title} - cortes", "cortes") + ".zip")

    @app.get("/api/backgrounds")
    def list_backgrounds() -> list[dict]:
        return [_background_public(item) for item in backgrounds.list()]

    @app.post("/api/backgrounds", status_code=201)
    def upload_background(file: UploadFile = File(...), name: str | None = Form(None)) -> dict:
        try:
            item = backgrounds.add(file.file, file.filename or "", name)
        except BackgroundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _background_public(item)

    @app.delete("/api/backgrounds/{background_id}", status_code=204)
    def delete_background(background_id: str) -> None:
        try:
            backgrounds.delete(background_id)
        except BackgroundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.mount("/media", StaticFiles(directory=settings.jobs_dir), name="media")
    app.mount("/bg", StaticFiles(directory=backgrounds.directory), name="backgrounds")
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="site")
    return app


def run() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path.cwd() / ".env")
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
