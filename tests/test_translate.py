import pytest

from app.translate import TranslationError, canonical_language, nllb_code, translate_lines


def upper(lines):
    return [line.upper() for line in lines]


def test_lines_keep_their_order():
    assert translate_lines(["a", "b", "c"], send=upper) == ["A", "B", "C"]


def test_lines_are_sent_in_batches():
    batches = []

    def send(lines):
        batches.append(lines)
        return lines

    translate_lines(["a", "b", "c"], send=send, batch_size=2)
    assert batches == [["a", "b"], ["c"]]


def test_blank_lines_stay_blank_and_are_not_sent():
    batches = []

    def send(lines):
        batches.append(lines)
        return upper(lines)

    assert translate_lines(["a", "  ", "b"], send=send) == ["A", "", "B"]
    assert batches == [["a", "b"]]


def test_engine_failure_raises_translation_error():
    def send(lines):
        raise RuntimeError("model missing")

    with pytest.raises(TranslationError):
        translate_lines(["a"], send=send)


def test_progress_reaches_one():
    seen = []
    translate_lines(["a", "b"], send=upper, on_progress=seen.append)
    assert seen[-1] == 1.0


def test_empty_input_needs_no_engine_call():
    def send(lines):
        raise AssertionError("should not be called")

    assert translate_lines([], send=send) == []


def test_language_codes_map_to_model_codes():
    assert nllb_code("fa") == "pes_Arab"
    assert nllb_code("en") == "eng_Latn"
    assert nllb_code("zh") == "zho_Hans"


def test_unsupported_language_has_no_model_code():
    assert nllb_code("xx") is None


def test_whisper_spelling_of_javanese_is_normalised():
    assert canonical_language("jw") == "jv"
    assert nllb_code("jw") == "jav_Latn"
