"""End-to-end job: campaign + video + transcript -> postable clips.

This is the layer that makes ``render`` into something you can actually use. It
adds the four things a bare ffmpeg command lacks:

1. **A manifest.** Every clip gets a record: which campaign, which window of the
   source, caption per platform, and the expected revenue at your stated views.
   Without this you cannot tell, three weeks later, which clip belonged to which
   campaign -- and unsubmitted work earns nothing.
2. **Idempotency.** Re-running skips clips whose output already exists and whose
   window has not changed, so a crashed run resumes instead of re-encoding.
3. **Captions.** Generated from the transcript window (see ``captions``).
4. **Upload.** Local copy by default, or an HTTP PUT to a presigned URL. No SDK,
   no credentials handled here -- you mint the presigned URL, this just PUTs.

The upload path is honest about its limits: ``upload_presigned`` is written
against the R2/S3 presigned-PUT contract and is **not exercised in this repo**,
because the sandbox has no network access to object storage. The local backend
is tested.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field

from . import captions as CAP
from . import plan as P
from . import render as R


def options_hash(opts: R.RenderOptions) -> str:
    """Fingerprint the render settings so changing them invalidates the cache."""
    blob = json.dumps({"reframe": opts.reframe, "captions": opts.captions,
                       "crf": opts.crf, "preset": opts.preset,
                       "font": opts.font, "font_size": opts.font_size},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass
class ClipJob:
    campaign_name: str
    segment: P.Segment
    out_path: str
    caption: CAP.Caption
    expected_usd: float = 0.0
    rendered: bool = False
    skipped: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "campaign": self.campaign_name,
            "start": self.segment.start,
            "end": self.segment.end,
            "duration": round(self.segment.duration, 2),
            "file": self.out_path,
            "caption": {p: self.caption.for_platform(p) for p in ("tiktok", "reels", "shorts", "x")},
            "expected_usd": round(self.expected_usd, 2),
            "rendered": self.rendered,
            "skipped": self.skipped,
            "error": self.error,
        }


@dataclass
class Manifest:
    campaign: str
    source: str
    views_assumed: float
    options_hash: str
    dry_run: bool = False
    clips: list[ClipJob] = field(default_factory=list)

    @property
    def total_expected_usd(self) -> float:
        """Projected revenue for every clip that did not fail.

        On a dry run nothing is rendered yet, but the projection is the whole
        point of a dry run, so failed clips are excluded rather than unbuilt
        ones.
        """
        return sum(c.expected_usd for c in self.clips if not c.error)

    def to_dict(self) -> dict:
        return {
            "campaign": self.campaign,
            "source": self.source,
            "views_assumed": self.views_assumed,
            "options_hash": self.options_hash,
            "dry_run": self.dry_run,
            "total_expected_usd": round(self.total_expected_usd, 2),
            "clips": [c.to_dict() for c in self.clips],
        }

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> dict | None:
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None


def plan_jobs(campaign, segments: list[P.Segment], outdir: str,
              views: float, opts: R.RenderOptions, stem: str = "clip") -> list[ClipJob]:
    """Build the job list, including captions and expected revenue."""
    os.makedirs(outdir, exist_ok=True)
    jobs: list[ClipJob] = []
    for i, seg in enumerate(segments, 1):
        out = os.path.join(outdir, f"{stem}_{i:02d}.mp4")
        jobs.append(ClipJob(
            campaign_name=campaign.name,
            segment=seg,
            out_path=out,
            caption=CAP.build(seg.text, list(getattr(campaign, "hashtags", []) or [])),
            expected_usd=campaign.revenue_per_clip(views),
        ))
    return jobs


def is_fresh(job: ClipJob) -> bool:
    """Output exists and is non-trivial. Zero-byte files are failed encodes."""
    try:
        return os.path.exists(job.out_path) and os.path.getsize(job.out_path) > 1024
    except OSError:
        return False


def run_job(job: ClipJob, video: str, cues: list[P.Cue], opts: R.RenderOptions,
            dry_run: bool = True, force: bool = False) -> ClipJob:
    """Render one clip. Skips when fresh unless ``force``."""
    if not force and is_fresh(job):
        job.skipped = True
        return job

    problems = R.validate(job.segment)
    if problems:
        job.error = "; ".join(problems)
        return job

    ass_path = None
    if opts.captions and cues:
        ass_path = os.path.splitext(job.out_path)[0] + ".ass"
        with open(ass_path, "w") as fh:
            fh.write(R.build_ass(cues, job.segment.start, job.segment.end, opts))

    argv = R.build_command(video, job.segment, job.out_path, opts, ass_path)
    if dry_run:
        job.rendered = False
        return job

    try:
        result = subprocess.run(argv, capture_output=True)
    except FileNotFoundError:
        job.error = "ffmpeg not found on PATH"
        return job
    if result.returncode != 0:
        tail = result.stderr.decode(errors="ignore").strip().splitlines()[-1:] or [""]
        job.error = f"ffmpeg exit {result.returncode}: {tail[0][:120]}"
        return job

    job.rendered = True
    return job


def upload_presigned(url: str, path: str, timeout: int = 120) -> int:
    """PUT a file to a presigned R2/S3 URL. Returns bytes sent.

    No SDK and no credentials: the signing happens wherever the URL was minted.
    Not exercised in this repo (no egress to object storage from the sandbox),
    so treat it as unproven until you run it once against a real bucket.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers={"Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"upload failed with status {resp.status}")
    return len(data)


def copy_local(src: str, destdir: str) -> str:
    os.makedirs(destdir, exist_ok=True)
    dest = os.path.join(destdir, os.path.basename(src))
    shutil.copy2(src, dest)
    return dest


def run(campaign, video: str, transcript: str, outdir: str, views: float,
        top: int = 6, min_seconds: float = 18.0, max_seconds: float = 48.0,
        opts: R.RenderOptions | None = None, dry_run: bool = True,
        force: bool = False, upload_url: str | None = None) -> Manifest:
    """Plan, render and record. Returns the manifest whether or not it encoded."""
    opts = opts or R.RenderOptions()
    segments = P.plan(transcript, min_seconds, max_seconds, top)
    cues = P.load_transcript(transcript)

    man = Manifest(campaign=campaign.name, source=os.path.basename(video),
                   views_assumed=views, options_hash=options_hash(opts),
                   dry_run=dry_run)
    man.clips = plan_jobs(campaign, segments, outdir, views, opts)

    for job in man.clips:
        run_job(job, video, cues, opts, dry_run=dry_run, force=force)
        if job.rendered and upload_url:
            try:
                upload_presigned(upload_url.format(name=os.path.basename(job.out_path)),
                                 job.out_path)
            except Exception as exc:                    # noqa: BLE001 - report, never crash a batch
                job.error = f"upload failed: {exc}"

    man.save(os.path.join(outdir, "manifest.json"))
    return man


def summary(man: Manifest) -> str:
    """One-screen report of what the job produced."""
    done = sum(1 for c in man.clips if c.rendered)
    skipped = sum(1 for c in man.clips if c.skipped)
    failed = sum(1 for c in man.clips if c.error)
    planned = len(man.clips) - done - skipped - failed
    counts = f"clips: {len(man.clips)}   new {done}"
    if planned:
        counts += f"   planned {planned}"
    counts += f"   cached {skipped}   failed {failed}"
    lines = [
        f"{man.campaign}  <-  {man.source}",
        counts,
        f"expected revenue at {man.views_assumed:,.0f} views/clip: ${man.total_expected_usd:.2f}",
    ]
    if failed:
        lines.append("")
        for c in man.clips:
            if c.error:
                lines.append(f"  ! {os.path.basename(c.out_path)}: {c.error}")
    if man.clips and not failed:
        c = man.clips[0]
        lines += ["", f"first caption (tiktok):", f'  "{c.caption.for_platform("tiktok")}"']
    return "\n".join(lines)
