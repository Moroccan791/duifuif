"""Choosing which 30 seconds to cut, from a transcript.

This is the honest weak point of any auto-clipper, so it is worth being precise
about what it does and does not do.

What it does: given a transcript with word or cue timings, it finds every
candidate window of a workable length that starts and ends on a sentence
boundary, then scores those windows on measurable properties -- speech density,
hook language in the first sentence, a payoff near the end, self-containment.

What it does not do: it cannot tell whether a moment is *funny*. A hook-word
dictionary is a proxy for a good opening, not a substitute for taste. Treat the
output as a shortlist to review, not a decision. The reason this still saves
most of the labour is that watching a 90-minute podcast to find four moments is
the slow part, and narrowing that to six candidates is the win.

Output is an ``EDL`` -- plain data describing what to cut -- which
``clipper.render`` turns into ffmpeg commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# openers that tend to hold a scroll. Deliberately short -- long lists start
# matching everything and the score stops discriminating.
HOOK_PATTERNS = [
    r"\bhow (i|you|we|to)\b", r"\bwhy (i|you|we)\b", r"\bwhat (i|you|we) (learned|wish)\b",
    r"\bnobody (tells|talks)\b", r"\bthe (truth|secret|problem|reason)\b",
    r"\b(stop|never|always|don'?t)\b", r"\bmost people\b", r"\bi (made|lost|spent|earned)\b",
    r"\b\d+ (years|months|days|k|grand|million)\b", r"\bhere'?s (how|why|the)\b",
    r"\bif you\b", r"\bthis is (why|how|the)\b", r"\bwarning\b",
]
# An empty alternative in an alternation matches at every position, which made
# every hook silently come back as "". Guard it rather than trusting the list.
assert all(p.strip() for p in HOOK_PATTERNS), "empty hook pattern would match everything"
_HOOK_RE = re.compile("|".join(HOOK_PATTERNS), re.IGNORECASE)
# NOTE: the patterns above use capturing groups for readability. findall() would
# therefore return tuples of the group contents rather than the matched text, so
# always go through finditer() and read group(0).

PAYOFF_PATTERNS = [r"\bthat'?s (why|how|the)\b", r"\bso (that|this) is\b", r"\bwhich means\b",
                   r"\bthe (result|lesson|point)\b", r"\bend(ed)? up\b"]
assert all(p.strip() for p in PAYOFF_PATTERNS), "empty payoff pattern would match everything"
_PAYOFF_RE = re.compile("|".join(PAYOFF_PATTERNS), re.IGNORECASE)

SENTENCE_END = re.compile(r"[.!?]\s*$")


@dataclass
class Cue:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Segment:
    start: float
    end: float
    text: str
    score: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def duration(self) -> float:
        return self.end - self.start

    def stamp(self) -> str:
        def f(t: float) -> str:
            m, s = divmod(int(round(t)), 60)
            return f"{m}:{s:02d}"
        return f"{f(self.start)}-{f(self.end)}"

    def line(self) -> str:
        return (f"  {self.stamp():>12}  {self.duration:>4.0f}s  "
                f"score {self.score:>5.1f}  {' '.join(self.reasons):<28}  "
                f"\"{self.text[:60].strip()}...\"")


def parse_ts(s: str) -> float:
    """'00:01:23,400' or '1:23.4' -> seconds."""
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        idx = 0
        if "-->" not in lines[0]:
            idx = 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        a, b = lines[idx].split("-->")[:2]
        body = " ".join(lines[idx + 1:]).strip()
        if body:
            cues.append(Cue(parse_ts(a), parse_ts(b), body))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    body = "\n".join(l for l in text.splitlines()
                     if not l.startswith("WEBVTT") and not l.startswith("NOTE"))
    return parse_srt(body)


def parse_words(text: str) -> list[Cue]:
    """'word 0.00 0.42' per line, or '00:00:01 --> 00:00:02 word'."""
    cues: list[Cue] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.*?)\s+(\d+\.\d+)\s+(\d+\.\d+)$", line)
        if m:
            cues.append(Cue(float(m.group(2)), float(m.group(3)), m.group(1)))
    return cues


def load_transcript(path: str) -> list[Cue]:
    text = open(path, errors="ignore").read()
    for parser in (parse_vtt, parse_srt):
        cues = parser(text)
        if cues:
            return sorted(cues, key=lambda c: c.start)
    cues = parse_words(text)
    if cues:
        return sorted(cues, key=lambda c: c.start)
    raise ValueError(f"could not parse a transcript from {path} (try .srt or .vtt)")


def _speech_density(cues: list[Cue]) -> float:
    """Words per second across a set of cues. Dead air drags this down."""
    if not cues:
        return 0.0
    span = max(c.end for c in cues) - min(c.start for c in cues)
    if span <= 0:
        return 0.0
    return sum(c.words for c in cues) / span


def build_segments(cues: list[Cue], min_seconds: float = 18.0,
                   max_seconds: float = 48.0) -> list[Segment]:
    """Every sentence-aligned window in the length range."""
    if not cues:
        return []
    # index of the first cue that ends a sentence, so windows start cleanly
    starts = [0] + [i for i, c in enumerate(cues) if SENTENCE_END.search(c.text)]

    out: list[Segment] = []
    for si in starts:
        if si >= len(cues):
            continue
        for ei in range(si + 1, len(cues)):
            dur = cues[ei].end - cues[si].start
            if dur < min_seconds:
                continue
            if dur > max_seconds:
                break
            if not SENTENCE_END.search(cues[ei].text) and ei != len(cues) - 1:
                continue
            chunk = cues[si:ei + 1]
            out.append(Segment(
                start=round(cues[si].start, 3),
                end=round(cues[ei].end, 3),
                text=" ".join(c.text for c in chunk),
            ))
    return out


def score_segment(seg: Segment, cues: list[Cue]) -> Segment:
    """Heuristic quality score. Read the reasons, not just the number."""
    inside = [c for c in cues if c.start >= seg.start and c.end <= seg.end]
    if not inside:
        return seg

    density = _speech_density(inside)
    first = inside[0].text
    last = " ".join(c.text for c in inside[-3:])

    score = 0.0
    reasons: list[str] = []

    # dead air is the single most common reason a clip dies
    if density >= 3.2:
        score += 30
        reasons.append("dense speech")
    elif density >= 2.4:
        score += 18
        reasons.append("ok density")
    elif density < 1.6:
        score -= 20
        reasons.append("too much silence")

    hooks = [m.group(0) for m in _HOOK_RE.finditer(first)]
    if hooks:
        score += 28
        reasons.append(f"hook '{hooks[0][:20]}'")
    if first.rstrip().endswith("?"):
        score += 12
        reasons.append("opens with a question")

    if _PAYOFF_RE.search(last):
        score += 14
        reasons.append("has payoff")

    # self-containment: a clip that starts mid-thought reads as broken
    if _HOOK_RE.search(first) or first[:1].isupper():
        score += 6

    if seg.duration < 22:
        score += 4                       # shorter holds retention
    elif seg.duration > 42:
        score -= 6

    seg.score = round(score, 1)
    seg.reasons = tuple(reasons)
    return seg


def plan(transcript_path: str, min_seconds: float = 18.0, max_seconds: float = 48.0,
         top: int = 6, min_gap: float = 30.0) -> list[Segment]:
    """Ranked, non-overlapping clip candidates.

    ``min_gap`` stops the shortlist being six variations of the same 30 seconds,
    which is what happens when a window scores well and every adjacent window
    inherits the score.
    """
    cues = load_transcript(transcript_path)
    scored = [score_segment(s, cues) for s in build_segments(cues, min_seconds, max_seconds)]
    scored.sort(key=lambda s: s.score, reverse=True)

    picked: list[Segment] = []
    for s in scored:
        if any(abs(s.start - p.start) < min_gap for p in picked):
            continue
        picked.append(s)
        if len(picked) >= top:
            break
    return picked


@dataclass
class EDL:
    """Edit decision list: what to cut and how to present it."""

    source: str
    segments: list[Segment]

    def to_dict(self) -> dict:
        return {"source": self.source,
                "segments": [{"start": s.start, "end": s.end, "text": s.text,
                              "score": s.score} for s in self.segments]}
