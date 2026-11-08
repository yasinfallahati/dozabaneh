SENTENCE_ENDINGS = (".", "?", "!", "؟", "。", "！", "？", "…")
MIN_SENTENCE_CHARS = 12

FIRST_COLOUR = "&H00FFFFFF"
SECOND_COLOUR = "&H003DD9FF"
FONT_NAME = "Vazirmatn"


def split_words_into_cues(words, max_chars=84, max_seconds=6.0, max_gap=1.0):
    cues = []
    current = []

    def flush():
        if current:
            cues.append(
                {
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "text": "".join(w["text"] for w in current).strip(),
                }
            )
            current.clear()

    for word in words:
        if current:
            text = "".join(w["text"] for w in current).strip()
            too_long = len((text + word["text"]).strip()) > max_chars
            too_slow = word["end"] - current[0]["start"] > max_seconds
            pause = word["start"] - current[-1]["end"] > max_gap
            sentence_done = text.endswith(SENTENCE_ENDINGS) and len(text) >= MIN_SENTENCE_CHARS
            if too_long or too_slow or pause or sentence_done:
                flush()
        current.append(word)
    flush()
    return cues


def _srt_time(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _ass_time(seconds):
    centis = round(seconds * 100)
    hours, centis = divmod(centis, 360_000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def build_srt(cues, lang):
    blocks = []
    for cue in cues:
        text = cue["texts"].get(lang, "").strip()
        if text:
            timing = f"{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}"
            blocks.append(f"{len(blocks) + 1}\n{timing}\n{text}\n")
    return "\n".join(blocks)


def _ass_text(text):
    # A literal backslash would otherwise start an ASS override such as \N.
    text = text.replace("\\", "⧵").replace("{", "\\{").replace("}", "\\}")
    return "\\N".join(text.splitlines())


def _style(name, size, colour, outline, margin):
    # Encoding -1 tells libass to detect right-to-left text per line.
    return (
        f"Style: {name},{FONT_NAME},{size},{colour},{colour},&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},1,2,{margin},{margin},{margin},-1"
    )


def build_ass(cues, lang1, lang2, width, height):
    size = round(min(width, height) * 0.05)
    outline = max(round(size * 0.07), 1)
    margin = round(min(width, height) * 0.05)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style("First", size, FIRST_COLOUR, outline, margin),
        _style("Second", round(size * 0.85), SECOND_COLOUR, outline, margin),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    for cue in cues:
        timing = f"{_ass_time(cue['start'])},{_ass_time(cue['end'])}"
        # libass stacks the earliest event lowest, so the first language goes last.
        for style, lang in (("Second", lang2), ("First", lang1)):
            text = cue["texts"].get(lang, "").strip() if lang else ""
            if text:
                events.append(f"Dialogue: 0,{timing},{style},,0,0,0,,{_ass_text(text)}")
    return "\n".join(header + events) + "\n"
