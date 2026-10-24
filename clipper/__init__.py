"""Whop clipping pipeline: campaign economics, clip planning, rendering.

Three separable pieces, in the order they matter:

1. ``campaigns`` -- which campaign is worth your hours. This dominates
   everything else: a 5x better campaign is worth more than 5x faster editing.
2. ``plan``     -- which 30 seconds to cut, from a transcript.
3. ``render``   -- cut, reframe to 9:16, burn captions. Deterministic.

What this package deliberately does NOT do is post to platforms or submit to
Whop. See CLIPPER.md for why.
"""

__all__ = ["campaigns", "plan", "render"]
