"""Servidor web: API de jobs + arquivos estáticos do site."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import video, whisper
from .backgrounds import BackgroundError, BackgroundLibrary
from .config import HARD_MAX_CLIP_SECONDS, MIN_CLIP_SECONDS, Settings
from .jobs import JobStore, Services
from .youtube import InvalidURL, canonical_url

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class JobRequest(BaseModel):
    url: str = Field(max_length=500)
    max_clips: int = Field(5, ge=1, le=10)
    max_duration: int = Field(60, ge=MIN_CLIP_SECONDS, le=HARD_MAX_CLIP_SECONDS)
    format: Literal["vertical_blur", "vertical_crop", "split", "original"] = "vertical_blur"
    captions: bool = True
    mirror: bool = False
    remove_silence: bool = False
    background: str = Field("fractal", max_length=40)


def _public(job) -> dict:
    data = job.to_dict()
    for clip in data["clips"]:
        clip["url"] = f"/media/{job.id}/{clip['file']}"
        clip["thumbnail_url"] = f"/media/{job.id}/{clip['thumbnail']}"
        clip["subtitles_url"] = f"/media/{job.id}/{clip['subtitles']}" if clip.get("subtitles") else None
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

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get(job_id) if job_id.isalnum() else None
        if job is None:
            raise HTTPException(status_code=404, detail="Processamento não encontrado ou expirado.")
        return _public(job)

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
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
