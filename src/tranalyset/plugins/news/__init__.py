"""News plugin."""

from tranalyset.plugins.news.contracts import (
    MARKET_SENTIMENT_CHANGED,
    NEWS_ANALYZED,
    NEWS_RECEIVED,
    MarketSentimentChangedPayload,
    NewsAnalyzedPayload,
    NewsReceivedPayload,
)
from tranalyset.plugins.news.plugin import NewsPlugin, create_plugin

__all__ = [
    "MARKET_SENTIMENT_CHANGED",
    "NEWS_ANALYZED",
    "NEWS_RECEIVED",
    "MarketSentimentChangedPayload",
    "NewsAnalyzedPayload",
    "NewsPlugin",
    "NewsReceivedPayload",
    "create_plugin",
]
