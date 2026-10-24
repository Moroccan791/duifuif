"""Turn an EDL into ffmpeg commands: cut, reframe to 9:16, burn captions.

This is the part that removes the editing labour, and it is genuinely
deterministic -- unlike choosing the moment, there is a correct answer for
"make this vertical with readable captions" and ffmpeg can produce it.

Three decisions worth knowing about:

**Reframe.** Two modes. ``crop`` takes the centre 9:16 slice and throws the
sides away -- best when the subject is centred, cheapest to encode. ``pad``
keeps the whole frame and fills the sides with a blurred copy of itself --
safest for two-person podcasts where cropping decapitates someone. Default is
``pad`` because a wrong crop is unwatchable and a blur is merely unfashionable.

**Captions.** Rendered as ASS, not SRT, because burned-in styling (outline,
bottom margin, bold) needs ASS. Captions are burned in rather than uploaded as
a sidecar file: every platform treats them differently and most clippers find
that silently captions get dropped.

**Loudness.** ``loudnorm`` to -14 LUFS, the target the platforms normalise to.
Without it, a quiet clip sounds broken next to everything else in a feed.

Commands are returned as argument lists, never shell strings, so a filename
containing a quote cannot turn into a shell injection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .plan import Cue, Segment

W, H = 1080, 1920
LUFS = -14.0


@dataclass
class RenderOptions:
    reframe: str = "pad"           # "pad" | "crop"
    captions: bool = True
    max_chars_per_line: int = 16
    font: str = "DejaVu Sans"
    font_size: int = 62
    crf: int = 20
    preset: str = "medium"


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def build_ass(cues: list[Cue], start: float, end: float, opts: RenderOptions) -> str:
    """ASS caption file, timings shifted so the clip starts at zero."""
    inside = [c for c in cues if c.end > start and c.start < end]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{opts.font},{opts.font_size},&H00FFFFFF,&H00000000,&H00000000,-1,0,1,5,0,2,80,80,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines_out = []
    for c in inside:
        a = max(c.start, start) - start
        b = min(c.end, end) - start
        if b - a <= 0.01:
            continue
        text = "\\N".join(_wrap(c.text, opts.max_chars_per_line))
        text = text.replace("{", "(").replace("}", ")")
        lines_out.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Cap,,0,0,0,,{text}")
    return header + "\n".join(lines_out) + "\n"


def video_filter(opts: RenderOptions, ass_path: str | None = None) -> str:
    """The filtergraph: scale/fit, optional blur pad, optional subtitles."""
    if opts.reframe == "crop":
        chain = [
            f"scale={W}:{H}:force_original_aspect_ratio=increase",
            f"crop={W}:{H}",
        ]
    else:
        chain = [
            f"scale={W}:{H}:force_original_aspect_ratio=decrease",
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]
    if ass_path:
        # the path is quoted and escaped for the filtergraph parser
        safe = ass_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        chain.append(f"subtitles=filename='{safe}'")
    return ",".join(chain)


def build_command(video: str, seg: Segment, out: str, opts: RenderOptions,
                  ass_path: str | None = None) -> list[str]:
    """ffmpeg argv for one clip.

    Audio is re-encoded with loudnorm; ``-ss`` before ``-i`` seeks fast, and the
    duration is made explicit so a long source does not get fully decoded.
    """
    dur = seg.end - seg.start
    vf = video_filter(opts, ass_path if opts.captions else None)
    return [
        "ffmpeg", "-y",
        "-ss", f"{seg.start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", video,
        "-vf", vf,
        "-af", f"loudnorm=I={LUFS}:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out,
    ]


def build_batch(video: str, segments: list[Segment], outdir: str,
                opts: RenderOptions | None = None, cues: list[Cue] | None = None,
                stem: str = "clip") -> list[tuple[list[str], str]]:
    """One (argv, ass_path) per segment. Writes the .ass files as a side effect."""
    opts = opts or RenderOptions()
    os.makedirs(outdir, exist_ok=True)
    jobs: list[tuple[list[str], str]] = []
    for i, seg in enumerate(segments, 1):
        out = os.path.join(outdir, f"{stem}_{i:02d}.mp4")
        ass = os.path.join(outdir, f"{stem}_{i:02d}.ass")
        if opts.captions and cues:
            with open(ass, "w") as fh:
                fh.write(build_ass(cues, seg.start, seg.end, opts))
        elif opts.captions:
            open(ass, "w").close()
        jobs.append((build_command(video, seg, out, opts, ass), out if not opts.captions else ass))
    return jobs


def probe_command(video: str) -> list[str]:
    """Duration/resolution, needed to validate a segment fits inside the source."""
    return ["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json", video]


def validate(seg: Segment, source_duration: float | None = None) -> list[str]:
    """Catch the mistakes that waste an encode."""
    problems = []
    if seg.end <= seg.start:
        problems.append("segment ends before it starts")
    if seg.duration < 5:
        problems.append(f"only {seg.duration:.1f}s -- too short to be a clip")
    if seg.duration > 90:
        problems.append(f"{seg.duration:.0f}s -- most platforms cap shorts at 60-90s")
    if seg.start < 0:
        problems.append("negative start")
    if source_duration is not None and seg.end > source_duration + 0.5:
        problems.append(f"ends at {seg.end:.1f}s but source is {source_duration:.1f}s")
    return problems
