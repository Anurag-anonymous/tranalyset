"""Corporate Actions Service for maintaining adjusted historical data."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from tranalyset.market_data.models import Candle, CorporateAction, Tick, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription

logger = logging.getLogger("tranalyset.market_data.corporate_actions")


class CorporateActionType(Enum):
    """Types of corporate actions."""
    STOCK_SPLIT = "stock_split"
    REVERSE_SPLIT = "reverse_split"
    DIVIDEND = "dividend"
    BONUS_ISSUE = "bonus_issue"
    RIGHTS_ISSUE = "rights_issue"


@dataclass(frozen=True, slots=True)
class AdjustmentFactor:
    """Adjustment factor for a corporate action."""
    action_type: CorporateActionType
    effective_date: datetime
    split_ratio: float = 1.0  # e.g., 2.0 for 2:1 split
    dividend_amount: float = 0.0
    adjustment_factor: float = 1.0


class CorporateActionsService:
    """Infrastructure service for adjusting historical OHLCV data.

    This service maintains adjusted and unadjusted views of market data
    without mutating the raw data stored in the repository.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._actions: dict[str, list[CorporateAction]] = defaultdict(list)
        self._factors: dict[str, list[AdjustmentFactor]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._running = False
        self._subscription: Subscription | None = None

    @property
    def supported_action_types(self) -> list[str]:
        return [action_type.value for action_type in CorporateActionType]

    async def start(self) -> None:
        """Start the corporate actions service."""
        if self._running:
            return
        self._running = True
        logger.info("Corporate actions service started.")

    async def stop(self) -> None:
        """Stop the corporate actions service."""
        if not self._running:
            return
        self._running = False
        if self._subscription:
            await self._bus.unsubscribe(self._subscription)
        logger.info("Corporate actions service stopped.")

    async def register_action(self, action: CorporateAction) -> None:
        """Register a corporate action and compute adjustment factors."""
        key = f"{action.symbol}:{action.exchange}"
        async with self._lock:
            self._actions[key].append(action)
            factor = self._compute_adjustment_factor(action)
            self._factors[key].append(factor)
            # Sort by effective date
            self._factors[key].sort(key=lambda f: f.effective_date)
        logger.info("Registered %s for %s:%s", action.action_type, action.symbol, action.exchange)

    async def get_adjusted_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Get adjusted OHLCV candles for a symbol/exchange/timeframe."""
        raw_candles = await self._repo.get_candles(symbol, exchange, timeframe, start=start, end=end)
        if not raw_candles:
            return ()

        key = f"{symbol}:{exchange}"
        factors = self._factors.get(key, [])
        if not factors:
            return raw_candles

        # Apply cumulative adjustment factors for historical continuity.
        # All registered factors apply to all candles so the price series
        # remains continuous across corporate actions.
        adjusted = []
        cumulative_factor = 1.0
        for factor in factors:
            cumulative_factor *= factor.adjustment_factor
        for candle in raw_candles:
            adjusted_candle = self._apply_adjustment(candle, cumulative_factor)
            adjusted.append(adjusted_candle)
        return tuple(adjusted)

    async def get_unadjusted_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Get raw, unadjusted OHLCV candles."""
        return await self._repo.get_candles(symbol, exchange, timeframe, start=start, end=end)

    async def get_adjusted_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Tick]:
        """Get adjusted ticks for a symbol/exchange."""
        raw_ticks = await self._repo.get_ticks(symbol, exchange, start=start, end=end)
        if not raw_ticks:
            return ()

        key = f"{symbol}:{exchange}"
        factors = self._factors.get(key, [])
        if not factors:
            return raw_ticks

        cumulative_factor = 1.0
        for factor in factors:
            cumulative_factor *= factor.adjustment_factor
        adjusted = [
            self._apply_tick_adjustment(tick, cumulative_factor) for tick in raw_ticks
        ]
        return tuple(adjusted)

    async def get_unadjusted_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Tick]:
        """Get raw, unadjusted ticks."""
        return await self._repo.get_ticks(symbol, exchange, start=start, end=end)

    def _compute_adjustment_factor(self, action: CorporateAction) -> AdjustmentFactor:
        """Compute the adjustment factor for a corporate action."""
        action_type = CorporateActionType(action.action_type)
        if action_type == CorporateActionType.STOCK_SPLIT:
            ratio = action.ratio if action.ratio > 0 else 1.0
            return AdjustmentFactor(
                action_type=CorporateActionType.STOCK_SPLIT,
                effective_date=action.effective_date,
                split_ratio=ratio,
                adjustment_factor=1.0 / ratio,
            )
        elif action_type == CorporateActionType.REVERSE_SPLIT:
            ratio = action.ratio if action.ratio > 0 else 1.0
            return AdjustmentFactor(
                action_type=CorporateActionType.REVERSE_SPLIT,
                effective_date=action.effective_date,
                split_ratio=ratio,
                adjustment_factor=1.0 / ratio,
            )
        elif action_type == CorporateActionType.DIVIDEND:
            return AdjustmentFactor(
                action_type=CorporateActionType.DIVIDEND,
                effective_date=action.effective_date,
                dividend_amount=action.amount,
                adjustment_factor=1.0,  # Dividends don't affect price adjustment factor
            )
        elif action_type == CorporateActionType.BONUS_ISSUE:
            ratio = action.ratio if action.ratio > 0 else 1.0
            return AdjustmentFactor(
                action_type=CorporateActionType.BONUS_ISSUE,
                effective_date=action.effective_date,
                split_ratio=ratio,
                adjustment_factor=1.0 / (1.0 + ratio),
            )
        elif action_type == CorporateActionType.RIGHTS_ISSUE:
            return AdjustmentFactor(
                action_type=CorporateActionType.RIGHTS_ISSUE,
                effective_date=action.effective_date,
                adjustment_factor=1.0,  # Simplified - would need rights price
            )
        else:
            return AdjustmentFactor(
                action_type=action_type,
                effective_date=action.effective_date,
                adjustment_factor=1.0,
            )

    def _get_cumulative_factor(self, factors: Sequence[AdjustmentFactor], timestamp: datetime) -> float:
        """Compute cumulative adjustment factor for all registered actions."""
        cumulative = 1.0
        for factor in factors:
            cumulative *= factor.adjustment_factor
        return cumulative

    def _apply_adjustment(self, candle: Candle, factor: float) -> Candle:
        """Apply adjustment factor to a candle."""
        return Candle(
            symbol=candle.symbol,
            exchange=candle.exchange,
            timeframe=candle.timeframe,
            timestamp=candle.timestamp,
            open=candle.open * factor,
            high=candle.high * factor,
            low=candle.low * factor,
            close=candle.close * factor,
            volume=candle.volume / factor if factor != 0 else candle.volume,
            trades=candle.trades,
        )

    def _apply_tick_adjustment(self, tick: Tick, factor: float) -> Tick:
        """Apply adjustment factor to a tick."""
        return Tick(
            symbol=tick.symbol,
            exchange=tick.exchange,
            timestamp=tick.timestamp,
            price=tick.price * factor,
            size=tick.size / factor if factor != 0 else tick.size,
            side=tick.side,
        )