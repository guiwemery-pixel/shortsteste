"""Fila de processamento: cada link vira um job que roda em segundo plano."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import anthropic

from . import captions, highlights, video, whisper, youtube
from .backgrounds import BackgroundError, BackgroundLibrary
from .config import Settings

log = logging.getLogger(__name__)

STAGES = ["metadata", "transcript", "download", "highlights", "render"]


@dataclass
class Job:
    id: str
    url: str
    options: dict[str, Any]
    status: str = "queued"  # queued | running | done | error
    stage: str | None = None
    progress: float = 0.0
    message: str = "Na fila…"
    warnings: list[str] = field(default_factory=list)
    video: dict[str, Any] | None = None
    method: str | None = None
    transcript_source: str | None = None
    clips: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Services:
    """Dependências externas do pipeline (substituíveis nos testes)."""

    fetch_metadata: Callable[[str, Settings], youtube.VideoMeta] = youtube.fetch_metadata
    fetch_transcript: Callable[..., tuple[list, str | None]] = youtube.fetch_transcript
    download_video: Callable[..., Path] = youtube.download_video
    claude_factory: Callable[[], anthropic.Anthropic] | None = None


class JobStore:
    def __init__(self, settings: Settings, services: Services, backgrounds: BackgroundLibrary):
        self.settings = settings
        self.services = services
        self.backgrounds = backgrounds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="job")
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # -- persistência -------------------------------------------------------

    def job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _save(self, job: Job) -> None:
        path = self.job_dir(job.id) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> None:
        cutoff = time.time() - self.settings.job_ttl_hours * 3600
        for path in self.settings.jobs_dir.glob("*/job.json"):
            try:
                job = Job(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if job.updated_at < cutoff:
                shutil.rmtree(path.parent, ignore_errors=True)
                continue
            if job.status in ("queued", "running"):
                job.status, job.error = "error", "O servidor reiniciou durante o processamento. Tente de novo."
                self._save(job)
            self._jobs[job.id] = job

    def cleanup(self) -> None:
        cutoff = time.time() - self.settings.job_ttl_hours * 3600
        with self._lock:
            expired = [j for j in self._jobs.values() if j.updated_at < cutoff and j.status in ("done", "error")]
            for job in expired:
                del self._jobs[job.id]
        for job in expired:
            shutil.rmtree(self.job_dir(job.id), ignore_errors=True)

    # -- API ----------------------------------------------------------------

    def create(self, url: str, options: dict[str, Any]) -> Job:
        self.cleanup()
        job = Job(id=uuid.uuid4().hex, url=url, options=options)
        self.job_dir(job.id).mkdir(parents=True)
        with self._lock:
            self._jobs[job.id] = job
            self._save(job)
        self._executor.submit(self._run, job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            self._save(job)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- pipeline -----------------------------------------------------------

    def _stage(self, job_id: str, stage: str, message: str, progress: float) -> None:
        self.update(job_id, status="running", stage=stage, message=message, progress=round(progress, 3))

    def _run(self, job_id: str) -> None:
        try:
            self._run_pipeline(job_id)
        finally:
            # O vídeo original baixado ocupa muito espaço; só os cortes ficam guardados.
            for leftover in self.job_dir(job_id).glob("source.*"):
                leftover.unlink(missing_ok=True)

    def _run_pipeline(self, job_id: str) -> None:
        try:
            self._pipeline(job_id)
        except (youtube.YouTubeError, video.VideoError, BackgroundError) as exc:
            self.update(job_id, status="error", error=str(exc), message="Não foi possível concluir.")
        except Exception:  # noqa: BLE001 - qualquer falha inesperada vira erro visível no job
            log.exception("Falha inesperada no job %s", job_id)
            self.update(
                job_id, status="error", error="Erro inesperado ao processar o vídeo.", message="Não foi possível concluir."
            )

    def _pipeline(self, job_id: str) -> None:
        job = self.get(job_id)
        assert job is not None
        opts = job.options
        settings, services = self.settings, self.services
        workdir = self.job_dir(job_id)
        warnings: list[str] = []

        self._stage(job_id, "metadata", "Lendo informações do vídeo…", 0.02)
        meta = services.fetch_metadata(job.url, settings)
        if meta.duration <= 0:
            raise youtube.YouTubeError("Não conseguimos descobrir a duração do vídeo.")
        if meta.duration > settings.max_video_minutes * 60:
            raise youtube.YouTubeError(
                f"O vídeo tem mais de {settings.max_video_minutes} minutos; escolha um vídeo menor."
            )
        self.update(
            job_id,
            video={
                "id": meta.id,
                "title": meta.title,
                "channel": meta.channel,
                "duration": meta.duration,
                "thumbnail": meta.thumbnail,
                "url": meta.url,
            },
        )

        self._stage(job_id, "transcript", "Buscando a transcrição…", 0.06)
        segments, source = services.fetch_transcript(meta, settings)

        def on_download(fraction: float) -> None:
            self._stage(job_id, "download", f"Baixando o vídeo… {int(fraction * 100)}%", 0.10 + 0.35 * fraction)

        self._stage(job_id, "download", "Baixando o vídeo…", 0.10)
        source_path = services.download_video(meta.url, workdir, settings, on_download)
        info = video.probe(source_path)
        duration = min(meta.duration, info.duration) if info.duration else meta.duration

        if not segments and settings.whisper_model and whisper.available():
            self._stage(job_id, "transcript", "Transcrevendo o áudio…", 0.47)
            segments = whisper.transcribe(source_path, settings.whisper_model)
            source = f"transcrição local ({settings.whisper_model})" if segments else None
        if not segments:
            warnings.append("Não encontramos legenda neste vídeo, então as legendas nos cortes ficaram de fora.")
        self.update(job_id, transcript_source=source)

        self._stage(job_id, "highlights", "Escolhendo os melhores momentos…", 0.50)
        ctx = highlights.VideoContext(
            title=meta.title,
            duration=duration,
            channel=meta.channel,
            description=meta.description,
            language=meta.language,
            chapters=meta.chapters,
            heatmap=meta.heatmap,
        )
        picks, method, pick_warnings = highlights.select_highlights(
            ctx,
            segments,
            count=opts["max_clips"],
            min_len=opts["min_duration"],
            max_len=opts["max_duration"],
            client_factory=services.claude_factory,
            model=settings.claude_model,
            effort=settings.claude_effort,
        )
        warnings += pick_warnings
        if not picks:
            raise video.VideoError("Não encontramos trechos para cortar neste vídeo.")
        self.update(job_id, method=method, warnings=warnings)

        mode = opts["format"]
        background = self.backgrounds.get(opts["background"]) if mode == "split" else None
        remove_silence = bool(opts.get("remove_silence")) and info.has_audio
        if opts.get("remove_silence") and not info.has_audio:
            warnings.append("O vídeo não tem áudio, então não deu para cortar os silêncios.")
            self.update(job_id, warnings=warnings)
        width, height = video.output_size(info, mode, settings.output_width)

        clips: list[dict[str, Any]] = []
        for i, pick in enumerate(picks, start=1):
            self._stage(
                job_id, "render", f"Gerando corte {i} de {len(picks)}…", 0.55 + 0.45 * (i - 1) / len(picks)
            )
            name = f"clip_{i:02d}"
            out = workdir / f"{name}.mp4"
            intervals = [(pick.start, pick.end)]
            if remove_silence:
                silences = video.detect_silences(source_path, pick.start, pick.end)
                intervals = video.speech_intervals(pick.start, pick.end, silences)
            ass = None
            if opts["captions"] and segments:
                ass = captions.build_ass(segments, intervals, width, height, center=mode == "split")
            srt = captions.build_srt(segments, intervals) if segments else None
            if srt:
                (workdir / f"{name}.srt").write_text(srt, encoding="utf-8")
            final_duration = video.render_clip(
                source_path,
                info,
                out,
                intervals,
                mode,
                settings.output_width,
                ass,
                mirror=bool(opts.get("mirror")),
                background=background,
            )
            video.thumbnail(out, workdir / f"{name}.jpg")
            clips.append(
                {
                    **pick.to_dict(),
                    "rank": i,
                    "file": f"{name}.mp4",
                    "thumbnail": f"{name}.jpg",
                    "subtitles": f"{name}.srt" if srt else None,
                    "captions": bool(ass),
                    "final_duration": round(final_duration, 2),
                    "silence_cuts": len(intervals) - 1,
                }
            )
            self.update(job_id, clips=list(clips))

        source_path.unlink(missing_ok=True)
        self.update(job_id, status="done", stage=None, progress=1.0, message="Pronto!", clips=clips)
