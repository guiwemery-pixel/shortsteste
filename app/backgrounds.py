"""Vídeos de fundo para a tela dividida (metade de baixo).

Os fundos prontos são animações geradas pelo próprio ffmpeg (sem direitos de terceiros).
O usuário também pode enviar os próprios vídeos, que ficam em DATA_DIR/backgrounds.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from . import video

BUILTIN = {
    "fractal": {
        "name": "Fractal hipnótico",
        "lavfi": "mandelbrot=size=360x320:rate=30",
    },
    "vida": {
        "name": "Jogo da vida",
        "lavfi": (
            "life=size=135x120:rate=30:mold=8:ratio=0.12:life_color=#ff3d6e:death_color=#1d1d2b,"
            "scale=540:480:flags=neighbor"
        ),
    },
    "gradiente": {
        "name": "Gradiente suave",
        "lavfi": "gradients=size=540x480:rate=30:speed=0.015:n=5",
    },
}

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


class BackgroundError(ValueError):
    pass


class BackgroundLibrary:
    def __init__(self, directory: Path, max_upload_mb: int = 500):
        self.directory = directory
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _builtin_thumbnail(self, key: str) -> str | None:
        path = self.directory / f"builtin_{key}.jpg"
        if not path.exists() and video.ffmpeg_available():
            try:
                video.lavfi_thumbnail(BUILTIN[key]["lavfi"], path)
            except video.VideoError:
                return None
        return path.name if path.exists() else None

    def list(self) -> list[dict]:
        items = [
            {"id": key, "name": item["name"], "kind": "builtin", "thumbnail": self._builtin_thumbnail(key)}
            for key, item in BUILTIN.items()
        ]
        uploads = []
        for meta_path in self.directory.glob("upload_*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            uploads.append(meta)
        uploads.sort(key=lambda m: m.get("created", 0), reverse=True)
        return items + [
            {"id": m["id"], "name": m["name"], "kind": "upload", "thumbnail": m.get("thumbnail")} for m in uploads
        ]

    def get(self, background_id: str) -> video.Background:
        if background_id in BUILTIN:
            return video.Background(lavfi=BUILTIN[background_id]["lavfi"])
        meta = self._meta(background_id)
        return video.Background(path=self.directory / meta["file"], duration=float(meta["duration"]))

    def exists(self, background_id: str) -> bool:
        if background_id in BUILTIN:
            return True
        try:
            self._meta(background_id)
        except BackgroundError:
            return False
        return True

    def _meta(self, background_id: str) -> dict:
        if not background_id.startswith("upload_") or not background_id[7:].isalnum():
            raise BackgroundError("Vídeo de fundo não encontrado.")
        path = self.directory / f"{background_id}.json"
        if not path.exists():
            raise BackgroundError("Vídeo de fundo não encontrado.")
        return json.loads(path.read_text(encoding="utf-8"))

    def add(self, stream: BinaryIO, filename: str, name: str | None = None) -> dict:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise BackgroundError("Envie um vídeo MP4, MOV, WEBM ou MKV.")
        background_id = f"upload_{uuid.uuid4().hex[:16]}"
        target = self.directory / f"{background_id}{suffix}"
        size = 0
        with target.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise BackgroundError(f"O vídeo passa de {self.max_bytes // (1024 * 1024)} MB.")
                out.write(chunk)
        try:
            info = video.probe(target)
        except video.VideoError as exc:
            target.unlink(missing_ok=True)
            raise BackgroundError("Não conseguimos ler esse vídeo.") from exc
        if info.duration < 3:
            target.unlink(missing_ok=True)
            raise BackgroundError("O vídeo de fundo precisa ter pelo menos 3 segundos.")
        thumb = self.directory / f"{background_id}.jpg"
        try:
            video.thumbnail(target, thumb, at=min(1.0, info.duration / 2))
        except video.VideoError:
            pass
        meta = {
            "id": background_id,
            "name": (name or Path(filename).stem or "Meu vídeo")[:60],
            "file": target.name,
            "duration": info.duration,
            "thumbnail": thumb.name if thumb.exists() else None,
            "created": time.time(),
        }
        (self.directory / f"{background_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return {"id": background_id, "name": meta["name"], "kind": "upload", "thumbnail": meta["thumbnail"]}

    def delete(self, background_id: str) -> None:
        meta = self._meta(background_id)
        with self._lock:
            for name in (meta["file"], meta.get("thumbnail"), f"{background_id}.json"):
                if name:
                    (self.directory / name).unlink(missing_ok=True)

