"""Leitura de legendas do YouTube (json3 / WebVTT) em trechos com tempo."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


def parse_json3(data: dict) -> list[Segment]:
    """Converte o formato json3 do YouTube (manual ou automático) em trechos.

    Legendas automáticas trazem o tempo de cada palavra (tOffsetMs); legendas
    manuais trazem só o tempo da linha.
    """
    events = [
        ev
        for ev in data.get("events", [])
        if ev.get("segs") and "".join(s.get("utf8", "") for s in ev["segs"]).strip()
    ]
    segments: list[Segment] = []
    for i, ev in enumerate(events):
        start = ev.get("tStartMs", 0) / 1000
        end = start + ev.get("dDurationMs", 0) / 1000
        # Linhas automáticas ficam na tela até a próxima começar; corta a sobreposição.
        if i + 1 < len(events):
            next_start = events[i + 1].get("tStartMs", 0) / 1000
            if start < next_start < end:
                end = next_start
        segs = ev["segs"]
        text = " ".join("".join(s.get("utf8", "") for s in segs).split())
        words: list[Word] = []
        timed = [s for s in segs if s.get("utf8", "").strip()]
        if len(timed) > 1 or (timed and "tOffsetMs" in timed[0]):
            for j, s in enumerate(timed):
                w_start = start + s.get("tOffsetMs", 0) / 1000
                w_end = start + timed[j + 1].get("tOffsetMs", 0) / 1000 if j + 1 < len(timed) else end
                # Um seg pode trazer mais de uma palavra; divide o tempo igualmente.
                tokens = s["utf8"].split()
                span = max(w_end - w_start, 0) / len(tokens)
                for k, token in enumerate(tokens):
                    words.append(Word(w_start + k * span, w_start + (k + 1) * span, token))
        if end <= start:
            end = start + 0.5
        segments.append(Segment(start, end, text, words))
    return segments


_VTT_TIME = re.compile(
    r"(?P<s>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*(?P<e>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})"
)
_TAG = re.compile(r"<[^>]+>")


def _vtt_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(text: str) -> list[Segment]:
    """Converte WebVTT em trechos, removendo as linhas repetidas das legendas automáticas."""
    segments: list[Segment] = []
    last_line = ""
    for block in re.split(r"\r?\n\r?\n", text):
        lines = block.strip().splitlines()
        for idx, line in enumerate(lines):
            match = _VTT_TIME.search(line)
            if not match:
                continue
            start, end = _vtt_seconds(match["s"]), _vtt_seconds(match["e"])
            body = [html.unescape(_TAG.sub("", l)).strip() for l in lines[idx + 1 :]]
            body = [l for l in body if l]
            # Legendas automáticas repetem a linha anterior em cima da nova.
            new_lines = [l for l in body if l != last_line]
            if body:
                last_line = body[-1]
            content = " ".join(" ".join(new_lines).split())
            if content and end > start:
                segments.append(Segment(start, end, content))
            break
    return segments


def format_for_prompt(segments: list[Segment]) -> str:
    return "\n".join(f"[{s.start:.1f}] {s.text}" for s in segments)


def text_between(segments: list[Segment], start: float, end: float) -> str:
    return " ".join(s.text for s in segments if s.end > start and s.start < end)
