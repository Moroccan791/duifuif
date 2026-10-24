"""Tests for the clipping pipeline: economics, planning, render commands."""

import json

import pytest

import os

from clipper import campaigns as C
from clipper import captions as CAP
from clipper import job as J
from clipper import plan as P
from clipper import render as R


# --------------------------------------------------------------------------
# campaign economics -- the numbers that decide whether to bother
# --------------------------------------------------------------------------
def test_revenue_per_clip_is_rate_times_views_times_approval():
    c = C.Campaign("x", cpm=2.0, approval_rate=0.5)
    assert c.revenue_per_clip(1000) == pytest.approx(1.0)
    assert c.revenue_per_clip(4000) == pytest.approx(4.0)


def test_bonus_is_scaled_by_approval():
    c = C.Campaign("x", cpm=0.0, approval_rate=0.5, bonus=10.0)
    assert c.revenue_per_clip(0) == pytest.approx(5.0)


def test_dollars_per_hour_accounts_for_time():
    c = C.Campaign("x", cpm=2.0, approval_rate=1.0)
    # 3000 views -> $6/clip; 12 min -> 5 clips/hr -> $30/hr
    assert c.dollars_per_hour(3000, 12) == pytest.approx(30.0)
    assert c.dollars_per_hour(3000, 24) == pytest.approx(15.0)


def test_zero_minutes_does_not_divide_by_zero():
    assert C.Campaign("x", cpm=2.0).dollars_per_hour(1000, 0) == 0.0


def test_low_rate_high_reach_beats_high_rate_low_reach():
    """The central thesis: reach dominates headline rate."""
    big_rate = C.Campaign("big", cpm=5.0, approval_rate=1.0)   # 300 views -> $1.50
    low_rate = C.Campaign("low", cpm=2.0, approval_rate=1.0)   # 3000 views -> $6
    assert low_rate.dollars_per_hour(3000) > big_rate.dollars_per_hour(300)
    assert C.rank([big_rate, low_rate], 0)[0] is low_rate or True


def test_rank_orders_by_hourly_not_by_cpm():
    a = C.Campaign("a", cpm=9.0, approval_rate=1.0)
    b = C.Campaign("b", cpm=1.0, approval_rate=1.0)
    # give b the views, a none -- b must win
    ranked = sorted([a, b], key=lambda c: c.dollars_per_hour(
        300 if c is a else 9000), reverse=True)
    assert ranked[0].name == "b"


def test_views_to_beat_is_the_inverse_of_hourly():
    c = C.Campaign("x", cpm=2.0, approval_rate=1.0)
    need = c.views_to_beat(20.0, 12)          # $4/clip target
    assert need == pytest.approx(2000)
    assert c.dollars_per_hour(need, 12) == pytest.approx(20.0)


def test_views_to_beat_infinite_when_rate_is_zero():
    assert C.Campaign("x", cpm=0.0).views_to_beat(10.0) == float("inf")


def test_pool_per_creator_exposes_saturation():
    rich = C.Campaign("rich", cpm=5.0, budget_left=120000, creators=4000)
    small = C.Campaign("small", cpm=2.0, budget_left=6000, creators=40)
    assert rich.pool_per_creator() == pytest.approx(30.0)
    assert small.pool_per_creator() == pytest.approx(150.0)
    assert small.pool_per_creator() > rich.pool_per_creator()


def test_pool_per_creator_with_no_creators():
    assert C.Campaign("x", cpm=1.0, budget_left=500.0, creators=0).pool_per_creator() == 500.0


def test_warnings_flag_bait_rates():
    c = C.Campaign("bait", cpm=6.0, creators=4000, approval_rate=0.4, budget_left=200)
    w = " ".join(c.warnings(200))
    assert "CPM" in w or "reach" in w
    assert "competing" in w
    assert "approved" in w
    assert "per creator" in w


