import anthropic
import httpx2

from app.config import HARD_MAX_CLIP_SECONDS
from app.highlights import (
    CLIPS_SCHEMA,
    Highlight,
    VideoContext,
    build_user_prompt,
    heuristic_highlights,
    normalize,
    select_highlights,
)
from app.transcript import Segment

from .fakes import FakeClaude


def make_segments(duration=600, step=5):
    return [Segment(float(t), float(t + step), f"frase número {t // step}") for t in range(0, duration, step)]


CTX = VideoContext(title="Podcast", duration=600, language="pt")


def select(ctx, segments, client, **kwargs):
    options = dict(count=3, min_len=15, max_len=90, model="claude-opus-5-5", effort="high")
    options.update(kwargs)
    return select_highlights(ctx, segments, client_factory=(lambda: client) if client else None, **options)


def test_normalize_never_exceeds_hard_limit():
    segments = make_segments()
    raw = [Highlight(100, 400, "longo", "", "", 90)]
    [clip] = normalize(raw, 600, segments, 3, 15, 200)
    assert clip.duration <= HARD_MAX_CLIP_SECONDS
    assert clip.start == 100


def test_normalize_snaps_to_sentence_boundaries():
    segments = make_segments()
    raw = [Highlight(101.2, 158.4, "t", "", "", 50)]
    [clip] = normalize(raw, 600, segments, 3, 15, 90)
    assert clip.start == 100.0
    assert clip.end == 160.0


def test_normalize_extends_short_clips_and_drops_overlaps():
    segments = make_segments()
    raw = [
        Highlight(200, 205, "curto", "", "", 40),
        Highlight(190, 250, "melhor", "", "", 95),
        Highlight(400, 460, "outro", "", "", 70),
        Highlight(590, 640, "fim", "", "", 60),
    ]
    clips = normalize(raw, 600, segments, 5, 15, 90)
    titles = [c.title for c in clips]
    assert titles == ["melhor", "outro", "fim"]  # "curto" sobrepõe "melhor"
    assert all(15 <= c.duration <= 90 for c in clips)
    assert clips[-1].end <= 600


def test_heuristic_uses_heatmap_peak():
    ctx = VideoContext(
        title="x",
        duration=600,
        heatmap=[{"start_time": t, "end_time": t + 6, "value": 1.0 if 300 <= t < 360 else 0.1} for t in range(0, 600, 6)],
    )
    [clip] = heuristic_highlights(ctx, [], 1, 15, 60)
    assert 290 <= clip.start <= 310
    assert clip.score == 100


def test_heuristic_uniform_without_signals():
    clips = heuristic_highlights(VideoContext(title="x", duration=400), [], 3, 15, 60)
    assert len(clips) == 3
    assert all(c.duration == 60 for c in clips)


def test_select_with_claude_and_prompt_content():
    segments = make_segments()
    client = FakeClaude(
        clips=[
            {"start": 120, "end": 175, "title": "Gancho forte", "hook": "Você não vai acreditar", "reason": "Surpresa.", "score": 92},
            {"start": 300, "end": 500, "title": "Longo demais", "hook": "", "reason": "", "score": 70},
        ]
    )
    ctx = VideoContext(
        title="Podcast", duration=600, language="pt", chapters=[{"start_time": 0, "end_time": 300, "title": "Intro"}],
        heatmap=[{"start_time": 0, "end_time": 6, "value": 0.5}],
    )
    clips, method, warnings = select(ctx, segments, client)
    assert method == "claude" and warnings == []
    assert [c.title for c in clips] == ["Gancho forte", "Longo demais"]
    assert clips[1].duration <= 90

    [call] = client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["schema"] == CLIPS_SCHEMA
    prompt = call["messages"][0]["content"]
    assert "[120.0] frase número 24" in prompt
    assert "Intro" in prompt and "Replay heatmap" in prompt
    assert "between 15 and 90 seconds" in prompt


def test_three_caption_suggestions_are_kept():
    client = FakeClaude(
        clips=[
            {
                "start": 120, "end": 175, "title": "Gancho", "hook": "", "reason": "", "score": 90,
                "captions": ["Curiosidade #a", "  ", "Direta #b", "Pergunta? #c", "Extra #d"],
            }
        ]
    )
    [clip], _, _ = select(CTX, make_segments(), client)
    assert clip.caption_options == ["Curiosidade #a", "Direta #b", "Pergunta? #c"]
    assert clip.caption == "Curiosidade #a"
    assert "exactly three ready-to-post social media captions" in client.calls[0]["system"]


def test_automatic_mode_suggests_three_captions():
    clips, method, _ = select(CTX, make_segments(), None)
    assert method == "transcript"
    assert all(len(c.caption_options) == 3 and c.caption == c.caption_options[0] for c in clips)
    assert all(c.title in option for c in clips for option in c.caption_options)


def test_refusal_falls_back_to_automatic_mode():
    clips, method, warnings = select(CTX, make_segments(), FakeClaude(stop_reason="refusal"))
    assert method == "transcript"
    assert clips
    assert "recusou" in warnings[0]


def test_invalid_json_falls_back():
    _, method, warnings = select(CTX, make_segments(), FakeClaude(raw_text="não é json"))
    assert method == "transcript"
    assert "formato inesperado" in warnings[0]


def test_api_error_falls_back():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    _, method, warnings = select(CTX, make_segments(), FakeClaude(error=error))
    assert method == "transcript"
    assert "conectar" in warnings[0]


def test_without_transcript_claude_is_skipped():
    client = FakeClaude()
    _, method, warnings = select(CTX, [], client)
    assert method == "uniform"
    assert client.calls == []
    assert "legendas" in warnings[0]


def test_short_video_limits_clip_length():
    ctx = VideoContext(title="curto", duration=40)
    clips, _, _ = select(ctx, [], None, count=2)
    assert clips and all(c.end <= 40 for c in clips)


def test_build_user_prompt_lists_requested_count():
    prompt = build_user_prompt(CTX, make_segments(20), 4, 15, 60)
    assert "Pick up to 4 clips" in prompt
