"""Tests for the Corporate Actions Service."""
from datetime import UTC, datetime

import pytest

from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.corporate_actions import AdjustmentFactor, CorporateActionType, CorporateActionsService
from tranalyset.market_data.models import Candle, CorporateAction, Tick, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.market_data.providers import InMemoryHistoricalDataProvider


def _candle(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, volume=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=Timeframe.ONE_MINUTE,
        timestamp=ts,
        open=price,
        high=price + 2,
        low=price - 2,
        close=price + 1,
        volume=volume,
        trades=42,
    )


def _tick(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Tick(symbol=symbol, exchange=exchange, timestamp=ts, price=price, size=size)


def _action(symbol="AAPL", exchange="NASDAQ", action_type="stock_split", effective_date=None, ratio=2.0):
    ts = effective_date or datetime(2024, 1, 15, tzinfo=UTC)
    return CorporateAction(
        symbol=symbol,
        exchange=exchange,
        action_type=action_type,
        effective_date=ts,
        ratio=ratio,
        amount=0.25,
    )


class TestCorporateActionsService:
    @pytest.fixture
    async def service(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        service = CorporateActionsService(repository=repo, event_bus=bus)
        yield service
        await service.stop()
        await bus.stop()

    async def test_register_action_stores_factor(self, service):
        action = _action()
        await service.register_action(action)
        key = "AAPL:NASDAQ"
        assert len(service._factors[key]) == 1
        assert service._factors[key][0].adjustment_factor == 0.5  # 1/2 for 2:1 split

    async def test_supported_action_types(self, service):
        assert len(service.supported_action_types) == 5
        assert "stock_split" in service.supported_action_types
        assert "reverse_split" in service.supported_action_types
        assert "dividend" in service.supported_action_types

    async def test_get_adjusted_candles_applies_factor(self, service):
        await service._repo.append_candle(_candle(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC), price=100.0))
        await service.register_action(_action(effective_date=datetime(2024, 1, 15, tzinfo=UTC), ratio=2.0))

        adjusted = await service.get_adjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert len(adjusted) == 1
        assert adjusted[0].open == 50.0
        assert adjusted[0].high == 51.0
        assert adjusted[0].low == 49.0
        assert adjusted[0].close == 50.5
        assert adjusted[0].volume == 2000.0

    async def test_get_unadjusted_candles_returns_raw(self, service):
        await service._repo.append_candle(_candle(price=100.0))
        await service.register_action(_action())

        unadjusted = await service.get_unadjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert len(unadjusted) == 1
        assert unadjusted[0].open == 100.0

    async def test_get_adjusted_ticks(self, service):
        await service._repo.append_tick(_tick(price=100.0))
        await service.register_action(_action(effective_date=datetime(2024, 1, 15, tzinfo=UTC), ratio=2.0))

        adjusted = await service.get_adjusted_ticks("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 17, 0, tzinfo=UTC))
        assert len(adjusted) == 1
        assert adjusted[0].price == 50.0

    async def test_adjustment_without_actions(self, service):
        await service._repo.append_candle(_candle(price=100.0))
        adjusted = await service.get_adjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert len(adjusted) == 1
        assert adjusted[0].open == 100.0

    async def test_reverse_split_adjustment(self, service):
        await service._repo.append_candle(_candle(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC), price=5.0))
        await service.register_action(_action(action_type="reverse_split", effective_date=datetime(2024, 1, 15, tzinfo=UTC), ratio=0.2))

        adjusted = await service.get_adjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert len(adjusted) == 1
        assert adjusted[0].open == 25.0

    async def test_dividend_does_not_adjust_price(self, service):
        await service._repo.append_candle(_candle(price=100.0))
        await service.register_action(_action(action_type="dividend", effective_date=datetime(2024, 1, 15, tzinfo=UTC), ratio=1.0))

        adjusted = await service.get_adjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert adjusted[0].open == 100.0

    async def test_start_stop_lifecycle(self, service):
        assert service._running is False
        await service.start()
        assert service._running is True
        await service.stop()
        assert service._running is False

    async def test_multiple_actions_cumulative(self, service):
        await service.register_action(_action(effective_date=datetime(2024, 1, 10, tzinfo=UTC), ratio=2.0))
        await service.register_action(_action(effective_date=datetime(2024, 1, 20, tzinfo=UTC), ratio=3.0))

        await service._repo.append_candle(_candle(price=600.0))
        adjusted = await service.get_adjusted_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 31, 9, 0, tzinfo=UTC))
        assert adjusted[0].open == 100.0


class TestAdjustmentFactor:
    async def test_stock_split_factor(self):
        action = _action(action_type="stock_split", ratio=2.0)
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        service = CorporateActionsService(
            repository=repo,
            event_bus=AsyncEventBus(),
        )
        factor = service._compute_adjustment_factor(action)
        assert factor.adjustment_factor == 0.5

    async def test_reverse_split_factor(self):
        action = _action(action_type="reverse_split", ratio=0.5)
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        service = CorporateActionsService(
            repository=repo,
            event_bus=AsyncEventBus(),
        )
        factor = service._compute_adjustment_factor(action)
        assert factor.adjustment_factor == 2.0  # 1/0.5 for 1:2 reverse split
