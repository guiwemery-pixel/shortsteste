"""Configurações lidas de variáveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Limite rígido pedido pelo produto: nenhum corte passa de 1min30.
HARD_MAX_CLIP_SECONDS = 90
MIN_CLIP_SECONDS = 15
# Quantidade máxima de cortes por vídeo.
MAX_CLIPS = 20


def load_dotenv(path: Path) -> None:
    """Lê KEY=valor de um arquivo .env sem sobrescrever variáveis já definidas."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "high"
    max_video_minutes: int = 180
    max_download_height: int = 1080
    output_width: int = 1080
    workers: int = 1
    job_ttl_hours: int = 24
    cookies_file: str | None = None
    whisper_model: str | None = None
    max_background_mb: int = 500

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def claude_enabled(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data")).resolve(),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
            claude_effort=os.environ.get("CLAUDE_EFFORT", "high"),
            max_video_minutes=_int("MAX_VIDEO_MINUTES", 180),
            max_download_height=_int("MAX_DOWNLOAD_HEIGHT", 1080),
            output_width=_int("OUTPUT_WIDTH", 1080),
            workers=max(1, _int("WORKERS", 1)),
            job_ttl_hours=_int("JOB_TTL_HOURS", 24),
            cookies_file=os.environ.get("YTDLP_COOKIES_FILE") or None,
            whisper_model=os.environ.get("WHISPER_MODEL") or None,
            max_background_mb=_int("MAX_BACKGROUND_MB", 500),
        )
