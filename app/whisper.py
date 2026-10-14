"""Transcrição local opcional (faster-whisper) para vídeos sem legenda.

Ative instalando `faster-whisper` e definindo WHISPER_MODEL (ex.: "small").
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from .transcript import Segment, Word

_models: dict[str, object] = {}
_lock = threading.Lock()


def available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def transcribe(path: Path, model_name: str) -> list[Segment]:
    from faster_whisper import WhisperModel

    with _lock:
        model = _models.get(model_name)
        if model is None:
            model = WhisperModel(model_name, device="auto", compute_type="int8")
            _models[model_name] = model
    parts, _info = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    segments = []
    for part in parts:
        words = [Word(w.start, w.end, w.word.strip()) for w in (part.words or []) if w.word.strip()]
        text = " ".join(part.text.split())
        if text:
            segments.append(Segment(part.start, part.end, text, words))
    return segments
