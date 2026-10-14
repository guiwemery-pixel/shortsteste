import pytest

from app.youtube import InvalidURL, VideoMeta, _pick_track, canonical_url, video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_video_id_accepts_youtube_links(url):
    assert video_id(url) == "dQw4w9WgXcQ"
    assert canonical_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123456",
        "https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/playlist?list=PL123",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://127.0.0.1:8000/watch?v=dQw4w9WgXcQ",
        "",
    ],
)
def test_video_id_rejects_other_links(url):
    with pytest.raises(InvalidURL):
        video_id(url)


def _meta(**kwargs):
    base = dict(id="x", url="u", title="t", channel="c", duration=60, description="", language=None, thumbnail=None)
    base.update(kwargs)
    return VideoMeta(**base)


def test_pick_track_prefers_manual_in_original_language():
    meta = _meta(
        language="pt",
        subtitles={"en": [{"ext": "json3"}], "pt-BR": [{"ext": "json3"}]},
        automatic_captions={"pt-orig": [{"ext": "json3"}]},
    )
    assert _pick_track(meta)[0] == "pt-BR"


def test_pick_track_uses_original_auto_captions():
    meta = _meta(
        language="pt",
        subtitles={},
        automatic_captions={"en": [{"ext": "json3"}], "pt-orig": [{"ext": "json3"}], "pt": [{"ext": "json3"}]},
    )
    key, _, kind = _pick_track(meta)
    assert (key, kind) == ("pt-orig", "auto")


def test_pick_track_none_without_captions():
    assert _pick_track(_meta(language="pt")) is None
