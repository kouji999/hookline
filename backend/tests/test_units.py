"""Unit tests for pure backend functions (no network, no ffmpeg)."""

import json
from pathlib import Path

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


def test_cv2_face_api_present():
    """Regression: opencv 5.x removed CascadeClassifier (job died in prod 2026-09-13).
    Requirements pin <5; fail loudly if environment drifts."""
    import cv2
    assert hasattr(cv2, "CascadeClassifier"), "opencv>=5 breaks face tracking"
    assert hasattr(cv2, "data")


def test_required_imports_present():
    """Regression: dotenv/yt_dlp were silently missing from requirements and broke fresh install."""
    from importlib.util import find_spec
    for mod in ("dotenv", "yt_dlp", "requests", "fastapi", "uvicorn", "google.genai", "youtube_transcript_api"):
        assert find_spec(mod), f"missing module: {mod}"


def test_render_batch_coerces_sandbox_string():
    """Regression: PowerShell/JSON string 'false' once enabled sandbox for a real render."""
    job_id = ve.new_job("dQw4w9WgXcQ", [{"start": 1, "end": 5, "title": "t", "quote": ""}], {"sandbox": "false", "nvenc": "true"})
    j = ve.JOBS[job_id]
    assert j["opts"]["sandbox"] is False
    assert j["opts"]["nvenc"] is True


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
    assert len(ve.PRESETS) == 10
    assert "mono-condensed" in ve.PRESETS
    for p in ve.PRESETS.values():
        assert p["primary"].startswith("&H") and p["primary"].endswith("&")
        assert p["size"] >= 30


def test_crop_filter_center_and_clamp():
    assert ve.crop_filter([(0.0, 0.5)], 1920, 607) == "crop=607:ih:656:0"
    assert ve.crop_filter([], 1920, 607) == "crop=607:ih:656:0"
    assert ve.crop_filter([(0.0, 0.0)], 1920, 607).startswith("crop=607:ih:0:")
    assert ve.crop_filter([(0.0, 1.0)], 1920, 607) == "crop=607:ih:1313:0"


def test_relative_subtitles_windows():
    """Regression: absolute->clip-relative conversion once produced 35s windows
    from 3.5s segments (offset subtraction bug)."""
    subs = [
        {"start": 1135.0, "duration": 3.5, "text": "first line"},
        {"start": 1139.0, "duration": 4.0, "text": "second line"},
        {"start": 9999.0, "duration": 2.0, "text": "outside range"},
    ]
    rel = ve.relative_subtitles(subs, start=1135.0, dur=35.0)
    assert len(rel) == 2
    assert rel[0]["start"] == 0.0 and rel[0]["duration"] == pytest.approx(3.5)
    assert rel[1]["start"] == pytest.approx(4.0) and rel[1]["duration"] == pytest.approx(4.0)


def test_relative_subtitles_clips_tail():
    subs = [{"start": 1168.0, "duration": 6.0, "text": "tail runs past clip end"}]
    rel = ve.relative_subtitles(subs, start=1135.0, dur=35.0)
    assert len(rel) == 1
    assert rel[0]["start"] == 33.0
    assert rel[0]["duration"] == pytest.approx(2.0)


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


# --- v3: substance scoring, upload analysis, final cut ---------------------

def test_clip_subtitles_window():
    segs = [
        {"start": 100.0, "duration": 3.0, "text": "before clip"},
        {"start": 110.0, "duration": 4.0, "text": "inside clip"},
        {"start": 200.0, "duration": 2.0, "text": "after clip"},
    ]
    subs = main.clip_subtitles(segs, 108.0, 140.0)
    assert len(subs) == 1
    assert subs[0]["start"] == 110.0


def test_quote_fidelity_and_snap_edge():
    segs = [{"start": 50.0, "duration": 4.0, "text": "alpha beta gamma delta"}]
    assert main.quote_fidelity("alpha gamma", segs) == 1.0
    assert main.quote_fidelity("zzzz qqqq", segs) == 0.0
    assert main.snap_edge(segs, 51.5, tol=3.0) == 50.0
    assert main.snap_edge(segs, 30.0, tol=3.0) == 30.0


