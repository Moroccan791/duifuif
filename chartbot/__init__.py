"""A bot that reads charts and places trades.

Binance for execution, optional TradingView webhooks for alerts. The chart
reading is numeric (`chartbot.reader`), and the rendered SVG exists so you can
check the machine's opinion against your own eyes.
"""

from .data import BinanceClient, Candle, synthetic_candles, trending_candles
from .reader import ChartRead, read_chart
from .signals import Signal, SignalConfig, evaluate

__all__ = [
    "BinanceClient", "Candle", "synthetic_candles", "trending_candles",
    "ChartRead", "read_chart", "Signal", "SignalConfig", "evaluate",
]
