"""Caption and hashtag generation, sized for each platform.

The caption is the last piece of manual labour between a rendered clip and a
posted one, and it is the piece most likely to be done badly at 1am. This
generates a starting caption from the transcript window that was cut, so the
text and the clip actually agree.

What this deliberately does not do is try to be clever. A caption built from the
spoken words beats a caption invented by a keyword extractor, because the clip
is a fragment of speech and the hook is already in it -- that is why
``plan.score_segment`` selected it. So the generator pulls the strongest opening
sentence it can find and trims it, rather than writing new copy.

Platform limits are enforced as a hard truncation at a word boundary, never a
mid-word cut: a caption ending "...you need to focu" signals automation to
everyone who reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Hard limits per platform. TikTok and Reels technically allow ~2200 characters,
# but a caption past ~150 is not read, so the practical limit is what is used.
LIMITS = {
    "tiktok": 150,
    "reels": 150,
    "shorts": 100,
    "x": 280,
    "default": 150,
}

# filler that weakens an opening when it leads the sentence. Matched on the
# WORD, not on a literal prefix, so "well," and "Well, " both get removed --
# a prefix list containing "well " silently never matched "well, and then...".
FILLER_MULTI = ("you know ", "i mean ", "kind of ", "sort of ")
FILLER_WORDS = {"so", "and", "but", "well", "okay", "ok", "like", "um", "uh",
                "right", "look", "yeah", "now", "alright"}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "that", "this", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "with", "about", "as", "by", "from", "it", "its", "you",
    "your", "we", "our", "they", "their", "i", "my", "me", "he", "she", "his",
    "her", "them", "not", "no", "do", "does", "did", "have", "has", "had", "can",
    "will", "would", "should", "could", "just", "very", "really", "get", "got",
    "one", "two", "out", "up", "down", "what", "who", "how", "why", "when",
    # adverbs and vague nouns make useless hashtags: "#completely" helps nobody
    "completely", "actually", "probably", "basically", "literally", "honestly",
    "something", "everything", "anything", "nothing", "people", "think",
    "know", "want", "need", "going", "gonna", "better", "worse", "first",
    "second", "enough", "little", "much", "many", "more", "most", "some",
    "thing", "things", "way", "back", "good", "bad", "great", "make", "made",
    "take", "took", "come", "came", "work", "works", "time", "times", "lot",
    "money", "start", "starts", "said", "says", "tell", "told", "show", "shows",
}


def strip_filler(text: str) -> str:
    """Remove lead-in words that blunt an opening line."""
    t = text.strip()
    for _ in range(6):                    # bounded, so "and and and" terminates
        low = t.lower()
        matched = False
        for m in FILLER_MULTI:
            if low.startswith(m):
                t = t[len(m):].lstrip(" ,;:-")
                matched = True
                break
        if matched:
            continue
        parts = t.split(None, 1)
        if len(parts) == 2 and parts[0].strip(",;:-.!?").lower() in FILLER_WORDS:
            t = parts[1].lstrip(" ,;:-")
            matched = True
        if not matched:
            break
    if t:
        t = t[0].upper() + t[1:]
    return t


def first_sentence(text: str) -> str:
    m = re.search(r"^(.+?[.!?])(\s|$)", text.strip())
    return m.group(1).strip() if m else text.strip()


def truncate(text: str, limit: int) -> str:
    """Trim to ``limit`` on a word boundary. Never returns a partial word.

    Only steps back when the cut actually lands *inside* a word. Stepping back
    unconditionally drops a whole word when the slice happens to end on a space
    -- "the quick brown" at limit 15 became "the quick", silently losing
    "brown" from every caption of exactly that length.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    limit = max(1, limit)
    cut = text[:limit]
    if limit < len(text) and text[limit] not in " ":
        sp = cut.rfind(" ")
        if sp > 0:
            cut = cut[:sp]
    return cut.rstrip().rstrip(".,;:—-")


def keywords(text: str, n: int = 4) -> list[str]:
    """Frequency, then length, over words that survive the stopword list.

    Crude on purpose, and it shows: without part-of-speech tagging a four-letter
    filter still occasionally picks a filler adjective. The campaign's own
    required hashtags always take priority for exactly this reason.
    """
    words = re.findall(r"[A-Za-z]{5,}", text)
    counts: dict[str, int] = {}
    surface: dict[str, str] = {}
    for w in words:
        low = w.lower()
        if low in STOPWORDS:
            continue
        counts[low] = counts.get(low, 0) + 1
        # keep the original casing so a name like "Bangladesh" survives
        if low not in surface or (w[:1].isupper() and not surface[low][:1].isupper()):
            surface[low] = w
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [surface[w] for w, _ in ranked[:n]]


@dataclass
class Caption:
    hook: str
    tags: list[str] = field(default_factory=list)

    def for_platform(self, platform: str = "default") -> str:
        """Hook plus hashtags, truncated to that platform's limit."""
        limit = LIMITS.get(platform, LIMITS["default"])
        tags = " ".join(f"#{t}" for t in self.tags)
        if not tags:
            return truncate(self.hook, limit)
        # reserve room for the tags so the hook is never the thing that gets cut
        room = max(20, limit - len(tags) - 1)
        return f"{truncate(self.hook, room)} {tags}".strip()


def build(segment_text: str, campaign_tags: list[str] | None = None,
          max_tags: int = 3) -> Caption:
    """Caption from the words actually spoken in the clip."""
    hook = strip_filler(first_sentence(segment_text))
    if not hook:
        hook = strip_filler(segment_text) or "Watch this"

    tags: list[str] = []
    for t in (campaign_tags or []):
        clean = re.sub(r"[^A-Za-z0-9]", "", t)
        if clean and clean.lower() not in [x.lower() for x in tags]:
            tags.append(clean)
    for w in keywords(segment_text, max_tags):
        if w.lower() not in [x.lower() for x in tags]:
            tags.append(w)
    return Caption(hook=hook, tags=tags[:max_tags])


def from_campaign(segment_text: str, campaign_obj) -> Caption:
    """Use the campaign's required hashtags when it has them."""
    required = list(getattr(campaign_obj, "hashtags", []) or [])
    return build(segment_text, required)
