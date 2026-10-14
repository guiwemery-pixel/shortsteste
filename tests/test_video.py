import subprocess

import pytest

from app.backgrounds import BUILTIN
from app.captions import build_ass
from app.transcript import Segment
from app.video import (
    Background,
    output_size,
    parse_silences,
    probe,
    render_clip,
    speech_intervals,
    thumbnail,
    detect_silences,
)

from .conftest import needs_ffmpeg


def test_parse_silences():
    stderr = """
[silencedetect @ 0x1] silence_start: 2.5
[silencedetect @ 0x1] silence_end: 4.0 | silence_duration: 1.5
[silencedetect @ 0x1] silence_start: 9.2
"""
    assert parse_silences(stderr, offset=100, length=10) == [(102.5, 104.0), (109.2, 110.0)]


def test_speech_intervals_keeps_breathing_room():
    keep = speech_intervals(100, 120, [(105, 108), (110, 110.3), (118, 120)])
    # Silêncio curto (0,3s) fica; os outros saem com 0,15s de folga de cada lado.
    assert keep == [(100, 105.15), (107.85, 118.15)]
    assert speech_intervals(0, 10, [(0, 10)]) == [(0, 10)]  # tudo silêncio: mantém o corte


@needs_ffmpeg
def test_output_sizes(sample_video):
    info = probe(sample_video)
    assert (info.width, info.height, info.has_audio) == (640, 360, True)
    assert round(info.duration) == 120
    assert output_size(info, "vertical_blur", 1080) == (1080, 1920)
    assert output_size(info, "split", 540) == (540, 960)
    assert output_size(info, "original", 1080) == (640, 360)


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["vertical_blur", "vertical_crop", "original"])
def test_render_clip_modes_with_captions(sample_video, tmp_path, mode):
    info = probe(sample_video)
    width, height = output_size(info, mode, 360)
    segments = [Segment(30 + i * 2, 32 + i * 2, f"palavra {i} aqui") for i in range(10)]
    ass = build_ass(segments, [(30, 50)], width, height)
    out = tmp_path / "clip_01.mp4"
    final = render_clip(sample_video, info, out, [(30, 50)], mode, 360, ass, mirror=True)
    result = probe(out)
    assert final == 20
    assert (result.width, result.height) == (width, height)
    assert result.has_audio
    assert abs(result.duration - 20) < 0.5
    assert (tmp_path / "clip_01.ass").exists()
    thumbnail(out, tmp_path / "clip_01.jpg")
    assert (tmp_path / "clip_01.jpg").stat().st_size > 0


@needs_ffmpeg
@pytest.mark.parametrize("background_id", list(BUILTIN))
def test_split_screen_with_builtin_background(sample_video, tmp_path, background_id):
    info = probe(sample_video)
    out = tmp_path / "clip_01.mp4"
    background = Background(lavfi=BUILTIN[background_id]["lavfi"])
    render_clip(sample_video, info, out, [(10, 16)], "split", 360, None, mirror=True, background=background)
    result = probe(out)
    assert (result.width, result.height) == (360, 640)
    assert abs(result.duration - 6) < 0.5


@needs_ffmpeg
def test_split_screen_loops_short_uploaded_background(sample_video, tmp_path):
    short = tmp_path / "bg.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "3", str(short)],
        check=True,
    )
    info = probe(sample_video)
    out = tmp_path / "clip_01.mp4"
    render_clip(sample_video, info, out, [(0, 10)], "split", 360, None, background=Background(path=short, duration=3))
    assert abs(probe(out).duration - 10) < 0.5


@needs_ffmpeg
def test_remove_silence_end_to_end(tmp_path):
    # 3s de tom, 3s de silêncio, 3s de tom.
    source = tmp_path / "fala.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100",
            "-filter_complex", "[1:a]volume=enable='between(t,3,6)':volume=0[a]",
            "-map", "0:v", "-map", "[a]", "-t", "9", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(source),
        ],
        check=True,
    )
    silences = detect_silences(source, 0, 9)
    assert len(silences) == 1
    a, b = silences[0]
    assert 2.8 < a < 3.3 and 5.7 < b < 6.3
    intervals = speech_intervals(0, 9, silences)
    assert len(intervals) == 2

    info = probe(source)
    segments = [Segment(0.5, 2.5, "antes da pausa"), Segment(6.5, 8.5, "depois da pausa")]
    ass = build_ass(segments, intervals, 360, 640)
    out = tmp_path / "clip_01.mp4"
    final = render_clip(source, info, out, intervals, "vertical_crop", 360, ass)
    result = probe(out)
    assert 6.0 < final < 6.8
    assert abs(result.duration - final) < 0.3
    assert result.has_audio
