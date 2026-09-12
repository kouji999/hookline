"""Unit tests for pure backend functions (no network, no ffmpeg)."""

import json

import pytest

import main
import video_engine as ve


# --- extract_video_id -------------------------------------------------------

@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("", None),
        ("https://example.com/watch?v=abc", None),
        ("nonsense", None),
    ],
)
def test_extract_video_id(url, want):
    assert main.extract_video_id(url) == want


# --- sse format ---------------------------------------------------------------

def test_sse_frame_format():
    frame = main.sse("meta", {"a": 1})
    assert frame == 'event: meta\ndata: {"a": 1}\n\n'


# --- transcript helpers -------------------------------------------------------

def test_estimate_duration_bounds():
    assert main.estimate_duration_from_transcript([]) == 600.0
    assert main.estimate_duration_from_transcript([{"start": 10, "duration": 4}]) == 60.0
    assert main.estimate_duration_from_transcript([{"start": 200, "duration": 10}]) == 210.0


def test_parse_manual_subtitles_srt():
    srt = (
        "1\n00:00:01,000 --> 00:00:03,500\nHello there\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nSecond line\n"
    )
    segs = main.parse_manual_subtitles(srt)
    assert len(segs) == 2
    assert segs[0]["start"] == pytest.approx(1.0)
    assert segs[0]["duration"] == pytest.approx(2.5)
    assert segs[0]["text"] == "Hello there"
    assert segs[1]["text"] == "Second line"


def test_parse_manual_subtitles_plain_text():
    segs = main.parse_manual_subtitles("First sentence. Second one here.")
    assert len(segs) == 2
    assert segs[1]["start"] > segs[0]["start"]
    assert all(s["duration"] > 0 for s in segs)


def test_sanitize_caption():
    assert main.sanitize_caption("[LAUGHTER] Hi (laughs) ok!") == "Hi ok!"
    assert main.sanitize_caption("keep 50% & $3, right?") == "keep 50% & $3, right?"


def test_build_signal_estimates_shape():
    segs = [{"start": i * 4, "duration": 4, "text": "hello there friend"} for i in range(30)]
    est = main.build_signal_estimates(segs, count=50)
    assert len(est) == 50
    assert all(0.0 <= v <= 1.0 for v in est)


# --- gemini prompt + parsing ----------------------------------------------------

def test_build_analysis_prompt_content():
    p = main.build_analysis_prompt("transcript body", "15s", "find jokes", "peaks", 8, 300.0)
    assert "Return ONLY a JSON array" in p
    assert "find jokes" in p
    assert "15 seconds" in p
    assert "peaks" in p


def test_parse_gemini_clips_plain_and_fenced():
    raw = '[{"start": 1, "end": 9, "title": "T", "reason": "R", "quote": "Q"}]'
    clips = main.parse_gemini_clips(raw)
    assert clips[0]["start"] == 1.0
    fenced = "```json\n" + raw + "\n```"
    assert main.parse_gemini_clips(fenced) == clips


def test_parse_gemini_clips_skips_invalid_keeps_valid():
    raw = '[{"start": "x"}, {"start": 1, "end": 9, "title": "T"}]'
    clips = main.parse_gemini_clips(raw)
    assert len(clips) == 1


def test_parse_gemini_clips_no_array_raises():
    with pytest.raises(ValueError):
        main.parse_gemini_clips("no json here")


def test_gemini_chain_order():
    assert main.GEMINI_CHAIN[0] == "gemini-3.6-flash"
    assert len(main.GEMINI_CHAIN) >= 3


# --- scoring -------------------------------------------------------------------

def test_score_with_signal_overlap():
    sig = [
        {"start": 10, "end": 20, "intensity": 0.7},
        {"start": 25, "end": 30, "intensity": 0.9},
    ]
    assert main.score_clip_against_signal({"start": 15, "end": 22}, sig, None, 100) == 0.7
    assert main.score_clip_against_signal({"start": 24, "end": 26}, sig, None, 100) == 0.9


def test_score_with_estimates_and_fallback():
    est = [0.2] * 60
    assert main.score_clip_against_signal({"start": 10, "end": 20}, None, est, 120) == 0.2
    assert main.score_clip_against_signal({"start": 10, "end": 20}, None, None, 120) == 0.5


# --- mock ------------------------------------------------------------------------

def test_mock_clips_ranges():
    for dur in ("15s", "30s", "60s"):
        clips = main.mock_clips(dur)
        assert len(clips) >= 3
        for c in clips:
            assert c["end"] > c["start"]
            assert c["title"] and c["quote"]


# --- video_engine: pure parts ----------------------------------------------------

def test_aspect_and_preset_tables():
    assert ve.ASPECTS["9:16"] == (1080, 1920)
    assert len(ve.PRESETS) == 7
    for p in ve.PRESETS.values():
        assert p["primary"].startswith("&H") and p["primary"].endswith("&")
        assert p["size"] >= 30


def test_crop_filter_center_and_clamp():
    assert ve.crop_filter([(0.0, 0.5)], 1920, 607) == "crop=607:ih:656:0"
    assert ve.crop_filter([], 1920, 607) == "crop=607:ih:656:0"
    assert ve.crop_filter([(0.0, 0.0)], 1920, 607).startswith("crop=607:ih:0:")
    assert ve.crop_filter([(0.0, 1.0)], 1920, 607) == "crop=607:ih:1313:0"


def test_build_ass_structure():
    subs = [{"start": 0.0, "duration": 3.0, "text": "hello {world} braces"}, {"start": 3.0, "duration": 2.0, "text": "second"}]
    ass = ve.build_ass(subs, "viral-pop", 1920, 260)
    assert "[V4+ Styles]" in ass
    assert "Dialogue: 0,0:00:00.00" in ass
    assert "{" not in ass.split("Text\n")[-1].replace("{\\kf", "")  # braces escaped in words
    assert "(world)" in ass


def test_ass_filter_arg_relative():
    p = ve.TEMP / "whatever.ass"
    arg = ve._ass_filter_arg(p)
    assert arg.startswith("temp/")
    assert ":" not in arg


def test_write_ass_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "TEMP", tmp_path)
    path = ve.write_ass("vid", "k1", [{"start": 0, "duration": 2, "text": "hi"}], "fire-red", 1920, 300)
    raw = path.read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf"  # no BOM: breaks libass
    assert path.read_text(encoding="utf-8").count("Dialogue") == 1


def test_job_progress_none_for_unknown():
    assert ve.job_progress("nope") is None
    assert ve.raw_download_status("nope") is None


def test_cookies_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "COOKIES", tmp_path / "cookies.txt")
    assert ve.cookies_status() == {"present": False}
    res = ve.save_cookies(".youtube.com\tTRUE\t/\tFALSE\t1893456000\tSID\tabc\n")
    assert res["saved"] and res["lines"] == 1 and ".youtube.com" in res["domains"]
    assert ve.cookies_status()["present"] is True
    ve.delete_cookies()
    assert ve.cookies_status() == {"present": False}
