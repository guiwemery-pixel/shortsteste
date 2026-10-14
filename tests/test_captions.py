from app.captions import ass_time, build_ass, build_srt, group_words, srt_time, words_in_window
from app.transcript import Segment, Word


def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.257) == "0:01:01.26"
    assert ass_time(3725.5) == "1:02:05.50"
    assert ass_time(-3) == "0:00:00.00"


def test_group_words_short_chunks_and_punctuation():
    words = [Word(i * 0.3, i * 0.3 + 0.3, t) for i, t in enumerate("isso é incrível, olha só que coisa".split())]
    groups = [g.text for g in group_words(words)]
    assert groups == ["isso é incrível,", "olha só que", "coisa"]


def test_words_distributed_when_no_word_timing():
    segments = [Segment(10, 12, "uma duas três quatro")]
    words = words_in_window(segments, [(10, 20)])
    # Tempos já relativos ao início do corte.
    assert [round(w.start, 2) for w in words] == [0, 0.5, 1, 1.5]


def test_build_ass_shifts_to_clip_time_and_escapes():
    segments = [Segment(100, 102, "fala {com} chaves\\barra"), Segment(102, 104, "fora do corte")]
    ass = build_ass(segments, [(100, 102)], 1080, 1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert dialogues[0].startswith("Dialogue: 0,0:00:00.00,")
    assert "(COM)" in dialogues[0] and "{" not in dialogues[0]
    assert all("FORA" not in d for d in dialogues)


def test_build_ass_none_without_words():
    assert build_ass([Segment(0, 5, "longe")], [(100, 120)], 1080, 1920) is None


def test_words_are_remapped_after_silence_cuts():
    segments = [Segment(10, 11, "antes"), Segment(14, 15, "depois")]
    # Silêncio de 11s a 14s removido: "depois" passa a começar em 1s no corte final.
    words = words_in_window(segments, [(10, 11), (14, 16)])
    assert [(w.text, round(w.start, 2)) for w in words] == [("antes", 0.0), ("depois", 1.0)]


def test_center_alignment_for_split_screen():
    ass = build_ass([Segment(0, 2, "meio da tela")], [(0, 2)], 1080, 1920, center=True)
    style = next(l for l in ass.splitlines() if l.startswith("Style:"))
    assert style.split(",")[18] == "5" and style.split(",")[21] == "0"


def test_build_srt():
    segments = [Segment(60, 64, "uma frase completa para o arquivo de legenda. outra")]
    srt = build_srt(segments, [(60, 64)])
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "uma frase completa para o arquivo de" in srt
    assert srt_time(3725.5) == "01:02:05,500"
    assert build_srt(segments, [(0, 10)]) is None