def test_enrich_clip_value_and_filler():
    segs = [{"start": 10.0, "duration": 4.0, "text": f"word{i} " * 12} for i in range(6)]
    clip = {"start": 10.0, "end": 30.0, "title": "t", "reason": "r", "quote": "word1 word2"}
    out = main.enrich_clip(dict(clip), segs, None, None, 40.0)
    assert 0 <= out["value"] <= 1
    assert out["subtitles"]
    filler = {"start": 10.0, "end": 30.0, "title": "t", "reason": "r", "quote": "word1 word2",
              "captions": None}
    segs_f = [{"start": 10.0, "duration": 20.0, "text": "hey guys don't forget to like and subscribe"}]
    f = main.enrich_clip(dict(filler), segs_f, None, None, 40.0)
    plain = main.enrich_clip(dict(filler), [{"start": 10.0, "duration": 20.0, "text": "the margin model works like this"}], None, None, 40.0)
    assert f["value"] < plain["value"]


def test_source_id_rejects_traversal():
    from fastapi import HTTPException
    assert main.source_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert main.source_id("https://youtu.be/dQw4w9WgXcQ?si=x") == "dQw4w9WgXcQ"
    assert main.source_id("up1a2b3c4d5e") == "up1a2b3c4d5e"
    for bad in ["../../windows", "a/b", "x" * 100, "id;rm -rf"]:
        try:
            main.source_id(bad)
            raise AssertionError(f"should reject {bad!r}")
        except HTTPException:
            pass


def test_build_video_prompt_rubric():
    p = main.build_video_prompt(30, "crypto frameworks", 5, 300.0, "id")
    assert "Substance" in p and "greeting" in p.lower()
    assert "captions" in p and "crypto frameworks" in p
    assert "Indonesian" in p


def test_parse_video_clips_offsets_captions():
    payload = json.dumps([
        {"start": 5, "end": 35, "title": "T", "kind": "method", "reason": "r", "quote": "hello there",
         "captions": [{"s": 0, "d": 3, "t": "hello there"}, {"s": 3, "d": 4, "t": "second line"}]},
        {"start": 500, "end": 480, "title": "bad range"},
    ])
    clips = main.parse_video_clips(payload, offset=120.0, cap=600.0)
    assert len(clips) == 1
    c = clips[0]
    assert c["start"] == 125.0 and c["end"] == 155.0
    assert c["captions"][0]["start"] == 125.0
    assert c["captions"][1]["start"] == 128.0


def test_clip_token_matches_export_naming():
    tok = ve.clip_token(1376.9, 1413.2)
    assert tok == "1376-1413"
    name = f"vid_{tok}_abc123.mp4"
    assert ve.clip_token(1376.9, 1413.2) in name


def test_assemble_final_rejects_empty_parts():
    assert ve.assemble_final([], Path("x.mp4"), "9:16", "fade", 0.4, []) is None


def test_merge_overlapping_subs_no_stacked_captions():
    """YouTube ASR segments overlap; ASS events must never render two lines at once."""
    subs = [
        {"start": 0.0, "duration": 4.0, "text": "the first line here"},
        {"start": 2.0, "duration": 4.0, "text": "the first line here continues now"},
        {"start": 6.0, "duration": 2.0, "text": "separate later line"},
    ]
    merged = ve.merge_overlapping_subs(subs)
    for i in range(1, len(merged)):
        assert merged[i]["start"] >= merged[i - 1]["start"] + merged[i - 1]["duration"] - 0.01
    assert merged[-1]["text"] == "separate later line"
    assert "continues" in merged[0]["text"]


def test_relative_subtitles_overlap_free():
    subs = [
        {"start": 100.0, "duration": 3.0, "text": "alpha beta"},
        {"start": 101.5, "duration": 3.0, "text": "beta gamma delta"},
    ]
    rel = ve.relative_subtitles(subs, 100.0, 8.0)
    assert len(rel) >= 1
    assert sum(1 for r in rel if r["start"] < 2.0) == 1


def test_remap_captions_onto_tightened_timeline():
    ranges = [(10.0, 14.0), (20.0, 28.0)]
    subs = [
        {"start": 11.0, "duration": 2.0, "text": "first kept"},
        {"start": 16.0, "duration": 2.0, "text": "in dead air dropped"},
        {"start": 21.0, "duration": 3.0, "text": "second kept"},
    ]
    out = ve.remap_captions(subs, ranges)
    assert [o["text"] for o in out] == ["first kept", "second kept"]
    assert out[0]["start"] == 1.0
    assert out[1]["start"] == round(4.0 + 1.0, 3)


def test_punch_filter_disabled_for_short_clips():
    assert ve.punch_filter(1080, 1920, 2.0) == ""
    f = ve.punch_filter(1080, 1920, 30.0)
    assert "eval=frame" in f and "crop=1080:1920" in f and "setsar=1" in f


