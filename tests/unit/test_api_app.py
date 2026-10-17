"""Tests for the FastAPI application."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tranalyset.api.app import WebSocketEventHub
from tranalyset.api.app import DashboardReadModel, create_app
from tranalyset.contracts.events import EventEnvelope, EventType


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()

    assert app.title == "Tranalyset"
    assert app.version == "0.1.0"


def test_health_endpoint() -> None:
    app = create_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_websocket_endpoint_accepts_dashboard_connection() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == {
                "type": "connection-established",
                "data": {"status": "connected"},
            }


def test_dashboard_snapshot_routes_exist_and_start_empty() -> None:
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/api/watchlist").json() == []
        assert client.get("/api/market-regime").json() == []
        assert client.get("/api/trade-health").json() == []
        assert client.get("/api/active-trades").json() == []
        assert client.get("/api/ai-reasoning").json() == []
        assert client.get("/api/trade-journal").json() == []
        assert client.get("/api/candles/AAPL?timeframe=1m").json() == []
        portfolio = client.get("/api/portfolio").json()
        assert portfolio["total_value"] == 100_000.0
        assert portfolio["cash"] == 100_000.0
        assert client.get("/api/runtime").json()["plugin_count"] == 24
        assert len(client.get("/api/engines").json()) == 24
        assert client.get("/api/alerts").json() == []
        assert client.get("/api/replay/status").json()["state"] == "idle"
        assert client.post("/api/replay/start").status_code == 409


def test_configured_dataset_hydrates_dashboard_and_replay_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    path = tmp_path / "nifty.csv"
    path.write_text(
        "datetime_ist,open,high,low,close,volume\n"
        "2026-04-01 09:15:00,100,103,99,102,0\n"
        "2026-04-01 09:20:00,102,104,101,103,0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRANALYSET_CANDLE_CSV", str(path))
    monkeypatch.setenv("TRANALYSET_CANDLE_SYMBOL", "NIFTY")
    monkeypatch.setenv("TRANALYSET_CANDLE_EXCHANGE", "NSE")
    monkeypatch.setenv("TRANALYSET_CANDLE_TIMEFRAME", "5m")
    monkeypatch.setenv("TRANALYSET_CANDLE_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setenv("TRANALYSET_CANDLE_TIMESTAMP_COLUMN", "datetime_ist")

    with TestClient(create_app()) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["dataset"]["candle_count"] == 2
        assert runtime["dataset"]["nonzero_volume_candle_count"] == 0
        assert len(runtime["dataset"]["identity"]) == 64
        assert len(client.get("/api/candles/NIFTY?timeframe=5m").json()) == 2

        step = client.post("/api/replay/step")
        assert step.status_code == 200
        assert step.json()["progress"] == 0.5
        assert step.json()["started"] is True
        assert client.get("/api/watchlist").json()[0]["symbol"] == "NIFTY"
        assert len(client.get("/api/candles/NIFTY?timeframe=5m").json()) == 2
        assert client.app.state.kernel.event_bus.metrics().critical_dead_lettered == 0
        assert client.post("/api/replay/start").status_code == 200


async def test_websocket_event_hub_broadcasts_frontend_event_envelope() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

    hub = WebSocketEventHub()
    client = FakeWebSocket()
    hub._clients.add(client)

    await hub.publish(
        EventEnvelope.create(
            source="market-state-engine",
            event_type=EventType("MarketRegimeChanged"),
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "regime": "Bull Trend",
                "confidence": 0.8,
                "occurred_at": "2026-10-17T10:00:00+00:00",
            },
        )
    )

    assert client.messages == [
        {
            "type": "market-regime-changed",
            "data": {
                "symbol": "AAPL",
                "regime": "Bull Trend",
                "confidence": 0.8,
                "timestamp": "2026-10-17T10:00:00+00:00",
            },
        }
    ]


async def test_dashboard_read_model_adapts_real_backend_payload_shapes() -> None:
    read_model = DashboardReadModel()
    event = EventEnvelope.create(
        source="trade-health-engine",
        event_type=EventType("TradeHealthUpdated"),
        payload={
            "trade_id": "trade-1",
            "symbol": "AAPL",
            "health_score": 75.0,
            "confidence_lower": 0.6,
            "confidence_upper": 0.9,
            "factors": {"trend": 0.8},
            "occurred_at": "2026-10-17T10:00:00+00:00",
        },
    )

    message = await read_model.consume(event)

    assert message == {
        "type": "trade-health-updated",
        "data": {
            "symbol": "AAPL",
            "health_score": 75.0,
            "confidence": [0.6, 0.9],
            "factors": {"trend": 0.8},
            "timestamp": "2026-10-17T10:00:00+00:00",
        },
    }
    assert await read_model.snapshot("trade-health") == [message["data"]]


async def test_dashboard_read_model_uses_paper_execution_and_portfolio_events() -> None:
    read_model = DashboardReadModel()
    opened = EventEnvelope.create(
        source="paper-trading-engine",
        event_type=EventType("PaperTradeUpdated"),
        payload={
            "trade_id": "paper-1",
            "symbol": "AAPL",
            "status": "opened",
            "quantity": 4.0,
            "average_price": 100.0,
            "last_price": 102.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 8.0,
            "commissions": 0.4,
            "slippage": 0.2,
            "occurred_at": "2026-10-17T10:00:00+00:00",
        },
    )
    portfolio = EventEnvelope.create(
        source="paper-trading-engine",
        event_type=EventType("PaperPortfolioUpdated"),
        payload={
            "cash": 9_599.6,
            "equity": 10_007.6,
            "virtual_positions": [
                {"symbol": "AAPL", "quantity": 4.0},
                {"symbol": "AAPL", "quantity": 2.0},
            ],
            "occurred_at": "2026-10-17T10:00:00+00:00",
        },
    )
    performance = EventEnvelope.create(
        source="paper-trading-engine",
        event_type=EventType("PaperPerformanceReported"),
        payload={
            "period": "daily",
            "metrics": {
                "equity": 10_007.6,
                "pnl": 7.6,
                "return": 0.00076,
                "drawdown": 0.01,
                "trade_count": 1.0,
            },
            "occurred_at": "2026-10-17T10:00:00+00:00",
        },
    )

    assert (await read_model.consume(opened))["type"] == "trade-updated"
    await read_model.consume(portfolio)
    await read_model.consume(performance)

    active_trades = await read_model.snapshot("active-trades")
    assert active_trades[0]["entry_price"] == 100.0
    assert active_trades[0]["unrealized_pnl"] == 8.0
    assert await read_model.snapshot("portfolio") == {
        "total_value": 10_007.6,
        "cash": 9_599.6,
        "positions": {"AAPL": 6.0},
        "timestamp": "2026-10-17T10:00:00+00:00",
    }
    assert await read_model.snapshot("performance") == {
        "period": "daily",
        "equity": 10_007.6,
        "pnl": 7.6,
        "total_return": 0.076,
        "drawdown": 1.0,
        "trade_count": 1.0,
        "timestamp": "2026-10-17T10:00:00+00:00",
    }


async def test_dashboard_read_model_journals_closed_paper_trade_once() -> None:
    read_model = DashboardReadModel()
    closed = EventEnvelope.create(
        source="paper-trading-engine",
        event_type=EventType("PaperTradeClosed"),
        payload={
            "trade_id": "paper-1",
            "symbol": "AAPL",
            "quantity": 4.0,
            "average_price": 100.0,
            "exit_price": 105.0,
            "realized_pnl": 18.0,
            "commissions": 0.8,
            "slippage": 1.2,
            "occurred_at": "2026-10-17T11:00:00+00:00",
        },
    )

    message = await read_model.consume(closed)

    assert message is not None
    assert message["type"] == "trade-closed"
    assert message["data"]["pnl"] == 18.0
    assert message["data"]["commissions"] == 0.8
    assert message["data"]["slippage"] == 1.2
    assert await read_model.consume(closed) is None
    assert await read_model.snapshot("trade-journal") == [message["data"]]


async def test_dashboard_read_model_discards_invalid_market_values() -> None:
    read_model = DashboardReadModel()
    invalid_candle = EventEnvelope.create(
        source="market-data",
        event_type=EventType("CandleClosed"),
        payload={
            "symbol": "AAPL",
            "timeframe": "1m",
            "open_price": 100.0,
            "high_price": 99.0,
            "low_price": 98.0,
            "close_price": 99.5,
            "volume": 10.0,
            "closed_at": "2026-10-17T10:00:00+00:00",
        },
    )
    invalid_regime = EventEnvelope.create(
        source="market-state-engine",
        event_type=EventType("MarketRegimeChanged"),
        payload={"symbol": "AAPL", "regime": "Bull Trend", "confidence": 1.5},
    )

    assert await read_model.consume(invalid_candle) is None
    assert await read_model.consume(invalid_regime) is None
    assert await read_model.snapshot("candles", symbol="AAPL") == []
    assert await read_model.snapshot("market-regime") == []


async def test_dashboard_read_model_emits_candle_and_watchlist_updates() -> None:
    read_model = DashboardReadModel()
    first = EventEnvelope.create(
        source="replay",
        event_type=EventType("CandleClosed"),
        timestamp=datetime(2026, 10, 17, 10, 5, tzinfo=UTC),
        payload={
            "symbol": "NIFTY",
            "timeframe": "5m",
            "open_price": 100,
            "high_price": 103,
            "low_price": 99,
            "close_price": 102,
            "volume": 0,
            "closed_at": "2026-10-17T10:05:00+00:00",
        },
    )
    second = EventEnvelope.create(
        source="replay",
        event_type=EventType("CandleClosed"),
        timestamp=datetime(2026, 10, 17, 10, 10, tzinfo=UTC),
        payload={
            "symbol": "NIFTY",
            "timeframe": "5m",
            "open_price": 102,
            "high_price": 104,
            "low_price": 101,
            "close_price": 103,
            "volume": 0,
            "closed_at": "2026-10-17T10:10:00+00:00",
        },
    )

    await read_model.consume(first)
    messages = await read_model.consume(second)

    assert isinstance(messages, tuple)
    assert [message["type"] for message in messages] == [
        "candle-updated",
        "watchlist-updated",
    ]
    assert messages[1]["data"]["price"] == 103
    assert messages[1]["data"]["change"] == pytest.approx((103 - 102) / 102 * 100)
