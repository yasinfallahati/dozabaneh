from app.media import escape_filter_value, needs_preview_copy, parse_probe


def probe(video="h264", audio="aac", pix_fmt="yuv420p", rotation=None, width=1920, height=1080):
    video_stream = {
        "codec_type": "video",
        "codec_name": video,
        "pix_fmt": pix_fmt,
        "width": width,
        "height": height,
    }
    if rotation is not None:
        video_stream["side_data_list"] = [{"rotation": rotation}]
    streams = [video_stream]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio})
    return {"streams": streams, "format": {"duration": "12.5"}}


def test_probe_reads_size_and_duration():
    info = parse_probe(probe())
    assert (info.width, info.height, info.duration) == (1920, 1080, 12.5)
    assert info.has_audio


def test_phone_video_rotated_ninety_degrees_swaps_size():
    info = parse_probe(probe(rotation=-90))
    assert (info.width, info.height) == (1080, 1920)


def test_probe_without_video_stream_returns_none():
    assert parse_probe({"streams": [{"codec_type": "audio"}], "format": {}}) is None


def test_probe_without_audio_is_flagged():
    assert not parse_probe(probe(audio=None)).has_audio


def test_browser_playable_mp4_needs_no_copy():
    assert not needs_preview_copy(".mp4", parse_probe(probe()))


def test_mkv_needs_a_copy():
    assert needs_preview_copy(".mkv", parse_probe(probe()))


def test_hevc_needs_a_copy():
    assert needs_preview_copy(".mp4", parse_probe(probe(video="hevc")))


def test_ten_bit_h264_needs_a_copy():
    assert needs_preview_copy(".mp4", parse_probe(probe(pix_fmt="yuv420p10le")))


def test_unusual_audio_needs_a_copy():
    assert needs_preview_copy(".mov", parse_probe(probe(audio="pcm_s16le")))


def test_filter_value_escaping():
    assert escape_filter_value("/a:b/it's") == r"/a\\:b/it\\\'s"
