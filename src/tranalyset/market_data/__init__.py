"""Market Data Repository - single source of truth for market data.

This bounded context provides a thread-safe, in-memory repository for market data
that is exposed through the kernel's dependency injection system.

It is NOT a plugin. It is shared infrastructure.
"""

from tranalyset.market_data.models import (
    Candle,
    CorporateAction,
    InstrumentMetadata,
    MarketSession,
    Tick,
    Timeframe,
)
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache, SUPPORTED_INDICATORS as INDICATOR_LIST
from tranalyset.market_data.ingestion import MarketDataIngestionService
from tranalyset.market_data.providers import (
    CSVHistoricalDataProvider,
    HistoricalDataProvider,
    InMemoryHistoricalDataProvider,
)
from tranalyset.market_data.repository import (
    InMemoryStorageBackend,
    MarketDataRepository,
    RetentionPolicy,
    StorageBackend,
)
from tranalyset.market_data.calendar import MarketCalendarService, SessionWindow
from tranalyset.market_data.corporate_actions import CorporateActionsService
from tranalyset.market_data.quality import DataQualityEngine
from tranalyset.market_data.replay import (
    HistoricalReplayResult,
    HistoricalTradingSessionReplay,
    PlaybackMode,
    RecordedReplayEvent,
    ReplayEngine,
    ReplayState,
)

__all__ = [
    "Candle",
    "CorporateAction",
    "CorporateActionsService",
    "DataQualityEngine",
    "CSVHistoricalDataProvider",
    "INDICATOR_LIST",
    "IndicatorCache",
    "InMemoryHistoricalDataProvider",
    "InstrumentMetadata",
    "InMemoryStorageBackend",
    "MarketCalendar",
    "MarketDataRepository",
    "MarketDataIngestionService",
    "MarketSession",
    "PlaybackMode",
    "HistoricalReplayResult",
    "HistoricalTradingSessionReplay",
    "MarketCalendarService",
    "ReplayEngine",
    "RecordedReplayEvent",
    "ReplayState",
    "RetentionPolicy",
    "StorageBackend",
    "SessionWindow",
    "Tick",
    "Timeframe",
    "TimeframeAggregationEngine",
    "TradingSession",
]