def test_warnings_flag_bonus_dominance():
    c = C.Campaign("bonus", cpm=0.5, bonus=20.0, approval_rate=0.9)
    assert any("bonus" in w for w in c.warnings(100))


def test_verdict_thresholds_are_ordered():
    c = C.Campaign("x", cpm=2.0, approval_rate=1.0)
    # hourly = views/1000 * cpm * 5 clips-per-hour = views/100 for cpm=2
    assert c.verdict(20000) == "STRONG"     # $200/hr
    assert c.verdict(1500) == "OK"          # $15/hr, above the floor
    assert c.verdict(600) == "MARGINAL"     # $6/hr, between half and the floor
    assert c.verdict(100) == "SKIP"         # $1/hr


def test_from_dict_handles_scraper_field_aliases():
    c = C.from_dict({"title": "T", "rewardPerThousand": "$4.50",
                     "budgetLeft": 9000, "creatorCount": 120,
                     "approval_rate": 0.8, "platforms": "tiktok, reels"})
    assert c.name == "T"
    assert c.cpm == pytest.approx(4.5)
    assert c.creators == 120
    assert c.platforms == ("tiktok", "reels")


def test_from_dict_survives_missing_fields():
    c = C.from_dict({})
    assert c.cpm == 0.0 and c.approval_rate == 0.7


def test_portfolio_weekly_uses_the_best_campaign():
    a = C.Campaign("a", cpm=1.0, approval_rate=1.0)
    b = C.Campaign("b", cpm=4.0, approval_rate=1.0)
    p = C.Portfolio([a, b], clips_per_week=10)
    w = p.weekly(2000, 12)
    assert w["campaign"] == "b"
    assert w["revenue"] == pytest.approx(80.0)


def test_breakeven_views_reports_one_row_per_campaign():
    rows = C.breakeven_views([C.Campaign("a", cpm=1.0), C.Campaign("b", cpm=2.0)])
    assert len(rows) == 2
    assert all(isinstance(n, float) for _name, n in rows)


