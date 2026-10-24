"""Draw the chart the bot is looking at.

Plain SVG, no dependencies, no CDN. The point is that the machine's opinion is
inspectable: every line the reader computed is drawn, and the decision is written
underneath in words. If the bot buys and the picture does not show why, the bot is
wrong or the code is.
"""

from __future__ import annotations

from .data import Candle
from .reader import ChartRead, ema
from .signals import Signal

W, H = 900, 420
PAD_L, PAD_R, PAD_T, PAD_B = 8, 78, 12, 54


def _scale(vals: list[float], lo: float, hi: float) -> list[float]:
    span = (hi - lo) or 1.0
    return [(v - lo) / span for v in vals]


def render_svg(candles: list[Candle], read: ChartRead | None = None,
               signal: Signal | None = None, title: str = "",
               width: int = W, height: int = H, show_ema: bool = True,
               watermark: str = "") -> str:
    n = len(candles)
    if n == 0:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"

    plot_w = width - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B

    closed = [c.close for c in candles]
    ef = ema(closed, 12) if show_ema else [c.close for c in candles]
    es = ema(closed, 26) if show_ema else [c.close for c in candles]

    lo = min(min(c.low for c in candles), min(ef), min(es))
    hi = max(max(c.high for c in candles), max(ef), max(es))
    pad = (hi - lo) * 0.06 or 1.0
    lo, hi = lo - pad, hi + pad

    def X(i: int) -> float:
        return PAD_L + (i + 0.5) * plot_w / n

    def Y(v: float) -> float:
        return PAD_T + (1.0 - (v - lo) / ((hi - lo) or 1.0)) * plot_h

    bw = max(1.5, plot_w / n * 0.62)
    up, dn = "#26a269", "#c01c28"
    parts: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}' font-family='ui-monospace,monospace'>",
        f"<rect width='{width}' height='{height}' fill='#14161a'/>",
    ]

    # grid + price axis
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        parts.append(f"<line x1='{PAD_L}' y1='{y:.1f}' x2='{PAD_L+plot_w}' y2='{y:.1f}' "
                     f"stroke='#262b33' stroke-width='1'/>")
        parts.append(f"<text x='{PAD_L+plot_w+6}' y='{y+4:.1f}' fill='#7d8796' "
                     f"font-size='10'>{v:,.0f}</text>")

    # support / resistance
    if read is not None:
        for level, col, label in (
            (read.resistance, "#c01c28", "R"),
            (read.support, "#26a269", "S"),
        ):
            if lo <= level <= hi:
                y = Y(level)
                parts.append(f"<line x1='{PAD_L}' y1='{y:.1f}' x2='{PAD_L+plot_w}' "
                             f"y2='{y:.1f}' stroke='{col}' stroke-width='1' "
                             f"stroke-dasharray='5 4' opacity='0.55'/>")
                parts.append(f"<text x='{PAD_L+3}' y='{y-3:.1f}' fill='{col}' "
                             f"font-size='9'>{label}</text>")

    # candles
    for i, c in enumerate(candles):
        col = up if c.bullish else dn
        x = X(i)
        parts.append(f"<line x1='{x:.1f}' y1='{Y(c.high):.1f}' x2='{x:.1f}' "
                     f"y2='{Y(c.low):.1f}' stroke='{col}' stroke-width='1'/>")
        y1, y2 = Y(max(c.open, c.close)), Y(min(c.open, c.close))
        parts.append(f"<rect x='{x-bw/2:.1f}' y='{y1:.1f}' width='{bw:.1f}' "
                     f"height='{max(1.0, y2-y1):.1f}' fill='{col}'/>")

    # EMA lines
    if show_ema:
        for series, col in ((ef, "#4a9eff"), (es, "#e0a030")):
            pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(series))
            parts.append(f"<polyline points='{pts}' fill='none' stroke='{col}' "
                         f"stroke-width='1.4' opacity='0.9'/>")

    # trade annotations
    if signal is not None and signal.tradeable:
        for val, col, lab in ((signal.stop, dn, "stop"), (signal.target, up, "target")):
            if lo <= val <= hi:
                y = Y(val)
                parts.append(f"<line x1='{PAD_L+plot_w*0.55:.1f}' y1='{y:.1f}' "
                             f"x2='{PAD_L+plot_w}' y2='{y:.1f}' stroke='{col}' "
                             f"stroke-width='1.2' stroke-dasharray='2 3'/>")
                parts.append(f"<text x='{PAD_L+plot_w*0.55:.1f}' y='{y-3:.1f}' "
                             f"fill='{col}' font-size='9'>{lab}</text>")

    # Unmistakable banner when the data is not real. A chart that looks like
    # BTC but is generated must say so, or it is lying.
    if watermark:
        parts.append(
            f"<rect x='0' y='{height/2-34:.0f}' width='{width}' height='68' "
            f"fill='#8a1c1c' opacity='0.30'/>"
        )
        parts.append(
            f"<text x='{width/2:.0f}' y='{height/2+8:.0f}' fill='#ff8f8f' "
            f"font-size='22' font-weight='bold' text-anchor='middle' "
            f"letter-spacing='2'>{_esc(watermark)}</text>"
        )

    # title + narration
    if title:
        parts.append(f"<text x='{PAD_L}' y='{PAD_T-1}' fill='#c8d0dc' font-size='11'>"
                     f"{_esc(title)}</text>")
    if read is not None:
        parts.append(f"<text x='{PAD_L}' y='{height-34}' fill='#9aa6b6' font-size='10'>"
                     f"{_esc(read.narrate())}</text>")
    if signal is not None:
        col = up if signal.action == "buy" else dn if signal.action == "sell" else "#9aa6b6"
        parts.append(f"<text x='{PAD_L}' y='{height-18}' fill='{col}' font-size='11'>"
                     f"{_esc(signal.describe())}</text>")

    parts.append("</svg>")
    return "".join(parts)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
