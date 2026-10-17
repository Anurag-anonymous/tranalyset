from datetime import UTC, datetime
from pathlib import Path

import pytest

from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.providers import CSVHistoricalDataProvider


@pytest.mark.asyncio
async def test_csv_historical_provider_parses_ist_candles_into_utc(tmp_path: Path) -> None:
    path = tmp_path / "nifty.csv"
    path.write_text(
        "datetime_ist,open,high,low,close,volume\n"
        "2026-04-01 09:15:00,100,105,99,103,0\n"
        "2026-04-01 09:20:00,103,107,102,106,50\n",
        encoding="utf-8",
    )
    provider = CSVHistoricalDataProvider(
        str(path),
        symbol="NIFTY",
        exchange="NSE",
        timeframe=Timeframe.FIVE_MINUTES,
        timezone="Asia/Kolkata",
        timestamp_column="datetime_ist",
    )

    candles = await provider.load_candles(
        "NIFTY",
        "NSE",
        Timeframe.FIVE_MINUTES,
        datetime(2026, 4, 1, 3, 45, tzinfo=UTC),
        datetime(2026, 4, 1, 3, 50, tzinfo=UTC),
    )

    assert len(candles) == 2
    assert candles[0].timestamp == datetime(2026, 4, 1, 3, 45, tzinfo=UTC)
    assert candles[0].timeframe is Timeframe.FIVE_MINUTES
    assert candles[0].volume == 0
    assert candles[1].close == 106


@pytest.mark.asyncio
async def test_csv_historical_provider_rejects_invalid_or_duplicate_rows(tmp_path: Path) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(
        "datetime_ist,open,high,low,close,volume\n"
        "2026-04-01 09:15:00,100,105,99,103,10\n"
        "2026-04-01 09:15:00,103,107,102,106,50\n",
        encoding="utf-8",
    )
    provider = CSVHistoricalDataProvider(
        str(path),
        symbol="NIFTY",
        exchange="NSE",
        timeframe=Timeframe.FIVE_MINUTES,
        timezone="Asia/Kolkata",
        timestamp_column="datetime_ist",
    )

    with pytest.raises(ValueError, match="row 3.*strictly increasing"):
        await provider.load_candles(
            "NIFTY",
            "NSE",
            Timeframe.FIVE_MINUTES,
            datetime.min.replace(tzinfo=UTC),
            datetime.max.replace(tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_csv_historical_provider_rejects_naive_query_ranges(tmp_path: Path) -> None:
    path = tmp_path / "candles.csv"
    path.write_text(
        "datetime_ist,open,high,low,close,volume\n"
        "2026-04-01 09:15:00,100,105,99,103,10\n",
        encoding="utf-8",
    )
    provider = CSVHistoricalDataProvider(
        str(path),
        symbol="NIFTY",
        exchange="NSE",
        timeframe=Timeframe.FIVE_MINUTES,
        timezone="Asia/Kolkata",
        timestamp_column="datetime_ist",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await provider.load_candles(
            "NIFTY",
            "NSE",
            Timeframe.FIVE_MINUTES,
            datetime(2026, 4, 1),
            datetime.max.replace(tzinfo=UTC),
        )
