"""Validated persistence boundary for canonical market-data events."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope
from tranalyset.market_data.models import Candle, Tick, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED


class MarketDataIngestionService:
    """Persists canonical source market data before it is published downstream."""

    def __init__(self, repository: MarketDataRepository) -> None:
        self._repository = repository

    async def persist_event(self, event: EventEnvelope) -> bool:
        if event.event_type == TICK_RECEIVED:
            return await self.persist_tick_payload(event.payload)
        if event.event_type == CANDLE_CLOSED:
            return await self.persist_candle_payload(event.payload)
        return False

    async def persist_tick_payload(self, payload: Mapping[str, Any]) -> bool:
        tick = Tick(
            symbol=_required_text(payload, "symbol"),
            exchange=_required_text(payload, "venue"),
            timestamp=_required_timestamp(payload, "received_at"),
            price=_required_number(payload, "price"),
            size=_required_number(payload, "size"),
            side=_optional_text(payload, "side", default="unknown"),
        )
        return await self._repository.append_tick(tick)

    async def persist_candle_payload(self, payload: Mapping[str, Any]) -> bool:
        opened_at = _required_timestamp(payload, "opened_at")
        closed_at = _required_timestamp(payload, "closed_at")
        if closed_at <= opened_at:
            raise ValueError("closed_at must be after opened_at.")
        candle = Candle(
            symbol=_required_text(payload, "symbol"),
            exchange=_required_text(payload, "venue"),
            timeframe=Timeframe(_required_text(payload, "timeframe")),
            timestamp=opened_at,
            open=_required_number(payload, "open_price"),
            high=_required_number(payload, "high_price"),
            low=_required_number(payload, "low_price"),
            close=_required_number(payload, "close_price"),
            volume=_required_number(payload, "volume"),
        )
        return await self._repository.append_candle(candle)


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _optional_text(payload: Mapping[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else default


def _required_number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    return float(value)


def _required_timestamp(payload: Mapping[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a timezone-aware ISO timestamp.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{key} must be timezone-aware.")
    return timestamp.astimezone(UTC)