def test_dead_air_no_silence_returns_full_window(tmp_path):
    src = tmp_path / "a.mp4"
    import subprocess
    subprocess.run([ve.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000:duration=6", "-c:a", "aac", str(src)], check=True)
    log: list[str] = []
    assert ve.detect_dead_air(src, 0.0, 6.0, log) == [(0.0, 6.0)]


def test_words_to_rel_events_real_timing_and_trim():
    words = [{"t": 10.0, "w": "satu"}, {"t": 10.4, "w": "dua"}, {"t": 10.9, "w": "tiga"},
             {"t": 15.0, "w": "di-dead-air"}, {"t": 20.5, "w": "empat"}]
    ranges = [(9.5, 12.5), (19.5, 23.5)]
    ev = ve.words_to_rel_events(words, ranges)
    assert ev, "word events expected"
    assert all("words" in e and e["words"] for e in ev)
    assert ev[0]["start"] == 0.5
    assert ev[0]["words"][0]["t"] == 0.0
    assert ev[-1]["start"] >= 3.0
    texts = [w["w"] for e in ev for w in e["words"]]
    assert "di-dead-air" not in texts


def test_clean_words_survives_legacy_number():
    assert main._clean_words(169) == []
    assert main._clean_words(None) == []
    assert main._clean_words([{"t": 1.0, "w": "halo"}, {"bad": 1}, [1, 2]]) == [{"t": 1.0, "w": "halo"}]
    assert main._clean_subs("nope") == []
    assert main._clean_subs([{"start": "10", "duration": "2", "text": "x"}]) == [{"start": 10.0, "duration": 2.0, "text": "x"}]


def test_validate_cookies_and_header_autofix(tmp_path, monkeypatch):
    good = "# Netscape HTTP Cookie File\n" + "\t".join([".youtube.com", "TRUE", "/", "FALSE", "1893456000", "SID", "real-value"])
    v, bad = ve.validate_cookies(good)
    assert bad == [] and len(v) == 2
    # header is mandatory for MozillaCookieJar / yt-dlp - without it every line is rejected
    _v, bad2 = ve.validate_cookies("not a cookie line")
    assert any("header" in b for b in bad2)
    # 6-field row rejected (the classic browser-extension mistake)
    _v, bad3 = ve.validate_cookies("# Netscape HTTP Cookie File\na\tTRUE\t/\tFALSE\tb\tc")
    assert any(b.startswith("a\t") for b in bad3)
    # save adds the header MozillaCookieJar requires
    monkeypatch.setattr(ve, "COOKIES", tmp_path / "cookies.txt")
    res = ve.save_cookies(good)
    assert res["saved"] is True
    txt = (tmp_path / "cookies.txt").read_text(encoding="utf-8")
    assert txt.startswith("# Netscape HTTP Cookie File")
    import http.cookiejar
    jar = http.cookiejar.MozillaCookieJar(str(tmp_path / "cookies.txt"))
    jar.load()  # must not raise LoadError
    st = ve.cookies_status()
    assert st["present"] and st["valid"] is True
    # invalid content rejected with a clear message
    res2 = ve.save_cookies("garbage without tabs")
    assert res2["saved"] is False and "Netscape" in res2["error"]
    # download_source ignores a broken cookie file instead of poisoning every job
    (tmp_path / "cookies.txt").write_text("junk-no-tabs\n", encoding="utf-8")
    assert ve.cookies_are_valid() is False


def test_grade_filter_and_mono_presets():
    assert ve.grade_filter("noir").startswith("eq=")
    assert "vignette" in ve.grade_filter("noir")
    assert ve.grade_filter("natural") == ""
    assert ve.grade_filter("fade") != ve.grade_filter("noir")
    for name in ("mono-condensed", "mono-heavy", "mono-stone"):
        p = ve.PRESETS[name]
        # black-and-white: BGR channels equal (no chroma); ASS layout &HBBGGRR+AA
        for color in (p["primary"], p["secondary"]):
            bgr = color[4:10]
            assert bgr[0:2] == bgr[2:4] == bgr[4:6], color
    assert ve.normalize_opts({"grade": "NOIR"})["grade"] == "noir"
    assert ve.normalize_opts({"grade": "garbage"})["grade"] == "noir"
    assert ve.normalize_opts({"grade": "fade"})["grade"] == "fade"
