from app.transcript import parse_json3, parse_vtt, text_between


def test_parse_json3_auto_captions_with_word_timing():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100000},  # definição de janela, sem texto
            {
                "tStartMs": 1000,
                "dDurationMs": 4000,
                "segs": [{"utf8": "olá"}, {"utf8": " pessoal", "tOffsetMs": 500}, {"utf8": " tudo bem", "tOffsetMs": 1200}],
            },
            {"tStartMs": 3000, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 4000, "dDurationMs": 3000, "segs": [{"utf8": "vamos", "tOffsetMs": 0}, {"utf8": " lá", "tOffsetMs": 600}]},
        ]
    }
    segments = parse_json3(data)
    assert [s.text for s in segments] == ["olá pessoal tudo bem", "vamos lá"]
    first = segments[0]
    # A linha ia até 5s, mas a próxima começa em 4s.
    assert (first.start, first.end) == (1.0, 4.0)
    assert [w.text for w in first.words] == ["olá", "pessoal", "tudo", "bem"]
    assert first.words[1].start == 1.5
    assert first.words[2].start == 2.2 and first.words[3].end == 4.0


def test_parse_json3_manual_captions_without_word_timing():
    data = {"events": [{"tStartMs": 2500, "dDurationMs": 2000, "segs": [{"utf8": "Linha manual\ncom quebra"}]}]}
    [segment] = parse_json3(data)
    assert segment.text == "Linha manual com quebra"
    assert (segment.start, segment.end) == (2.5, 4.5)
    assert segment.words == []


def test_parse_vtt_removes_rolling_duplicates():
    vtt = """WEBVTT
Kind: captions
Language: pt

00:00:01.000 --> 00:00:03.000 align:start position:0%
primeira<00:00:01.500><c> frase</c>

00:00:03.000 --> 00:00:03.010
primeira frase

00:00:03.010 --> 00:00:05.000
primeira frase
segunda frase &amp; mais

01:02:03.500 --> 01:02:04.000
fim
"""
    segments = parse_vtt(vtt)
    assert [s.text for s in segments] == ["primeira frase", "segunda frase & mais", "fim"]
    assert segments[-1].start == 3723.5


def test_text_between():
    segments = parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\na\n\n00:00:02.000 --> 00:00:04.000\nb\n")
    assert text_between(segments, 1, 3) == "a b"
    assert text_between(segments, 2, 3) == "b"
