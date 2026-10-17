"""Tests for the Historical Data Provider abstraction."""
from datetime import UTC, datetime

import pytest

from tranalyset.market_data.models import (
    Candle,
    CorporateAction,
    InstrumentMetadata,
    MarketCalendar,
    MarketSession,
    Tick,
    Timeframe,
    TradingSession,
)
from tranalyset.market_data.providers import (
    CSVHistoricalDataProvider,
    InMemoryHistoricalDataProvider,
    HistoricalDataProvider,
)


def _tick(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Tick(symbol=symbol, exchange=exchange, timestamp=ts, price=price, size=size)


def _candle(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=ts,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1000.0,
        trades=42,
    )


def _instrument(symbol="AAPL", exchange="NASDAQ"):
    return InstrumentMetadata(
        symbol=symbol,
        exchange=exchange,
        currency="USD",
        timezone="America/New_York",
    )


def _session(exchange="NASDAQ"):
    return MarketCalendar(
        exchange=exchange,
        timezone="America/New_York",
        sessions=[
            TradingSession(
                exchange=exchange,
                session_type=MarketSession.REGULAR,
                start=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
                end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
            )
        ],
    )


def _action(symbol="AAPL", exchange="NASDAQ"):
    return CorporateAction(
        symbol=symbol,
        exchange=exchange,
        action_type="dividend",
        effective_date=datetime(2024, 1, 15, tzinfo=UTC),
        amount=0.25,
    )


class TestInMemoryHistoricalDataProvider:
    @pytest.fixture
    def provider(self):
        return InMemoryHistoricalDataProvider()

    async def test_load_ticks_filters_by_symbol_exchange_and_time(self, provider):
        provider.add_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        provider.add_tick(_tick(symbol="MSFT", exchange="NYSE", timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))
        provider.add_tick(_tick(timestamp=datetime(2024, 1, 1, 10, 30, tzinfo=UTC)))

        result = await provider.load_ticks("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
        assert len(result) == 1
        assert result[0].symbol == "AAPL"

    async def test_load_candles_filters_by_timeframe(self, provider):
        provider.add_candle(_candle(timeframe=Timeframe.ONE_MINUTE))
        provider.add_candle(_candle(timeframe=Timeframe.FIVE_MINUTES))

        m1 = await provider.load_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 2, 9, 0, tzinfo=UTC))
        m5 = await provider.load_candles("AAPL", "NASDAQ", Timeframe.FIVE_MINUTES, datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 2, 9, 0, tzinfo=UTC))
        assert len(m1) == 1
        assert len(m5) == 1

    async def test_load_sessions_by_exchange(self, provider):
        provider.add_session(_session(exchange="NASDAQ"))
        provider.add_session(_session(exchange="NYSE"))

        result = await provider.load_sessions("NASDAQ", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC))
        assert len(result) == 1
        assert result[0].exchange == "NASDAQ"

    async def test_load_metadata_filters(self, provider):
        provider.add_metadata(_instrument(symbol="AAPL", exchange="NASDAQ"))
        provider.add_metadata(_instrument(symbol="MSFT", exchange="NYSE"))

        result = await provider.load_metadata(symbol="AAPL")
        assert len(result) == 1
        assert result[0].symbol == "AAPL"

        result = await provider.load_metadata(exchange="NYSE")
        assert len(result) == 1
        assert result[0].exchange == "NYSE"

    async def test_load_corporate_actions_filters(self, provider):
        provider.add_corporate_action(_action())
        provider.add_corporate_action(_action(symbol="MSFT"))

        result = await provider.load_corporate_actions("AAPL", "NASDAQ", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC))
        assert len(result) == 1
        assert result[0].action_type == "dividend"

    async def test_load_returns_empty_tuples_when_no_data(self, provider):
        result = await provider.load_ticks("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 1, 17, 0, tzinfo=UTC))
        assert result == ()

    async def test_returns_are_immutable(self, provider):
        provider.add_tick(_tick())
        result = await provider.load_ticks("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 1, 17, 0, tzinfo=UTC))
        assert isinstance(result, tuple)


class TestCSVHistoricalDataProvider:
    async def test_candle_provider_returns_no_tick_data(self, tmp_path):
        provider = CSVHistoricalDataProvider(
            base_path=str(tmp_path / "candles.csv"),
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe=Timeframe.ONE_MINUTE,
        )
        assert await provider.load_ticks(
            "AAPL",
            "NASDAQ",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        ) == ()


class TestHistoricalDataProviderContract:
    async def test_provider_does_not_know_about_plugins(self):
        """Providers must not import or reference plugin types."""
        import tranalyset.market_data.providers as providers
        source = providers.__file__
        with open(source) as f:
            content = f.read()
        assert "Plugin" not in content or "plugins" not in content.split("Plugin")[0].split("\n")[-1]

    async def test_provider_returns_domain_models(self):
        provider = InMemoryHistoricalDataProvider()
        provider.add_tick(_tick())
        ticks = await provider.load_ticks("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 0, tzinfo=UTC), datetime(2024, 1, 1, 17, 0, tzinfo=UTC))
        assert all(isinstance(t, Tick) for t in ticks)