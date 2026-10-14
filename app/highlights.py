"""Escolha dos melhores momentos do vídeo.

Com ANTHROPIC_API_KEY configurada, o Claude lê a transcrição, os capítulos e o
mapa de "Mais repetidos" do YouTube e escolhe os cortes. Sem chave (ou se a
chamada falhar), um modo automático usa o mapa de calor e a densidade de fala.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable

import anthropic

from .config import HARD_MAX_CLIP_SECONDS, MIN_CLIP_SECONDS
from .transcript import Segment, format_for_prompt, text_between

log = logging.getLogger(__name__)


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    hook: str
    reason: str
    score: int
    caption: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "duration": round(self.duration, 2)}


@dataclass
class VideoContext:
    title: str
    duration: float
    channel: str = ""
    description: str = ""
    language: str | None = None
    chapters: list[dict] | None = None
    heatmap: list[dict] | None = None


class HighlightError(Exception):
    """A seleção com IA falhou; a mensagem é mostrada ao usuário como aviso."""


CLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                    "caption": {"type": "string"},
                },
                "required": ["start", "end", "title", "hook", "reason", "score", "caption"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a senior short-form video editor. You turn long YouTube videos into \
vertical shorts (YouTube Shorts, Reels, TikTok) that hold attention and stand on their own.

You receive a video's metadata, its chapters, the audience replay heatmap ("Most replayed", \
0 to 1, higher means more people rewatched that part) and a timestamped transcript where each \
line starts with its start time in seconds.

Pick the strongest moments: surprising facts, bold opinions, emotional peaks, humor, practical \
tips, story climaxes and quotable lines. Treat heatmap peaks as evidence of audience interest, \
but only pick a moment if the transcript shows it works as a clip.

Every clip must:
- start at the beginning of a sentence, with a hook in the first 3 seconds, using the start \
time of a transcript line;
- end right after the thought is complete, never mid-sentence, using the start time of the \
next transcript line (or the video end);
- make sense to someone who has not seen the rest of the video;
- respect the requested minimum and maximum duration;
- not overlap any other clip.

For each clip return: start and end in seconds; a catchy title of at most 60 characters in \
the video's language; a one-line hook caption in the video's language; one sentence in \
Brazilian Portuguese explaining why the moment works; a score from 1 to 100 for its \
short-form potential; and a ready-to-post social media caption in the video's language (one or \
two short sentences plus 3 to 5 relevant hashtags). Order clips from best to worst. Return fewer clips than requested when \
the video does not have enough strong moments."""


def _format_seconds(value: float) -> str:
    value = int(round(value))
    return f"{value // 60}:{value % 60:02d}"


def build_user_prompt(ctx: VideoContext, segments: list[Segment], count: int, min_len: int, max_len: int) -> str:
    parts = [
        f"Title: {ctx.title}",
        f"Channel: {ctx.channel or 'unknown'}",
        f"Duration: {ctx.duration:.0f} seconds ({_format_seconds(ctx.duration)})",
        f"Language: {ctx.language or 'unknown'}",
    ]
    if ctx.description:
        parts.append(f"Description:\n{ctx.description}")
    if ctx.chapters:
        lines = [
            f"- {c.get('start_time', 0):.0f}s to {c.get('end_time', 0):.0f}s: {c.get('title', '')}"
            for c in ctx.chapters
        ]
        parts.append("Chapters:\n" + "\n".join(lines))
    if ctx.heatmap:
        lines = [
            f"- {h.get('start_time', 0):.0f}s to {h.get('end_time', 0):.0f}s: {h.get('value', 0):.2f}"
            for h in ctx.heatmap
        ]
        parts.append("Replay heatmap:\n" + "\n".join(lines))
    parts.append("Transcript:\n" + format_for_prompt(segments))
    parts.append(
        f"Pick up to {count} clips. Each clip must last between {min_len} and {max_len} seconds."
    )
    return "\n\n".join(parts)


