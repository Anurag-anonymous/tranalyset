"""Data Quality Engine for validating incoming market data."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.market_data.models import Candle, Tick
from tranalyset.market_data.repository import MarketDataRepository

logger = logging.getLogger("tranalyset.market_data.quality")


class IssueSeverity(Enum):
    """Data quality issue severity levels."""
    WARNING = "warning"
    ERROR = "error"


class DataQualityEventType(str):
    """Event types for data quality issues."""
    WARNING = "data.quality.warning"
    ERROR = "data.quality.error"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """A data quality issue detected by the validation engine."""
    severity: IssueSeverity
    issue_type: str
    symbol: str
    exchange: str
    timestamp: datetime
    description: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DataQualityEngine:
    """Infrastructure service for validating incoming market data.

    This service NEVER modifies incoming data. It only reports issues
    through published events.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
        max_price_spike_pct: float = 10.0,
        max_volume_spike_pct: float = 100.0,
        max_timestamp_drift_seconds: float = 5.0,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._max_price_spike_pct = max_price_spike_pct
        self._max_volume_spike_pct = max_volume_spike_pct
        self._max_timestamp_drift = max_timestamp_drift_seconds
        self._lock = asyncio.Lock()
        self._running = False
        self._subscription: Subscription | None = None
        self._last_prices: dict[str, float] = {}
        self._last_volumes: dict[str, float] = {}
        self._last_timestamps: dict[str, datetime] = {}

    @property
    def supported_issue_types(self) -> list[str]:
        return [
            "duplicate_tick",
            "missing_candle",
            "timestamp_anomaly",
            "price_spike",
            "invalid_ohlc",
            "negative_volume",
            "out_of_order_event",
        ]

    async def start(self) -> None:
        """Start the data quality engine."""
        if self._running:
            return
        self._running = True
        logger.info("Data quality engine started.")

    async def stop(self) -> None:
        """Stop the data quality engine."""
        if not self._running:
            return
        self._running = False
        if self._subscription:
            await self._bus.unsubscribe(self._subscription)
        logger.info("Data quality engine stopped.")

    async def validate_tick(self, tick: Tick) -> Sequence[DataQualityIssue]:
        """Validate a tick and return any issues found."""
        issues: list[DataQualityIssue] = []
        key = f"{tick.symbol}:{tick.exchange}"

        # Check negative volume
        if tick.size < 0:
            issues.append(self._create_issue(
                IssueSeverity.ERROR,
                "negative_volume",
                tick.symbol,
                tick.exchange,
                tick.timestamp,
                f"Tick has negative volume: {tick.size}",
            ))

        # Check duplicate tick (same timestamp)
        if key in self._last_timestamps:
            if tick.timestamp == self._last_timestamps[key]:
                issues.append(self._create_issue(
                    IssueSeverity.WARNING,
                    "duplicate_tick",
                    tick.symbol,
                    tick.exchange,
                    tick.timestamp,
                    f"Duplicate tick at {tick.timestamp}",
                ))

        # Check timestamp anomaly
        if key in self._last_timestamps:
            drift = (tick.timestamp - self._last_timestamps[key]).total_seconds()
            if abs(drift) > self._max_timestamp_drift:
                issues.append(self._create_issue(
                    IssueSeverity.WARNING,
                    "timestamp_anomaly",
                    tick.symbol,
                    tick.exchange,
                    tick.timestamp,
                    f"Timestamp drift of {drift:.2f}s exceeds threshold",
                ))

        # Check price spike
        if key in self._last_prices:
            last_price = self._last_prices[key]
            if last_price > 0:
                change_pct = abs(tick.price - last_price) / last_price * 100
                if change_pct > self._max_price_spike_pct:
                    issues.append(self._create_issue(
                        IssueSeverity.WARNING,
                        "price_spike",
                        tick.symbol,
                        tick.exchange,
                        tick.timestamp,
                        f"Price spike of {change_pct:.2f}% detected",
                    ))

        # Check volume spike
        if key in self._last_volumes and tick.size > 0:
            last_volume = self._last_volumes[key]
            if last_volume > 0:
                volume_change_pct = (tick.size - last_volume) / last_volume * 100
                if volume_change_pct > self._max_volume_spike_pct:
                    issues.append(self._create_issue(
                        IssueSeverity.WARNING,
                        "volume_spike",
                        tick.symbol,
                        tick.exchange,
                        tick.timestamp,
                        f"Volume spike of {volume_change_pct:.2f}% detected",
                    ))

        # Update tracking
        self._last_prices[key] = tick.price
        self._last_volumes[key] = tick.size
        self._last_timestamps[key] = tick.timestamp

        return tuple(issues)

    async def validate_candle(self, candle: Candle) -> Sequence[DataQualityIssue]:
        """Validate a candle and return any issues found."""
        issues: list[DataQualityIssue] = []

        # Check negative volume
        if candle.volume < 0:
            issues.append(self._create_issue(
                IssueSeverity.ERROR,
                "negative_volume",
                candle.symbol,
                candle.exchange,
                candle.timestamp,
                f"Candle has negative volume: {candle.volume}",
            ))

        # Check invalid OHLC
        if candle.high < candle.low:
            issues.append(self._create_issue(
                IssueSeverity.ERROR,
                "invalid_ohlc",
                candle.symbol,
                candle.exchange,
                candle.timestamp,
                f"Candle high ({candle.high}) is less than low ({candle.low})",
            ))

        if candle.high < candle.open or candle.high < candle.close:
            issues.append(self._create_issue(
                IssueSeverity.ERROR,
                "invalid_ohlc",
                candle.symbol,
                candle.exchange,
                candle.timestamp,
                f"Candle high ({candle.high}) is less than open ({candle.open}) or close ({candle.close})",
            ))

        if candle.low > candle.open or candle.low > candle.close:
            issues.append(self._create_issue(
                IssueSeverity.ERROR,
                "invalid_ohlc",
                candle.symbol,
                candle.exchange,
                candle.timestamp,
                f"Candle low ({candle.low}) is greater than open ({candle.open}) or close ({candle.close})",
            ))

        # Check for missing candles (gaps)
        await self._check_missing_candles(candle)

        return tuple(issues)

    async def validate_event_order(self, event: EventEnvelope) -> Sequence[DataQualityIssue]:
        """Validate event ordering and return any issues found."""
        issues: list[DataQualityIssue] = []

        # Extract symbol and exchange from payload if available
        payload = event.payload
        symbol = str(payload.get("symbol", "UNKNOWN"))
        exchange = str(payload.get("exchange", "UNKNOWN"))
        timestamp_str = payload.get("timestamp")
        
        if timestamp_str and isinstance(timestamp_str, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                key = f"{symbol}:{exchange}"
                
                if key in self._last_timestamps:
                    if timestamp < self._last_timestamps[key]:
                        issues.append(self._create_issue(
                            IssueSeverity.WARNING,
                            "out_of_order_event",
                            symbol,
                            exchange,
                            timestamp,
                            f"Event timestamp {timestamp} is before previous event {self._last_timestamps[key]}",
                        ))
                
                self._last_timestamps[key] = timestamp
            except (ValueError, TypeError):
                pass

        return tuple(issues)

    async def _check_missing_candles(self, candle: Candle) -> None:
        """Check for gaps in candle data and publish a warning if found."""
        key = f"{candle.symbol}:{candle.exchange}:{candle.timeframe}"
        if key in self._last_timestamps:
            last_ts = self._last_timestamps[key]
            # Simple gap detection: check if timestamp is significantly after expected
            # For 1m candles, expect within 2 minutes; for larger timeframes, proportionally more
            timeframe_seconds = {
                "1m": 60,
                "3m": 180,
                "5m": 300,
                "10m": 600,
                "15m": 900,
                "30m": 1800,
                "1h": 3600,
                "2h": 7200,
                "4h": 14400,
                "1d": 86400,
            }.get(str(candle.timeframe), 60)
            expected_next = last_ts + __import__("datetime").timedelta(seconds=timeframe_seconds)
            if candle.timestamp > expected_next:
                await self._publish_issue(self._create_issue(
                    IssueSeverity.WARNING,
                    "missing_candle",
                    candle.symbol,
                    candle.exchange,
                    candle.timestamp,
                    f"Gap detected: expected candle around {expected_next}, got {candle.timestamp}",
                ))
        self._last_timestamps[key] = candle.timestamp

    def _create_issue(
        self,
        severity: IssueSeverity,
        issue_type: str,
        symbol: str,
        exchange: str,
        timestamp: datetime,
        description: str,
    ) -> DataQualityIssue:
        """Create a data quality issue."""
        return DataQualityIssue(
            severity=severity,
            issue_type=issue_type,
            symbol=symbol,
            exchange=exchange,
            timestamp=timestamp,
            description=description,
        )

    async def _publish_issue(self, issue: DataQualityIssue) -> None:
        """Publish a data quality issue event."""
        event_type = DataQualityEventType.WARNING if issue.severity == IssueSeverity.WARNING else DataQualityEventType.ERROR
        await self._bus.publish(
            EventEnvelope.create(
                source="data_quality",
                event_type=EventType(event_type),
                payload={
                    "severity": issue.severity.value,
                    "issue_type": issue.issue_type,
                    "symbol": issue.symbol,
                    "exchange": issue.exchange,
                    "timestamp": issue.timestamp.isoformat(),
                    "description": issue.description,
                },
            )
        )
        logger.debug("Published %s for %s:%s: %s", event_type, issue.symbol, issue.exchange, issue.description)