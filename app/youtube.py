"""Integração com o YouTube via yt-dlp: metadados, legendas e download."""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from .config import Settings
from .transcript import Segment, parse_json3, parse_vtt

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}


class InvalidURL(ValueError):
    pass


class YouTubeError(Exception):
    pass


@dataclass
class VideoMeta:
    id: str
    url: str
    title: str
    channel: str
    duration: float
    description: str
    language: str | None
    thumbnail: str | None
    chapters: list[dict] = field(default_factory=list)
    heatmap: list[dict] = field(default_factory=list)
    subtitles: dict[str, list[dict]] = field(default_factory=dict)
    automatic_captions: dict[str, list[dict]] = field(default_factory=dict)


def video_id(url: str) -> str:
    """Extrai o ID de 11 caracteres de qualquer link de vídeo do YouTube."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in ("http", "https") or host not in _HOSTS:
        raise InvalidURL("Cole um link de vídeo do YouTube.")
    candidate = ""
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("youtu.be"):
        candidate = parts[0] if parts else ""
    elif parsed.path == "/watch":
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        candidate = parts[1]
    if not _ID.match(candidate):
        raise InvalidURL("Não encontramos o vídeo nesse link do YouTube.")
    return candidate


def canonical_url(url: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id(url)}"


def _js_runtimes() -> dict[str, dict]:
    runtimes: dict[str, dict] = {}
    deno = shutil.which("deno") or str(Path(sys.executable).parent / "deno")
    if Path(deno).exists():
        runtimes["deno"] = {"path": deno}
    if shutil.which("node"):
        runtimes["node"] = {}
    return runtimes or {"deno": {}}


def _base_options(settings: Settings) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "allowed_extractors": ["youtube"],
        "js_runtimes": _js_runtimes(),
        "socket_timeout": 30,
        "retries": 3,
    }
    if settings.cookies_file:
        options["cookiefile"] = settings.cookies_file
    return options


def _friendly(exc: Exception) -> YouTubeError:
    message = str(exc)
    if "Sign in to confirm" in message or "not a bot" in message:
        return YouTubeError(
            "O YouTube pediu login para liberar este vídeo neste servidor. "
            "Configure YTDLP_COOKIES_FILE (veja o README)."
        )
    if "Private video" in message:
        return YouTubeError("Este vídeo é privado.")
    if "age" in message.lower() and "confirm" in message.lower():
        return YouTubeError("Este vídeo tem restrição de idade; configure YTDLP_COOKIES_FILE.")
    if "is not available" in message or "Video unavailable" in message:
        return YouTubeError("Este vídeo não está disponível.")
    return YouTubeError(f"Não foi possível acessar o vídeo: {message[-300:]}")


def fetch_metadata(url: str, settings: Settings) -> VideoMeta:
    try:
        with yt_dlp.YoutubeDL({**_base_options(settings), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly(exc) from exc
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise YouTubeError("Transmissões ao vivo ainda não são suportadas; tente depois que terminar.")
    return VideoMeta(
        id=info["id"],
        url=url,
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=float(info.get("duration") or 0),
        description=info.get("description") or "",
        language=info.get("language"),
        thumbnail=info.get("thumbnail"),
        chapters=info.get("chapters") or [],
        heatmap=info.get("heatmap") or [],
        subtitles={k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"},
        automatic_captions=info.get("automatic_captions") or {},
    )


def _pick_track(meta: VideoMeta) -> tuple[str, list[dict], str] | None:
    """Escolhe a legenda no idioma original: manual primeiro, depois automática."""
    lang = (meta.language or "").lower()

    def matches(key: str) -> bool:
        key = key.lower()
        return bool(lang) and (key == lang or key.split("-")[0] == lang.split("-")[0])

    manual = meta.subtitles
    for key in manual:
        if matches(key):
            return key, manual[key], "manual"
    if manual and not lang:
        key = next(iter(manual))
        return key, manual[key], "manual"

    auto = meta.automatic_captions
    # "xx-orig" é a transcrição automática original, sem tradução.
    for key in auto:
        if key.endswith("-orig") and (not lang or matches(key[: -len("-orig")])):
            return key, auto[key], "auto"
    for key in auto:
        if matches(key):
            return key, auto[key], "auto"
    if manual:
        key = next(iter(manual))
        return key, manual[key], "manual"
    return None


def fetch_transcript(meta: VideoMeta, settings: Settings) -> tuple[list[Segment], str | None]:
    """Baixa a legenda do vídeo. Retorna (trechos, descrição da fonte)."""
    track = _pick_track(meta)
    if not track:
        return [], None
    key, formats, kind = track
    by_ext = {f.get("ext"): f for f in formats if f.get("url")}
    chosen = by_ext.get("json3") or by_ext.get("vtt")
    if not chosen:
        return [], None
    try:
        with yt_dlp.YoutubeDL(_base_options(settings)) as ydl:
            body = ydl.urlopen(chosen["url"]).read().decode("utf-8", errors="replace")
    except Exception:  # Falha de rede na legenda não deve derrubar o processamento.
        return [], None
    segments = parse_json3(json.loads(body)) if chosen["ext"] == "json3" else parse_vtt(body)
    label = f"legenda {'manual' if kind == 'manual' else 'automática'} ({key})"
    return segments, label


def download_video(
    url: str, dest: Path, settings: Settings, on_progress: Callable[[float], None] | None = None
) -> Path:
    height = settings.max_download_height

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    options = {
        **_base_options(settings),
        "format": (
            f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/bv*[height<={height}]+ba/"
            f"b[height<={height}]/b"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(dest / "source.%(ext)s"),
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly(exc) from exc
    files = [p for p in dest.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise YouTubeError("O download terminou sem gerar arquivo de vídeo.")
    return max(files, key=lambda p: p.stat().st_size)