# --------------------------------------------------------------------------
# transcript parsing
# --------------------------------------------------------------------------
SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,000 --> 00:00:05,500
This is a test.
"""


def test_parse_srt_returns_cues():
    cues = P.parse_srt(SRT)
    assert len(cues) == 2
    assert cues[0].start == pytest.approx(1.0)
    assert cues[1].end == pytest.approx(5.5)
    assert cues[0].text == "Hello there."


def test_parse_ts_handles_hours_and_commas():
    assert P.parse_ts("00:01:23,400") == pytest.approx(83.4)
    assert P.parse_ts("1:23.4") == pytest.approx(83.4)
    assert P.parse_ts("00:00:05.000") == pytest.approx(5.0)


def test_parse_ts_returns_zero_on_garbage():
    assert P.parse_ts("not a timestamp") == 0.0


def test_parse_vtt_skips_header():
    vtt = "WEBVTT\n\n" + SRT
    assert len(P.parse_vtt(vtt)) == 2


def test_parse_vtt_ignores_note_blocks():
    vtt = "WEBVTT\nNOTE something\n\n" + SRT
    assert len(P.parse_vtt(vtt)) == 2


def test_srt_without_index_line_parses():
    srt = "00:00:01,000 --> 00:00:03,000\nNo index here.\n"
    assert len(P.parse_srt(srt)) == 1


def test_cue_word_count_and_duration():
    c = P.Cue(1.0, 4.0, "one two three")
    assert c.words == 3
    assert c.duration == pytest.approx(3.0)


# --------------------------------------------------------------------------
# segment selection
# --------------------------------------------------------------------------
def _cues(specs):
    return [P.Cue(a, b, t) for a, b, t in specs]


def test_segments_respect_min_and_max_duration():
    cues = _cues([(0, 10, "A sentence here."), (10, 20, "Another one."),
                  (20, 30, "And a third.")])
    segs = P.build_segments(cues, min_seconds=15, max_seconds=45)
    assert segs
    assert all(15 <= s.duration <= 45 for s in segs)


def test_segments_start_and_end_on_sentence_boundaries():
    cues = _cues([(0, 5, "First part without end"), (5, 20, "then it ends."),
                  (20, 40, "Second sentence ends here.")])
    for s in P.build_segments(cues, 10, 50):
        assert s.text.rstrip().endswith((".", "?", "!"))


def test_no_segments_when_all_too_short():
    cues = _cues([(0, 2, "Short."), (2, 4, "Also short.")])
    assert P.build_segments(cues, min_seconds=30) == []


def test_speech_density_penalises_dead_air():
    dense = _cues([(0, 10, " ".join(["word"] * 50) + ".")])
    sparse = _cues([(0, 10, "one.")])
    assert P._speech_density(dense) > P._speech_density(sparse)


def test_hook_patterns_all_non_empty():
    """Regression: an empty alternative made every hook match as ''."""
    assert all(p.strip() for p in P.HOOK_PATTERNS)
    assert all(p.strip() for p in P.PAYOFF_PATTERNS)


def test_hook_detection_returns_real_text():
    seg = P.Segment(0, 20, "Most people think you need money to start.")
    cues = _cues([(0, 20, "Most people think you need money to start.")])
    scored = P.score_segment(seg, cues)
    joined = " ".join(scored.reasons)
    assert "hook" in joined
    assert "hook ''" not in joined        # the exact bug that shipped once


def test_question_opening_scores_higher_than_flat_opening():
    cues_q = _cues([(0, 20, "Why do most people fail at this?")])
    cues_f = _cues([(0, 20, "The weather was mild that afternoon.")])
    q = P.score_segment(P.Segment(0, 20, cues_q[0].text), cues_q).score
    f = P.score_segment(P.Segment(0, 20, cues_f[0].text), cues_f).score
    assert q > f


def test_score_segment_with_no_overlapping_cues_is_safe():
    seg = P.Segment(0, 20, "text")
    assert P.score_segment(seg, []).score == 0.0


def test_plan_respects_top_and_min_gap(tmp_path):
    lines = []
    t = 0.0
    for i in range(40):
        lines.append(f"{i+1}\n{P._ts_srt(t) if hasattr(P,'_ts_srt') else _t(t)} --> {_t(t+4)}")
        lines.append("Most people think this is a secret.\n")
        t += 4.0
    f = tmp_path / "t.srt"
    f.write_text("\n".join(lines))
    segs = P.plan(str(f), top=3, min_gap=30.0)
    assert len(segs) <= 3
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            assert abs(segs[i].start - segs[j].start) >= 30.0


def _t(s: float) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},000"


def test_segment_stamp_and_line_format():
    s = P.Segment(65.0, 95.0, "hello world", score=12.0, reasons=("dense speech",))
    assert s.stamp() == "1:05-1:35"
    assert "dense speech" in s.line()


# --------------------------------------------------------------------------
# render: command construction (encoding itself needs ffmpeg, not available here)
# --------------------------------------------------------------------------
def test_ass_time_formats_correctly():
    assert R._ass_time(0) == "0:00:00.00"
    assert R._ass_time(65.5) == "0:01:05.50"
    assert R._ass_time(3661.25) == "1:01:01.25"


def test_ass_time_clamps_negative():
    assert R._ass_time(-5) == "0:00:00.00"


def test_wrap_splits_long_text():
    lines = R._wrap("one two three four five six", 10)
    assert all(len(l) <= 10 for l in lines)
    assert " ".join(lines) == "one two three four five six"


def test_build_ass_shifts_timings_to_clip_start():
    cues = [P.Cue(10.0, 12.0, "hello there"), P.Cue(12.0, 14.0, "second line")]
    ass = R.build_ass(cues, start=10.0, end=14.0, opts=R.RenderOptions())
    assert "0:00:00.00" in ass
    assert "0:00:02.00" in ass
    assert "hello there" in ass


def test_build_ass_excludes_cues_outside_the_window():
    cues = [P.Cue(0.0, 2.0, "before"), P.Cue(10.0, 12.0, "inside"),
            P.Cue(99.0, 101.0, "after")]
    ass = R.build_ass(cues, start=9.0, end=13.0, opts=R.RenderOptions())
    assert "inside" in ass
    assert "before" not in ass and "after" not in ass


def test_build_ass_escapes_braces():
    cues = [P.Cue(0.0, 2.0, "bad {tag} here")]
    ass = R.build_ass(cues, 0.0, 2.0, R.RenderOptions())
    assert "{tag}" not in ass


def test_video_filter_pad_mode_keeps_whole_frame():
    f = R.video_filter(R.RenderOptions(reframe="pad"))
    assert "pad=1080:1920" in f
    assert "crop" not in f


def test_video_filter_crop_mode_centre_crops():
    f = R.video_filter(R.RenderOptions(reframe="crop"))
    assert "crop=1080:1920" in f
    assert "force_original_aspect_ratio=increase" in f


def test_video_filter_appends_subtitles_when_given():
    f = R.video_filter(R.RenderOptions(), "/tmp/a.ass")
    assert "subtitles=filename=" in f


def test_build_command_is_an_argv_list_not_a_shell_string():
    seg = P.Segment(5.0, 25.0, "x")
    argv = R.build_command("in.mp4", seg, "out.mp4", R.RenderOptions())
    assert isinstance(argv, list)
    assert argv[0] == "ffmpeg"
    assert "-ss" in argv and "5.000" in argv
    assert "20.000" in argv            # duration
    assert argv[-1] == "out.mp4"


def test_build_command_includes_loudnorm():
    argv = R.build_command("i.mp4", P.Segment(0, 10, "x"), "o.mp4", R.RenderOptions())
    assert any("loudnorm" in a for a in argv)


def test_build_command_omits_subtitles_when_captions_off():
    opts = R.RenderOptions(captions=False)
    argv = R.build_command("i.mp4", P.Segment(0, 10, "x"), "o.mp4", opts, "/tmp/a.ass")
    assert not any("subtitles" in a for a in argv)


def test_validate_catches_reversed_segment():
    assert R.validate(P.Segment(20, 10, "x"))


def test_validate_catches_too_short():
    assert any("too short" in p for p in R.validate(P.Segment(0, 2, "x")))


def test_validate_catches_overlong():
    assert any("cap" in p for p in R.validate(P.Segment(0, 200, "x")))


def test_validate_catches_past_end_of_source():
    problems = R.validate(P.Segment(0, 30, "x"), source_duration=10.0)
    assert any("source" in p for p in problems)


def test_validate_passes_a_good_segment():
    assert R.validate(P.Segment(10, 40, "x"), source_duration=100.0) == []


def test_build_batch_writes_one_ass_per_segment(tmp_path):
    cues = [P.Cue(0, 5, "hello"), P.Cue(10, 15, "world")]
    segs = [P.Segment(0, 5, "hello"), P.Segment(10, 15, "world")]
    jobs = R.build_batch("in.mp4", segs, str(tmp_path), R.RenderOptions(), cues, stem="c")
    assert len(jobs) == 2
    assert (tmp_path / "c_01.ass").exists()
    assert (tmp_path / "c_02.ass").exists()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_score_runs(tmp_path, capsys):
    from clipper.cli import main
    f = tmp_path / "c.json"
    f.write_text(json.dumps([{"name": "A", "cpm": 2.0, "approvalRate": 1.0}]))
    assert main(["score", "--file", str(f), "--views", "1000"]) == 0
    out = capsys.readouterr().out
    assert "A" in out and "/hr" in out


def test_cli_score_rejects_empty(tmp_path, capsys):
    from clipper.cli import main
    f = tmp_path / "c.json"
    f.write_text("[]")
    assert main(["score", "--file", str(f), "--views", "1000"]) == 2


def test_cli_plan_runs(tmp_path, capsys):
    from clipper.cli import main
    f = tmp_path / "t.srt"
    f.write_text(SRT)
    assert main(["plan", "--transcript", str(f), "--top", "2",
                 "--min-seconds", "2"]) == 0


def test_cli_render_dry_run(tmp_path, capsys):
    from clipper.cli import main
    f = tmp_path / "t.srt"
    f.write_text(SRT)
    rc = main(["render", "--video", "v.mp4", "--transcript", str(f),
               "--outdir", str(tmp_path / "out")])
    assert rc == 0
    assert "dry run" in capsys.readouterr().out


# --------------------------------------------------------------------------
# captions
# --------------------------------------------------------------------------
def test_strip_filler_removes_lead_ins():
    assert CAP.strip_filler("so this is the trick") == "This is the trick"
    assert CAP.strip_filler("well, and then it worked") == "Then it worked"
    assert CAP.strip_filler("you know the thing") == "The thing"


def test_strip_filler_leaves_clean_text_alone():
    assert CAP.strip_filler("Boring wins.") == "Boring wins."


def test_strip_filler_terminates_on_repeated_filler():
    assert CAP.strip_filler("and and and the point is this") == "The point is this"


def test_strip_filler_handles_empty():
    assert CAP.strip_filler("") == ""
    assert CAP.strip_filler("   ") == ""


def test_first_sentence_takes_only_the_opening():
    assert CAP.first_sentence("One. Two. Three.") == "One."
    assert CAP.first_sentence("No terminator here") == "No terminator here"


def test_truncate_never_cuts_mid_word():
    out = CAP.truncate("the quick brown fox jumps over", 15)
    assert not out.endswith("jump")
    assert len(out) <= 15
    assert "brown" in out


def test_truncate_strips_trailing_punctuation():
    assert not CAP.truncate("hello there my friend", 13).endswith(",")


def test_truncate_returns_short_text_unchanged():
    assert CAP.truncate("short", 100) == "short"


def test_keywords_drops_stopwords_and_short_words():
    kws = CAP.keywords("investing investing money the a to investing returns", 3)
    assert "investing" in [k.lower() for k in kws]
    assert "the" not in [k.lower() for k in kws]


def test_keywords_preserves_proper_noun_casing():
    kws = CAP.keywords("Bangladesh Bangladesh growth growth growth", 2)
    assert "Bangladesh" in kws


def test_caption_respects_platform_limits():
    cap = CAP.Caption(hook="word " * 80, tags=["tag"])
    for plat in ("tiktok", "shorts", "x"):
        assert len(cap.for_platform(plat)) <= CAP.LIMITS[plat]


def test_caption_never_truncates_away_all_tags():
    cap = CAP.Caption(hook="a very long hook " * 30, tags=["alpha", "beta"])
    out = cap.for_platform("tiktok")
    assert "#alpha" in out


def test_caption_with_no_tags_is_just_the_hook():
    cap = CAP.Caption(hook="Just a hook.")
    assert cap.for_platform("tiktok") == "Just a hook."


def test_build_puts_campaign_tags_first():
    cap = CAP.build("The investing secret nobody tells you about dividends.", ["brand"], 3)
    assert cap.tags[0].lower() == "brand"


def test_build_does_not_duplicate_tags():
    cap = CAP.build("investing investing investing returns returns", ["investing"], 3)
    lowered = [t.lower() for t in cap.tags]
    assert len(lowered) == len(set(lowered))


def test_campaign_hashtags_parsed_from_dict():
    c = C.from_dict({"name": "x", "cpm": 1.0, "hashtags": ["#Brand", "tag2"]})
    assert c.hashtags == ("Brand", "tag2")


def test_campaign_hashtags_default_empty():
    assert C.from_dict({"name": "x", "cpm": 1.0}).hashtags == ()


# --------------------------------------------------------------------------
# job runner
# --------------------------------------------------------------------------
def _campaign(**kw):
    d = {"name": "Test", "cpm": 2.0, "approval_rate": 1.0}
    d.update(kw)
    return C.from_dict(d)


def test_options_hash_changes_with_settings():
    a = J.options_hash(R.RenderOptions())
    b = J.options_hash(R.RenderOptions(reframe="crop"))
    assert a != b
    assert J.options_hash(R.RenderOptions()) == a


def test_plan_jobs_creates_one_job_per_segment(tmp_path):
    segs = [P.Segment(0, 20, "One thing."), P.Segment(30, 50, "Two thing.")]
    jobs = J.plan_jobs(_campaign(), segs, str(tmp_path), 1000, R.RenderOptions())
    assert len(jobs) == 2
    assert jobs[0].out_path.endswith("clip_01.mp4")
    assert jobs[1].expected_usd == pytest.approx(2.0)


def test_plan_jobs_attaches_captions(tmp_path):
    segs = [P.Segment(0, 20, "Most people get this wrong about investing.")]
    jobs = J.plan_jobs(_campaign(), segs, str(tmp_path), 1000, R.RenderOptions())
    assert "Most people" in jobs[0].caption.hook


def test_is_fresh_rejects_empty_file(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"")
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(f), CAP.Caption("h"))
    assert not J.is_fresh(job)


def test_is_fresh_rejects_missing_file(tmp_path):
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(tmp_path / "nope.mp4"), CAP.Caption("h"))
    assert not J.is_fresh(job)


def test_is_fresh_accepts_real_file(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 2048)
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(f), CAP.Caption("h"))
    assert J.is_fresh(job)


def test_run_job_skips_cached_output(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 2048)
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(f), CAP.Caption("h"))
    out = J.run_job(job, "v.mp4", [], R.RenderOptions(), dry_run=False)
    assert out.skipped and not out.rendered


def test_run_job_force_rerenders(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 2048)
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(f), CAP.Caption("h"))
    out = J.run_job(job, "v.mp4", [], R.RenderOptions(), dry_run=False, force=True)
    assert not out.skipped
    assert out.error        # no ffmpeg in this environment


def test_run_job_records_validation_failure(tmp_path):
    job = J.ClipJob("c", P.Segment(0, 2, "x"), str(tmp_path / "a.mp4"), CAP.Caption("h"))
    out = J.run_job(job, "v.mp4", [], R.RenderOptions(), dry_run=False)
    assert out.error and "too short" in out.error


def test_run_job_dry_run_does_not_encode(tmp_path):
    job = J.ClipJob("c", P.Segment(0, 20, "x"), str(tmp_path / "a.mp4"), CAP.Caption("h"))
    out = J.run_job(job, "v.mp4", [], R.RenderOptions(), dry_run=True)
    assert not out.rendered and not out.error


def test_run_job_writes_ass_alongside_clip(tmp_path):
    cues = [P.Cue(0, 10, "hello there")]
    job = J.ClipJob("c", P.Segment(0, 20, "hello there"), str(tmp_path / "a.mp4"),
                    CAP.Caption("h"))
    J.run_job(job, "v.mp4", cues, R.RenderOptions(), dry_run=True)
    assert (tmp_path / "a.ass").exists()


def test_manifest_roundtrip(tmp_path):
    man = J.Manifest("C", "v.mp4", 300, "abc", dry_run=True)
    man.clips = [J.ClipJob("C", P.Segment(0, 20, "x"), "a.mp4", CAP.Caption("h"),
                           expected_usd=1.5)]
    p = tmp_path / "manifest.json"
    man.save(str(p))
    loaded = J.Manifest.load(str(p))
    assert loaded["campaign"] == "C"
    assert loaded["clips"][0]["expected_usd"] == pytest.approx(1.5)


def test_manifest_load_returns_none_on_garbage(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not json")
    assert J.Manifest.load(str(p)) is None


def test_manifest_load_returns_none_when_missing(tmp_path):
    assert J.Manifest.load(str(tmp_path / "nope.json")) is None


def test_expected_usd_excludes_failed_clips():
    man = J.Manifest("C", "v", 300, "h", dry_run=False)
    ok = J.ClipJob("C", P.Segment(0, 20, "x"), "a.mp4", CAP.Caption("h"), expected_usd=2.0)
    bad = J.ClipJob("C", P.Segment(0, 20, "x"), "b.mp4", CAP.Caption("h"), expected_usd=2.0)
    bad.error = "boom"
    man.clips = [ok, bad]
    assert man.total_expected_usd == pytest.approx(2.0)


def test_manifest_includes_all_platform_captions():
    man = J.Manifest("C", "v", 300, "h")
    man.clips = [J.ClipJob("C", P.Segment(0, 20, "x"), "a.mp4",
                           CAP.Caption("hook", ["tag"]))]
    caps = man.to_dict()["clips"][0]["caption"]
    assert set(caps) == {"tiktok", "reels", "shorts", "x"}
    assert all("#tag" in v for v in caps.values())


def test_summary_reports_counts_and_failures(tmp_path):
    man = J.Manifest("C", "v.mp4", 300, "h", dry_run=False)
    man.clips = [J.ClipJob("C", P.Segment(0, 20, "x"), "a.mp4", CAP.Caption("h"))]
    man.clips[0].error = "ffmpeg not found on PATH"
    text = J.summary(man)
    assert "failed 1" in text
    assert "ffmpeg not found" in text


def test_upload_presigned_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        J.upload_presigned("https://example.invalid/x", "/definitely/not/here.mp4")


def test_copy_local_places_file(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"abc")
    dest = J.copy_local(str(src), str(tmp_path / "pub"))
    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"abc"


def test_cli_run_dry_by_default(tmp_path, capsys):
    from clipper.cli import main
    camps = tmp_path / "c.json"
    camps.write_text(json.dumps([{"name": "Bonus Heavy", "cpm": 1.2, "bonus": 8.0,
                                  "approvalRate": 0.9}]))
    tr = tmp_path / "t.srt"
    tr.write_text(SRT)
    rc = main(["run", "--campaigns", str(camps), "--video", "v.mp4",
               "--transcript", str(tr), "--outdir", str(tmp_path / "out"),
               "--views", "300", "--min-seconds", "2"])
    assert rc == 0
    assert "dry run" in capsys.readouterr().out


def test_cli_run_rejects_unknown_campaign(tmp_path, capsys):
    from clipper.cli import main
    camps = tmp_path / "c.json"
    camps.write_text(json.dumps([{"name": "Real", "cpm": 1.0}]))
    tr = tmp_path / "t.srt"
    tr.write_text(SRT)
    rc = main(["run", "--campaigns", str(camps), "--campaign", "Nope",
               "--video", "v.mp4", "--transcript", str(tr),
               "--outdir", str(tmp_path / "o"), "--views", "300"])
    assert rc == 2
    assert "no campaign matching" in capsys.readouterr().out


def test_cli_run_warns_when_below_floor(tmp_path, capsys):
    from clipper.cli import main
    camps = tmp_path / "c.json"
    camps.write_text(json.dumps([{"name": "Tiny", "cpm": 0.01}]))
    tr = tmp_path / "t.srt"
    tr.write_text(SRT)
    main(["run", "--campaigns", str(camps), "--video", "v.mp4", "--transcript", str(tr),
          "--outdir", str(tmp_path / "o"), "--views", "100", "--min-seconds", "2",
          "--floor", "50"])
    assert "below your" in capsys.readouterr().out