def select_with_claude(
    client: anthropic.Anthropic,
    model: str,
    effort: str,
    ctx: VideoContext,
    segments: list[Segment],
    count: int,
    min_len: int,
    max_len: int,
) -> list[Highlight]:
    prompt = build_user_prompt(ctx, segments, count, min_len, max_len)
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": CLIPS_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise HighlightError("A chave da API do Claude é inválida.") from exc
    except anthropic.RateLimitError as exc:
        raise HighlightError("Limite de uso da API do Claude atingido.") from exc
    except anthropic.APIStatusError as exc:
        raise HighlightError(f"A API do Claude respondeu com erro {exc.status_code}.") from exc
    except anthropic.APIConnectionError as exc:
        raise HighlightError("Não foi possível conectar à API do Claude.") from exc

    if response.stop_reason == "refusal":
        raise HighlightError("O Claude recusou analisar este vídeo.")
    if response.stop_reason == "max_tokens":
        raise HighlightError("A resposta do Claude veio incompleta.")
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        raw = json.loads(text)["clips"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HighlightError("A resposta do Claude veio em formato inesperado.") from exc

    highlights = []
    for item in raw:
        try:
            highlights.append(
                Highlight(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    title=str(item["title"]).strip(),
                    hook=str(item["hook"]).strip(),
                    reason=str(item["reason"]).strip(),
                    score=int(item["score"]),
                    caption=str(item.get("caption", "")).strip(),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return highlights


# ---------------------------------------------------------------------------
# Ajuste dos cortes: encaixa nas frases, respeita limites e remove sobreposição
# ---------------------------------------------------------------------------


def _snap_start(t: float, segments: list[Segment], tolerance: float = 1.5) -> float:
    for s in segments:
        if s.start <= t < s.end:
            return s.start if t - s.start <= tolerance else t
        if s.start > t:
            return s.start if s.start - t <= tolerance else t
    return t


def _snap_end(start: float, t: float, segments: list[Segment], min_len: float, max_len: float) -> float:
    """Leva o fim para o término de uma frase, sem passar de max_len."""
    limit = start + max_len
    containing = next((s for s in segments if s.start < t <= s.end), None)
    if containing and containing.end <= limit and containing.end - t <= 3:
        return containing.end
    ends = [s.end for s in segments if start + min_len <= s.end <= min(t, limit)]
    if ends:
        return ends[-1]
    return min(t, limit)


def normalize(
    highlights: list[Highlight],
    duration: float,
    segments: list[Segment],
    count: int,
    min_len: int,
    max_len: int,
) -> list[Highlight]:
    max_len = min(max_len, HARD_MAX_CLIP_SECONDS)
    min_len = min(min_len, max_len)
    fixed: list[Highlight] = []
    for h in highlights:
        start = max(0.0, min(h.start, duration))
        end = max(0.0, min(h.end, duration))
        if end - start < 1:
            continue
        if segments:
            start = _snap_start(start, segments)
        if end - start > max_len:
            end = start + max_len
        if segments:
            end = _snap_end(start, end, segments, min_len, max_len)
        if end - start < min_len:
            end = min(duration, start + min_len)
            if end - start < min_len:
                start = max(0.0, end - min_len)
        end = min(end, start + max_len, duration)
        if end - start < 1:
            continue
        fixed.append(
            Highlight(
                round(start, 2), round(end, 2), h.title, h.hook, h.reason, max(1, min(100, h.score)), h.caption
            )
        )

    chosen: list[Highlight] = []
    for h in sorted(fixed, key=lambda x: x.score, reverse=True):
        overlaps = any(min(h.end, c.end) - max(h.start, c.start) > 1 for c in chosen)
        if not overlaps:
            chosen.append(h)
        if len(chosen) == count:
            break
    return chosen


# ---------------------------------------------------------------------------
# Modo automático (sem IA)
# ---------------------------------------------------------------------------


def _interest_curve(duration: float, segments: list[Segment], heatmap: list[dict] | None) -> list[float]:
    n = max(1, math.ceil(duration))
    curve = [0.0] * n
    if heatmap:
        for marker in heatmap:
            a = int(marker.get("start_time", 0))
            b = int(math.ceil(marker.get("end_time", 0)))
            for sec in range(max(0, a), min(n, b)):
                curve[sec] = max(curve[sec], float(marker.get("value", 0)))
    elif segments:
        for s in segments:
            words = len(s.text.split())
            bonus = 1.5 if any(ch in s.text for ch in "?!") else 1.0
            a, b = int(s.start), max(int(s.start) + 1, int(math.ceil(s.end)))
            per_second = words / max(b - a, 1) * bonus
            for sec in range(max(0, a), min(n, b)):
                curve[sec] += per_second
    else:
        curve = [1.0] * n
    return curve


def heuristic_highlights(
    ctx: VideoContext, segments: list[Segment], count: int, min_len: int, max_len: int
) -> list[Highlight]:
    duration = ctx.duration
    window = int(max(min_len, min(max_len, 60)))
    window = min(window, max(1, int(duration)))
    curve = _interest_curve(duration, segments, ctx.heatmap)
    prefix = [0.0]
    for value in curve:
        prefix.append(prefix[-1] + value)

    uniform = not ctx.heatmap and not segments
    candidates: list[tuple[float, int]] = []
    if uniform:
        step = duration / (count + 1)
        for i in range(count):
            center = step * (i + 1)
            candidates.append((1.0, int(max(0, min(duration - window, center - window / 2)))))
    else:
        for start in range(0, max(1, len(curve) - window + 1)):
            candidates.append((prefix[start + window] - prefix[start], start))
        candidates.sort(reverse=True)

    best = max((c[0] for c in candidates), default=1.0) or 1.0
    picked: list[Highlight] = []
    for value, start in candidates:
        end = min(duration, start + window)
        if any(min(end, p.end) - max(start, p.start) > 0 for p in picked):
            continue
        snippet = text_between(segments, start, end)
        first_words = " ".join(snippet.split()[:8])
        title = (first_words + "…") if first_words else f"Momento {len(picked) + 1}"
        picked.append(
            Highlight(
                start=float(start),
                end=float(end),
                title=title,
                hook=first_words,
                reason=(
                    "Trecho mais revisto pelo público no YouTube."
                    if ctx.heatmap
                    else "Trecho com fala intensa e contínua."
                    if segments
                    else "Trecho distribuído ao longo do vídeo."
                ),
                score=max(1, int(round(100 * value / best))),
                caption=f"{title}\n\n#shorts #cortes",
            )
        )
        if len(picked) == count:
            break
    return picked


def select_highlights(
    ctx: VideoContext,
    segments: list[Segment],
    count: int,
    min_len: int,
    max_len: int,
    client_factory: Callable[[], anthropic.Anthropic] | None,
    model: str,
    effort: str,
) -> tuple[list[Highlight], str, list[str]]:
    """Retorna (cortes, método usado, avisos)."""
    warnings: list[str] = []
    max_len = min(max_len, HARD_MAX_CLIP_SECONDS, max(1, int(ctx.duration)))
    min_len = min(max(min_len, MIN_CLIP_SECONDS), max_len)

    if client_factory and segments:
        try:
            raw = select_with_claude(client_factory(), model, effort, ctx, segments, count, min_len, max_len)
            clips = normalize(raw, ctx.duration, segments, count, min_len, max_len)
            if clips:
                return clips, "claude", warnings
            warnings.append("O Claude não encontrou cortes válidos; usamos o modo automático.")
        except HighlightError as exc:
            log.warning("Seleção com Claude falhou: %s", exc)
            warnings.append(f"{exc} Usamos o modo automático.")
    elif client_factory and not segments:
        warnings.append("O vídeo não tem legendas; a IA precisa da transcrição, então usamos o modo automático.")

    raw = heuristic_highlights(ctx, segments, count, min_len, max_len)
    method = "heatmap" if ctx.heatmap else "transcript" if segments else "uniform"
    return normalize(raw, ctx.duration, segments, count, min_len, max_len), method, warnings
