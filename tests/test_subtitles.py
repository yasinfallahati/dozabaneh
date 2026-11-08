from app.subtitles import build_ass, build_srt, split_words_into_cues


def word(start, end, text):
    return {"start": start, "end": end, "text": text}


def cue(start, end, **texts):
    return {"start": start, "end": end, "texts": texts}


def test_short_phrase_becomes_one_cue():
    cues = split_words_into_cues([word(0.5, 1.0, " Hello"), word(1.0, 1.5, " world")])
    assert cues == [{"start": 0.5, "end": 1.5, "text": "Hello world"}]


def test_long_speech_is_split_by_character_limit():
    words = [word(i * 0.3, i * 0.3 + 0.3, " abcdefghi") for i in range(12)]
    cues = split_words_into_cues(words, max_chars=40)
    assert len(cues) == 3
    assert all(len(c["text"]) <= 40 for c in cues)


def test_cue_never_exceeds_duration_limit():
    words = [word(i * 2.0, i * 2.0 + 2.0, " a") for i in range(6)]
    cues = split_words_into_cues(words, max_seconds=6.0)
    assert all(c["end"] - c["start"] <= 6.0 for c in cues)


def test_silence_starts_a_new_cue():
    cues = split_words_into_cues([word(0, 1, " one"), word(5, 6, " two")], max_gap=1.0)
    assert [c["text"] for c in cues] == ["one", "two"]


def test_sentence_end_starts_a_new_cue():
    words = [
        word(0, 1, " This is a full sentence."),
        word(1, 2, " Next one"),
    ]
    assert [c["text"] for c in split_words_into_cues(words)] == [
        "This is a full sentence.",
        "Next one",
    ]


def test_persian_question_mark_ends_a_sentence():
    words = [word(0, 1, " حال شما چطور است؟"), word(1, 2, " خوبم")]
    assert len(split_words_into_cues(words)) == 2


def test_no_words_gives_no_cues():
    assert split_words_into_cues([]) == []


def test_srt_format():
    cues = [cue(1.5, 3.25, en="Hello"), cue(3661.001, 3662, en="Bye")]
    assert build_srt(cues, "en") == (
        "1\n00:00:01,500 --> 00:00:03,250\nHello\n\n"
        "2\n01:01:01,001 --> 01:01:02,000\nBye\n"
    )


def test_srt_skips_blank_cues_and_renumbers():
    cues = [cue(0, 1, en="A"), cue(1, 2, en="  "), cue(2, 3, en="C")]
    srt = build_srt(cues, "en")
    assert "2\n00:00:02,000 --> 00:00:03,000\nC" in srt
    assert "00:00:01,000" not in srt.replace("00:00:00,000 --> 00:00:01,000", "")


def test_ass_has_one_event_per_language():
    ass = build_ass([cue(0, 1, fa="سلام", en="Hi")], "fa", "en", 1920, 1080)
    assert ass.count("Dialogue:") == 2
    assert ",First,,0,0,0,,سلام" in ass
    assert ",Second,,0,0,0,,Hi" in ass


def test_ass_with_single_language_has_one_event():
    ass = build_ass([cue(0, 1, fa="سلام")], "fa", None, 1920, 1080)
    assert ass.count("Dialogue:") == 1


def test_ass_uses_video_size_and_auto_text_direction():
    ass = build_ass([cue(0, 1, fa="سلام")], "fa", None, 1280, 720)
    assert "PlayResX: 1280" in ass
    assert "PlayResY: 720" in ass
    style = next(l for l in ass.splitlines() if l.startswith("Style: First"))
    assert style.endswith(",-1")


def test_ass_font_scales_with_short_side_of_portrait_video():
    portrait = build_ass([cue(0, 1, fa="a")], "fa", None, 720, 1280)
    landscape = build_ass([cue(0, 1, fa="a")], "fa", None, 1280, 720)
    size = lambda ass: next(l for l in ass.splitlines() if l.startswith("Style: First")).split(",")[2]
    assert size(portrait) == size(landscape)


def test_ass_neutralises_override_tags_and_newlines():
    ass = build_ass([cue(0, 1, en="{\\b1}bold\nnext")], "en", None, 1920, 1080)
    assert "{\\b1}" not in ass
    assert "bold\\Nnext" in ass


def test_ass_skips_blank_text():
    ass = build_ass([cue(0, 1, fa="", en="Hi")], "fa", "en", 1920, 1080)
    assert ass.count("Dialogue:") == 1


def test_first_language_is_stacked_above_the_second():
    # libass places the earliest event at the bottom of a collision stack.
    ass = build_ass([cue(0, 1, fa="سلام", en="Hi")], "fa", "en", 1920, 1080)
    assert ass.index(",Second,") < ass.index(",First,", ass.index("[Events]"))
