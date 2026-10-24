"""Clipping economics: which Whop campaign is actually worth your hours.

The mistake almost every clipper makes is ranking campaigns by the headline
rate -- "$5 per 1k views!" -- and never computing what that rate pays for a
*typical* clip they would actually make. This module refuses to show you the
headline number without the arithmetic underneath it:

    revenue_per_clip = views_per_clip / 1000 * cpm * approval_rate
    dollars_per_hour = revenue_per_clip / (minutes_per_clip / 60)

That second line is the only number that matters, because your scarce resource
is hours, not clips. A $5/1k campaign where your clips get 200 views pays
$1.00 less the rejections. A $2/1k campaign where clips get 4,000 views pays
$8.00. The worse-sounding campaign is 8x better and nobody advertises that.

Every input here is something you must estimate from real data, and the defaults
are deliberately pessimistic. Guessing ``views_per_clip`` is where people lie to
themselves, so the CLI requires it as an explicit argument rather than defaulting
it to something flattering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Realistic cost of producing one submission when the editing is already
# automated by clipper/render.py. The remaining labour is choosing the moment,
# writing the caption, posting and filing the submission link.
DEFAULT_MINUTES_PER_CLIP = 12.0


@dataclass
class Campaign:
    name: str
    cpm: float                      # reward per 1,000 views, USD
    budget_left: float = 0.0        # USD remaining in the pool
    creators: int = 0               # competing clippers
    approval_rate: float = 0.7      # share of submissions a brand accepts
    platforms: tuple[str, ...] = ()
    bonus: float = 0.0              # flat per-approved-submission bonus
    hashtags: tuple[str, ...] = ()  # tags the campaign requires or rewards
    ends_in_days: float | None = None

    def revenue_per_clip(self, views: float) -> float:
        """Expected dollars for one accepted-and-viewed clip."""
        return (views / 1000.0) * self.cpm * self.approval_rate + self.bonus * self.approval_rate

    def dollars_per_hour(self, views: float, minutes: float = DEFAULT_MINUTES_PER_CLIP) -> float:
        if minutes <= 0:
            return 0.0
        return self.revenue_per_clip(views) / (minutes / 60.0)

    def views_to_beat(self, other_hourly: float, minutes: float = DEFAULT_MINUTES_PER_CLIP) -> float:
        """Views this campaign needs per clip to match a given $/hour."""
        if self.cpm <= 0:
            return float("inf")
        target = other_hourly * (minutes / 60.0)
        return max(0.0, (target - self.bonus * self.approval_rate)) * 1000.0 / (
            self.cpm * self.approval_rate)

    def pool_per_creator(self) -> float:
        """How much of the remaining budget is left per competitor.

        A huge budget with 4,000 clippers is worth less than a small budget with
        20. This is the number that reveals a saturated campaign.
        """
        if self.creators <= 0:
            return self.budget_left
        return self.budget_left / self.creators

    def warnings(self, views: float) -> list[str]:
        out = []
        if self.approval_rate < 0.5:
            out.append(f"only {self.approval_rate:.0%} of submissions get approved")
        if self.creators > 500:
            out.append(f"{self.creators:,} clippers competing")
        if 0 < self.pool_per_creator() < 20:
            out.append(f"pool is only ${self.pool_per_creator():.2f} per creator")
        if self.ends_in_days is not None and self.ends_in_days < 3:
            out.append(f"campaign ends in {self.ends_in_days:.1f} days")
        if self.cpm >= 3 and views < 1000:
            out.append("high CPM but low views -- rate is bait, reach is the constraint")
        bonus_share = (self.bonus * self.approval_rate) / max(1e-9, self.revenue_per_clip(views))
        if bonus_share > 0.6:
            out.append(f"{bonus_share:.0%} of revenue is the flat bonus -- "
                       "confirm it is uncapped and per submission, not per campaign")
        return out

    def verdict(self, views: float, minutes: float = DEFAULT_MINUTES_PER_CLIP,
                floor_hourly: float = 8.0) -> str:
        h = self.dollars_per_hour(views, minutes)
        if h >= floor_hourly * 2.5:
            return "STRONG"
        if h >= floor_hourly:
            return "OK"
        if h >= floor_hourly * 0.5:
            return "MARGINAL"
        return "SKIP"

    def row(self, views: float, minutes: float = DEFAULT_MINUTES_PER_CLIP,
            floor_hourly: float = 8.0) -> str:
        return (f"{self.name[:26]:<26} ${self.cpm:>5.2f}/1k  {views:>6,.0f} views  "
                f"${self.revenue_per_clip(views):>6.2f}/clip  "
                f"${self.dollars_per_hour(views, minutes):>6.2f}/hr  "
                f"{self.verdict(views, minutes, floor_hourly)}")


def rank(campaigns: list[Campaign], views: float,
         minutes: float = DEFAULT_MINUTES_PER_CLIP,
         floor_hourly: float = 8.0) -> list[Campaign]:
    """Best expected dollars-per-hour first. This is the only sane order."""
    return sorted(campaigns, key=lambda c: c.dollars_per_hour(views, minutes), reverse=True)


def from_dict(d: dict) -> Campaign:
    """Build from a scraper payload (Apify actors and manual exports both fit)."""
    def num(*keys, default=0.0):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v.replace("$", "").replace(",", ""))
                except ValueError:
                    continue
        return default

    plats = d.get("platforms") or d.get("allowedPlatforms") or ()
    if isinstance(plats, str):
        plats = tuple(p.strip() for p in plats.split(",") if p.strip())

    return Campaign(
        name=str(d.get("name") or d.get("title") or d.get("campaignName") or "?"),
        cpm=num("cpm", "rewardPerThousand", "reward_per_1k", "rate"),
        budget_left=num("budgetLeft", "budget_left_usd", "budgetRemaining", "remainingBudget"),
        creators=int(num("creators", "creatorCount", "clippers")),
        approval_rate=num("approvalRate", "approval_rate", default=0.7) or 0.7,
        platforms=tuple(plats),
        hashtags=tuple(
            str(t).strip().lstrip("#") for t in
            (d.get("hashtags") or d.get("requiredHashtags") or d.get("tags") or ())
            if str(t).strip().lstrip("#")),
        bonus=num("bonus", "flatBonus"),
        ends_in_days=(num("endsInDays") or None),
    )


def breakeven_views(campaigns: list[Campaign], minutes: float = DEFAULT_MINUTES_PER_CLIP,
                    floor_hourly: float = 8.0) -> list[tuple[str, float]]:
    """Views per clip each campaign needs to hit the $/hour floor.

    Use this to sanity-check your view estimate. If a campaign needs 9,000 views
    per clip to be worth doing, and your account gets 300, it is not a campaign
    problem -- it is a reach problem, and no amount of editing fixes it.
    """
    return [(c.name, c.views_to_beat(floor_hourly, minutes)) for c in campaigns]


@dataclass
class Portfolio:
    """What a week of clipping actually adds up to."""

    campaigns: list[Campaign] = field(default_factory=list)
    clips_per_week: int = 10

    def weekly(self, views: float, minutes: float = DEFAULT_MINUTES_PER_CLIP) -> dict:
        best = rank(self.campaigns, views, minutes)
        if not best:
            return {"revenue": 0.0, "hours": 0.0, "hourly": 0.0}
        top = best[0]
        hours = self.clips_per_week * minutes / 60.0
        revenue = self.clips_per_week * top.revenue_per_clip(views)
        return {"revenue": revenue, "hours": hours,
                "hourly": revenue / hours if hours else 0.0, "campaign": top.name}
