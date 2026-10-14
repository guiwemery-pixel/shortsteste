"""Corte, edição e reenquadramento dos vídeos com ffmpeg."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FORMATS = ("vertical_blur", "vertical_crop", "split", "original")

Interval = tuple[float, float]


class VideoError(Exception):
    pass


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass
class Background:
    """Vídeo da metade de baixo na tela dividida: gerado (lavfi) ou enviado pelo usuário."""

    lavfi: str | None = None
    path: Path | None = None
    duration: float = 0.0

    def input_args(self, needed: float) -> list[str]:
        if self.lavfi:
            return ["-f", "lavfi", "-i", self.lavfi]
        assert self.path is not None
        # Começa num ponto aleatório para cada corte não repetir o mesmo trecho.
        offset = random.uniform(0, max(0.0, self.duration - needed)) if self.duration > needed else 0.0
        return ["-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", str(self.path.resolve())]


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise VideoError(f"ffmpeg falhou: {tail}")
    return proc


def probe(path: Path) -> VideoInfo:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise VideoError(f"Não foi possível ler o vídeo: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise VideoError("O arquivo não tem faixa de vídeo.")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = int(video["width"]), int(video["height"])
    # Vídeos gravados no celular podem vir com rotação nos metadados.
    rotation = 0
    for side in video.get("side_data_list", []):
        rotation = int(side.get("rotation", 0) or 0)
    if abs(rotation) in (90, 270):
        width, height = height, width
    return VideoInfo(width, height, duration, has_audio)


# ---------------------------------------------------------------------------
# Silêncios
# ---------------------------------------------------------------------------

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silences(stderr: str, offset: float, length: float) -> list[Interval]:
    silences: list[Interval] = []
    current: float | None = None
    for line in stderr.splitlines():
        if m := _SILENCE_START.search(line):
            current = max(0.0, float(m[1]))
        elif (m := _SILENCE_END.search(line)) and current is not None:
            silences.append((offset + current, offset + min(float(m[1]), length)))
            current = None
    if current is not None:
        silences.append((offset + current, offset + length))
    return silences


def detect_silences(
    source: Path, start: float, end: float, noise_db: int = -35, min_silence: float = 0.5
) -> list[Interval]:
    """Trechos sem fala (abaixo de noise_db por pelo menos min_silence segundos)."""
    proc = _run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(source.resolve()), "-map", "0:a:0",
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-",
        ],
        timeout=600,
    )
    return parse_silences(proc.stderr, start, end - start)


def speech_intervals(
    start: float, end: float, silences: list[Interval], pad: float = 0.15, min_keep: float = 0.25
) -> list[Interval]:
    """Partes do corte que ficam depois de tirar os silêncios (com uma folga para respiração)."""
    keep: list[Interval] = []
    cursor = start
    for s_start, s_end in sorted(silences):
        cut_start, cut_end = max(start, s_start + pad), min(end, s_end - pad)
        if cut_end - cut_start < 0.2:
            continue
        if cut_start - cursor >= min_keep:
            keep.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if end - cursor >= min_keep:
        keep.append((cursor, end))
    return keep or [(start, end)]


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def output_size(source: VideoInfo, mode: str, output_width: int) -> tuple[int, int]:
    if mode in ("vertical_blur", "vertical_crop", "split"):
        width = _even(output_width)
        return width, _even(width * 16 / 9)
    # Original: mantém a proporção, limitando o lado maior.
    longest = max(source.width, source.height)
    scale = min(1.0, (output_width * 16 / 9) / longest)
    return _even(source.width * scale), _even(source.height * scale)


def layout_filter(src: str, mode: str, width: int, height: int, mirror: bool, subtitles: str | None) -> str:
    """Filtro que leva o vídeo [src] (e o fundo [1:v], na tela dividida) até a saída [v]."""
    flip = "hflip," if mirror else ""
    if mode == "vertical_blur":
        chain = (
            f"[{src}]{flip}split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:2[bgb];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    elif mode == "vertical_crop":
        chain = (
            f"[{src}]{flip}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )
    elif mode == "split":
        top = _even(height / 2)
        bottom = height - top
        chain = (
            f"[{src}]{flip}scale={width}:{top}:force_original_aspect_ratio=increase,"
            f"crop={width}:{top},setsar=1,fps=30,format=yuv420p[top];"
            f"[1:v]scale={width}:{bottom}:force_original_aspect_ratio=increase,"
            f"crop={width}:{bottom},setsar=1,fps=30,format=yuv420p[bottom];"
            f"[top][bottom]vstack=inputs=2"
        )
    elif mode == "original":
        chain = f"[{src}]{flip}scale={width}:{height},setsar=1"
    else:
        raise ValueError(f"Formato desconhecido: {mode}")
    if subtitles:
        chain += f",subtitles={subtitles}"
    return chain + "[v]"


def concat_filter(intervals: list[Interval], origin: float, has_audio: bool) -> str:
    """Junta os intervalos (em tempo do vídeo original) em [vc]/[ac]."""
    n = len(intervals)
    parts = [f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n))]
    if has_audio:
        parts.append(f"[0:a]asplit={n}" + "".join(f"[r{i}]" for i in range(n)))
    pads = ""
    for i, (a, b) in enumerate(intervals):
        a, b = a - origin, b - origin
        parts.append(f"[s{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[r{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
            pads += f"[v{i}][a{i}]"
        else:
            pads += f"[v{i}]"
    parts.append(f"{pads}concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
    return ";".join(parts)


def render_clip(
    source: Path,
    source_info: VideoInfo,
    output: Path,
    intervals: list[Interval],
    mode: str,
    output_width: int,
    ass_content: str | None = None,
    mirror: bool = False,
    background: Background | None = None,
) -> float:
    """Gera o corte (H.264 + AAC). Retorna a duração final em segundos."""
    if mode == "split" and background is None:
        raise VideoError("Escolha um vídeo de fundo para a tela dividida.")
    width, height = output_size(source_info, mode, output_width)
    workdir = output.parent
    subtitles = None
    if ass_content:
        # Nome simples, relativo ao cwd do ffmpeg, para não precisar escapar o caminho no filtro.
        subtitles = f"{output.stem}.ass"
        (workdir / subtitles).write_text(ass_content, encoding="utf-8")

    origin, last = intervals[0][0], intervals[-1][1]
    final_duration = sum(b - a for a, b in intervals)
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{origin:.3f}", "-t", f"{last - origin:.3f}", "-i", str(source.resolve()),
    ]
    if mode == "split":
        args += background.input_args(final_duration)

    if len(intervals) == 1:
        graph = layout_filter("0:v", mode, width, height, mirror, subtitles)
        audio_map = ["-map", "0:a:0"] if source_info.has_audio else []
    else:
        graph = concat_filter(intervals, origin, source_info.has_audio) + ";" + layout_filter(
            "vc", mode, width, height, mirror, subtitles
        )
        audio_map = ["-map", "[ac]"] if source_info.has_audio else []

    args += ["-t", f"{final_duration:.3f}", "-filter_complex", graph, "-map", "[v]", *audio_map]
    if source_info.has_audio:
        args += ["-c:a", "aac", "-b:a", "160k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", output.name,
    ]
    _run(args, cwd=workdir)
    return final_duration


def thumbnail(clip: Path, output: Path, at: float = 1.0) -> None:
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(clip.resolve()),
            "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", str(output.resolve()),
        ],
        timeout=120,
    )


def lavfi_thumbnail(expr: str, output: Path) -> None:
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", expr,
            "-ss", "2", "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(output.resolve()),
        ],
        timeout=120,
    )
