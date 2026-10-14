"""Legendas: queimadas no vídeo (ASS, estilo Shorts) e arquivo .srt para download."""

from __future__ import annotations

from .transcript import Segment, Word

Interval = tuple[float, float]


def words_in_window(segments: list[Segment], intervals: list[Interval]) -> list[Word]:
    """Palavras dentro dos intervalos, já no tempo do corte final (depois de tirar silêncios)."""
    words: list[Word] = []
    offset = 0.0
    for a, b in intervals:
        for s in segments:
            if s.end <= a or s.start >= b:
                continue
            if s.words:
                seg_words = s.words
            else:
                tokens = s.text.split()
                span = (s.end - s.start) / max(len(tokens), 1)
                seg_words = [Word(s.start + i * span, s.start + (i + 1) * span, t) for i, t in enumerate(tokens)]
            for w in seg_words:
                if a <= (w.start + w.end) / 2 < b:
                    words.append(Word(offset + max(w.start, a) - a, offset + min(w.end, b) - a, w.text))
        offset += b - a
    return words


def group_words(words: list[Word], max_words: int = 3, max_seconds: float = 1.6) -> list[Word]:
    """Agrupa palavras em legendas curtas; quebra também em pontuação."""
    groups: list[Word] = []
    current: list[Word] = []
    for w in words:
        current.append(w)
        too_long = current[-1].end - current[0].start >= max_seconds
        if len(current) >= max_words or too_long or w.text[-1:] in ".!?,;:":
            groups.append(Word(current[0].start, current[-1].end, " ".join(x.text for x in current)))
            current = []
    if current:
        groups.append(Word(current[0].start, current[-1].end, " ".join(x.text for x in current)))
    # Cada legenda fica até a próxima começar (sem buracos curtos que piscam).
    for i in range(len(groups) - 1):
        gap = groups[i + 1].start - groups[i].end
        if 0 < gap < 0.6:
            groups[i].end = groups[i + 1].start
    return groups


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(
    segments: list[Segment], intervals: list[Interval], width: int, height: int, center: bool = False
) -> str | None:
    """Legenda queimada. Com center=True fica no meio da tela (divisão da tela dividida)."""
    words = words_in_window(segments, intervals)
    if not words:
        return None
    total = sum(b - a for a, b in intervals)
    vertical = height > width
    font_size = round(height * (0.045 if vertical else 0.065))
    margin_v = 0 if center else round(height * (0.22 if vertical else 0.08))
    margin_h = round(width * 0.08)
    outline = max(2, round(font_size * 0.08))
    alignment = 5 if center else 2
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for g in group_words(words):
        a, b = max(0.0, g.start), min(total, g.end)
        if b - a < 0.05:
            continue
        lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{_escape(g.text.upper())}")
    if not lines:
        return None
    return header + "\n".join(lines) + "\n"


def build_srt(segments: list[Segment], intervals: list[Interval]) -> str | None:
    """Arquivo .srt do corte, com frases um pouco maiores para editores e redes sociais."""
    words = words_in_window(segments, intervals)
    if not words:
        return None
    total = sum(b - a for a, b in intervals)
    blocks = []
    for g in group_words(words, max_words=7, max_seconds=3.5):
        a, b = max(0.0, g.start), min(total, g.end)
        if b - a < 0.05:
            continue
        blocks.append(f"{len(blocks) + 1}\n{srt_time(a)} --> {srt_time(b)}\n{g.text}\n")
    return "\n".join(blocks) if blocks else None
