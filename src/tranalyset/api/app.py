import asyncio
import hashlib
import math
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.kernel import Microkernel
from tranalyset.api.runtime import RuntimeSettings, configure_replay, create_production_kernel
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.providers import CSVHistoricalDataProvider
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.market_data.replay import ReplayEngine, ReplayState


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported WebSocket event payload value: {type(value).__name__}")


class WebSocketSender(Protocol):
    async def send_json(self, data: Any) -> None: ...


class DashboardReadModel:
    def __init__(self, *, history_limit: int = 1_000) -> None:
        self._history_limit = history_limit
        self._lock = asyncio.Lock()
        self._watchlist: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._previous_prices: dict[str, float] = {}
        self._candles: OrderedDict[tuple[str, str], deque[dict[str, Any]]] = OrderedDict()
        self._regimes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._trade_health: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._active_trades: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._closed_trade_ids: OrderedDict[str, None] = OrderedDict()
        self._reasoning: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._news: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._alerts: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._trade_journal: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._portfolio: dict[str, Any] | None = None
        self._performance: dict[str, Any] | None = None

    async def consume(
        self,
        event: EventEnvelope,
    ) -> dict[str, Any] | tuple[dict[str, Any], ...] | None:
        event_type = str(event.event_type)
        payload = _json_safe(event.payload)
        if not isinstance(payload, dict):
            return None
        timestamp = _first_text(
            payload,
            "occurred_at",
            "timestamp",
            "closed_at",
            "generated_at",
            "analyzed_at",
            "received_at",
            default=event.timestamp.isoformat(),
        )
        message: dict[str, Any] | None = None

        async with self._lock:
            if event_type == "TickReceived":
                symbol = _first_text(payload, "symbol")
                price = _number(payload.get("price"))
                size = _number(payload.get("size"))
                if symbol and price > 0 and size >= 0:
                    previous = self._previous_prices.get(symbol, price)
                    item = {
                        "symbol": symbol,
                        "price": price,
                        "change": ((price - previous) / previous * 100.0) if previous else 0.0,
                        "volume": size,
                        "timestamp": timestamp,
                    }
                    self._previous_prices[symbol] = price
                    self._put(self._watchlist, symbol, item)
                    message = {"type": "watchlist-updated", "data": item}
            elif event_type in {"CandleClosed", "TimeframeCandleClosed"}:
                symbol = _first_text(payload, "symbol")
                timeframe = _first_text(payload, "timeframe")
                candle_values = (
                    payload.get("open_price", payload.get("open")),
                    payload.get("high_price", payload.get("high")),
                    payload.get("low_price", payload.get("low")),
                    payload.get("close_price", payload.get("close")),
                    payload.get("volume"),
                )
                if symbol and timeframe and all(_is_number(value) for value in candle_values):
                    open_price, high_price, low_price, close_price, volume = (
                        float(value) for value in candle_values
                    )
                    if (
                        min(open_price, high_price, low_price, close_price) <= 0
                        or volume < 0
                        or high_price < max(open_price, low_price, close_price)
                        or low_price > min(open_price, high_price, close_price)
                    ):
                        return None
                    candle = {
                        "time": timestamp,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                    }
                    key = (symbol, timeframe)
                    history = self._candles.setdefault(key, deque(maxlen=self._history_limit))
                    matching_index = next(
                        (index for index, value in enumerate(history) if value["time"] == timestamp),
                        None,
                    )
                    if matching_index is not None:
                        history[matching_index] = candle
                    else:
                        history.append(candle)
                    self._candles.move_to_end(key)
                    self._trim(self._candles)
                    previous = self._previous_prices.get(symbol, close_price)
                    watch_item = {
                        "symbol": symbol,
                        "price": close_price,
                        "change": (
                            (close_price - previous) / previous * 100.0 if previous else 0.0
                        ),
                        "volume": volume,
                        "timestamp": timestamp,
                    }
                    self._previous_prices[symbol] = close_price
                    self._put(self._watchlist, symbol, watch_item)
                    message = {
                        "type": "candle-updated",
                        "data": {"symbol": symbol, "timeframe": timeframe, "candle": candle},
                    }
                    messages = (
                        message,
                        {"type": "watchlist-updated", "data": watch_item},
                    )
            elif event_type == "MarketRegimeChanged":
                symbol = _first_text(payload, "symbol")
                confidence = payload.get("confidence")
                if symbol and _is_number(confidence) and 0 <= confidence <= 1:
                    item = {
                        "symbol": symbol,
                        "regime": _first_text(payload, "regime", default="Unknown"),
                        "confidence": float(confidence),
                        "timestamp": timestamp,
                    }
                    self._put(self._regimes, symbol, item)
                    message = {"type": "market-regime-changed", "data": item}
            elif event_type == "TradeHealthUpdated":
                symbol = _first_text(payload, "symbol")
                health_values = (
                    payload.get("health_score"),
                    payload.get("confidence_lower"),
                    payload.get("confidence_upper"),
                )
                if symbol and all(_is_number(value) for value in health_values):
                    health_score, confidence_lower, confidence_upper = (
                        float(value) for value in health_values
                    )
                    if (
                        not 0 <= health_score <= 100
                        or not 0 <= confidence_lower <= confidence_upper <= 100
                    ):
                        return None
                    item = {
                        "symbol": symbol,
                        "health_score": health_score,
                        "confidence": [confidence_lower, confidence_upper],
                        "factors": _numeric_mapping(payload.get("factors")),
                        "timestamp": timestamp,
                    }
                    self._put(self._trade_health, symbol, item)
                    message = {"type": "trade-health-updated", "data": item}
            elif event_type == "TradeOpened":
                trade_id = _first_text(payload, "trade_id")
                if trade_id and trade_id not in self._closed_trade_ids:
                    item = {
                        "trade_id": trade_id,
                        "symbol": _first_text(payload, "symbol"),
                        "quantity": _number(payload.get("quantity")),
                        "entry_price": _number(payload.get("entry_price")),
                        "timestamp": timestamp,
                    }
                    self._put(self._active_trades, trade_id, item)
                    message = {"type": "trade-opened", "data": item}
            elif event_type == "TradeClosed":
                trade_id = _first_text(payload, "trade_id")
                existing = self._active_trades.pop(trade_id, None) if trade_id else None
                if existing is not None and trade_id not in self._closed_trade_ids:
                    self._remember_closed_trade(trade_id)
                    item = {
                        **existing,
                        "exit_price": _number(payload.get("exit_price")),
                        "timestamp": timestamp,
                    }
                    if isinstance(payload.get("realized_pnl"), int | float):
                        item["pnl"] = _number(payload.get("realized_pnl"))
                    elif isinstance(payload.get("pnl"), int | float):
                        item["pnl"] = _number(payload.get("pnl"))
                    self._trade_journal.appendleft(item)
                    message = {"type": "trade-closed", "data": item}
            elif event_type == "PaperTradeUpdated":
                trade_id = _first_text(payload, "trade_id")
                symbol = _first_text(payload, "symbol")
                if trade_id and trade_id not in self._closed_trade_ids and symbol and _has_numbers(
                    payload,
                    (
                        "quantity",
                        "average_price",
                        "last_price",
                        "realized_pnl",
                        "unrealized_pnl",
                        "commissions",
                        "slippage",
                    ),
                ):
                    item = {
                        **self._active_trades.get(trade_id, {}),
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "quantity": _number(payload.get("quantity")),
                        "entry_price": _number(payload.get("average_price")),
                        "last_price": _number(payload.get("last_price")),
                        "realized_pnl": _number(payload.get("realized_pnl")),
                        "unrealized_pnl": _number(payload.get("unrealized_pnl")),
                        "commissions": _number(payload.get("commissions")),
                        "slippage": _number(payload.get("slippage")),
                        "status": _first_text(payload, "status"),
                        "timestamp": timestamp,
                    }
                    self._put(self._active_trades, trade_id, item)
                    message = {"type": "trade-updated", "data": item}
            elif event_type == "PaperTradeClosed":
                trade_id = _first_text(payload, "trade_id")
                if trade_id and trade_id not in self._closed_trade_ids:
                    self._remember_closed_trade(trade_id)
                    existing = self._active_trades.pop(trade_id, {})
                    item = {
                        **existing,
                        "trade_id": trade_id,
                        "symbol": _first_text(payload, "symbol"),
                        "quantity": _number(payload.get("quantity")),
                        "entry_price": _number(payload.get("average_price")),
                        "exit_price": _number(payload.get("exit_price")),
                        "pnl": _number(payload.get("realized_pnl")),
                        "commissions": _number(payload.get("commissions")),
                        "slippage": _number(payload.get("slippage")),
                        "timestamp": timestamp,
                    }
                    self._trade_journal.appendleft(item)
                    message = {"type": "trade-closed", "data": item}
            elif event_type in {"PortfolioUpdated", "PaperPortfolioUpdated"}:
                paper_portfolio = event_type == "PaperPortfolioUpdated"
                positions = payload.get("virtual_positions" if paper_portfolio else "open_positions", ())
                capital_key = "equity" if paper_portfolio else "capital"
                if _has_numbers(payload, (capital_key, "cash")):
                    position_map: dict[str, float] = {}
                    if isinstance(positions, list):
                        for position in positions:
                            if (
                                isinstance(position, dict)
                                and _first_text(position, "symbol")
                                and _is_number(position.get("quantity"))
                            ):
                                symbol = _first_text(position, "symbol")
                                position_map[symbol] = (
                                    position_map.get(symbol, 0.0)
                                    + _number(position.get("quantity"))
                                )
                    self._portfolio = {
                        "total_value": _number(payload.get(capital_key)),
                        "cash": _number(payload.get("cash")),
                        "positions": position_map,
                        "timestamp": timestamp,
                    }
                    message = {"type": "portfolio-updated", "data": self._portfolio}
            elif event_type == "PaperPerformanceReported":
                metrics = payload.get("metrics")
                period = _first_text(payload, "period")
                if (
                    isinstance(metrics, dict)
                    and period
                    and _has_numbers(metrics, ("equity", "pnl", "return", "drawdown", "trade_count"))
                    and _number(metrics.get("equity")) >= 0
                    and 0 <= _number(metrics.get("drawdown")) <= 1
                    and _number(metrics.get("trade_count")) >= 0
                ):
                    self._performance = {
                        "period": period,
                        "equity": _number(metrics.get("equity")),
                        "pnl": _number(metrics.get("pnl")),
                        "total_return": _number(metrics.get("return")) * 100,
                        "drawdown": _number(metrics.get("drawdown")) * 100,
                        "trade_count": _number(metrics.get("trade_count")),
                        "timestamp": timestamp,
                    }
                    message = {"type": "performance-updated", "data": self._performance}
            elif event_type == "ReasoningGenerated":
                subject = payload.get("subject")
                subject_data = subject if isinstance(subject, dict) else {}
                item = {
                    "signal_id": _first_text(payload, "reasoning_id"),
                    "symbol": _first_text(subject_data, "symbol", default="Unknown"),
                    "reasoning": _first_text(payload, "summary"),
                    "details": payload.get("explanation", []),
                    "timestamp": timestamp,
                }
                if not any(entry["signal_id"] == item["signal_id"] for entry in self._reasoning):
                    self._reasoning.appendleft(item)
                    message = {"type": "ai-reasoning", "data": item}
            elif event_type == "NewsAnalyzed":
                item = _adapt_news(payload, timestamp)
                if item is not None and not any(
                    entry["id"] == item["id"] for entry in self._news
                ):
                    self._news.appendleft(item)
                    message = {"type": "news", "data": item}
            elif event_type in {"RiskRejected", "PaperRiskLimitEnforced", "ConnectionLost"}:
                alert_id = _first_text(payload, "trade_id", "connection_name", "order_id")
                reason = _first_text(payload, "reason", "message")
                if alert_id and reason:
                    item = {
                        "id": f"{event_type}:{alert_id}:{timestamp}",
                        "level": "error" if event_type != "RiskRejected" else "warning",
                        "message": reason,
                        "timestamp": timestamp,
                    }
                    if not any(entry["id"] == item["id"] for entry in self._alerts):
                        self._alerts.appendleft(item)
                        message = {"type": "alert", "data": item}

        if event_type in {"CandleClosed", "TimeframeCandleClosed"} and message is not None:
            return messages
        return message

    async def snapshot(
        self,
        name: str,
        *,
        symbol: str | None = None,
        timeframe: str = "1m",
    ) -> object:
        async with self._lock:
            if name == "watchlist":
                return list(self._watchlist.values())
            if name == "market-regime":
                return list(self._regimes.values())
            if name == "trade-health":
                return list(self._trade_health.values())
            if name == "active-trades":
                return list(self._active_trades.values())
            if name == "trade-journal":
                return list(self._trade_journal)
            if name == "ai-reasoning":
                return list(self._reasoning)
            if name == "news":
                return list(self._news)
            if name == "alerts":
                return list(self._alerts)
            if name == "portfolio":
                return self._portfolio
            if name == "performance":
                return self._performance
            if name == "candles":
                if symbol is None:
                    return []
                return list(self._candles.get((symbol, timeframe), ()))
        raise KeyError(name)

    def _put(self, values: OrderedDict[str, dict[str, Any]], key: str, value: dict[str, Any]) -> None:
        values[key] = value
        values.move_to_end(key)
        self._trim(values)

    def _trim(self, values: OrderedDict[Any, Any]) -> None:
        while len(values) > self._history_limit:
            values.popitem(last=False)

    def _remember_closed_trade(self, trade_id: str) -> None:
        self._closed_trade_ids[trade_id] = None
        self._closed_trade_ids.move_to_end(trade_id)
        self._trim(self._closed_trade_ids)


class WebSocketEventHub:
    def __init__(self, read_model: DashboardReadModel | None = None) -> None:
        self._clients: set[WebSocketSender] = set()
        self._read_model = read_model or DashboardReadModel()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        await websocket.send_json(
            {"type": "connection-established", "data": {"status": "connected"}}
        )

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def publish(self, event: EventEnvelope) -> None:
        result = await self._read_model.consume(event)
        if result is None:
            return
        messages = result if isinstance(result, tuple) else (result,)
        failed_clients: list[WebSocketSender] = []
        for client in tuple(self._clients):
            for message in messages:
                try:
                    await client.send_json(message)
                except (WebSocketDisconnect, RuntimeError):
                    failed_clients.append(client)
                    break
        for client in failed_clients:
            self.disconnect(client)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = RuntimeSettings.from_environment()
    kernel = create_production_kernel(settings)
    read_model = DashboardReadModel()
    event_hub = WebSocketEventHub(read_model)
    event_subscription = await kernel.event_bus.subscribe_topic("#", event_hub.publish)
    app.state.kernel = kernel
    try:
        await kernel.start()
        replay: ReplayEngine = kernel.container.resolve(ReplayEngine)
        configure_replay(replay, settings)
        dataset = None
        if settings.candle_csv is not None:
            provider = CSVHistoricalDataProvider(
                settings.candle_csv,
                symbol=settings.symbol,
                exchange=settings.exchange,
                timeframe=settings.timeframe,
                timezone=settings.timezone,
                timestamp_column=settings.timestamp_column,
            )
            candles = await provider.load_candles(
                settings.symbol,
                settings.exchange,
                settings.timeframe,
                datetime.min.replace(tzinfo=UTC),
                datetime.max.replace(tzinfo=UTC),
            )
            repository: MarketDataRepository = kernel.container.resolve(MarketDataRepository)
            for candle in candles:
                await repository.append_candle(candle)
            await replay.load_session(
                settings.symbol,
                settings.exchange,
                candles[0].timestamp,
                candles[-1].timestamp,
                settings.timeframe,
            )
            dataset = {
                "name": Path(settings.candle_csv).name,
                "identity": await asyncio.to_thread(_file_sha256, settings.candle_csv),
                "symbol": settings.symbol,
                "exchange": settings.exchange,
                "timeframe": settings.timeframe.value,
                "timezone": settings.timezone,
                "candle_count": len(candles),
                "nonzero_volume_candle_count": sum(candle.volume > 0 for candle in candles),
                "start": candles[0].timestamp.isoformat(),
                "end": candles[-1].timestamp.isoformat(),
            }
        await kernel.event_bus.wait_until_idle()
        app.state.dashboard_read_model = read_model
        app.state.websocket_event_hub = event_hub
        app.state.runtime_settings = settings
        app.state.replay_engine = replay
        app.state.replay_started = False
        app.state.dataset = dataset
        yield
    finally:
        replay = getattr(app.state, "replay_engine", None)
        if replay is not None and replay.state in {ReplayState.PLAYING, ReplayState.PAUSED}:
            await replay.stop()
        await kernel.event_bus.unsubscribe(event_subscription)
        await kernel.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tranalyset",
        version="0.1.0",
        description="Event-driven microkernel shell for an AI trading operating system.",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["platform"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/runtime")
    async def runtime_status() -> dict[str, object]:
        settings: RuntimeSettings = app.state.runtime_settings
        return {
            "paper_only": True,
            "dataset": app.state.dataset,
            "symbol": settings.symbol,
            "timeframe": settings.timeframe.value,
            "exchange": settings.exchange,
            "execution_assumptions": {
                "initial_cash": settings.initial_cash,
                "commission_rate": settings.commission_rate,
                "slippage_rate": settings.slippage_rate,
                "execution_mode": "simulated_paper_only",
            },
            "plugin_count": len(app.state.kernel.plugin_states()),
        }

    @app.get("/api/engines")
    async def engines() -> list[dict[str, object]]:
        kernel: Microkernel = app.state.kernel
        health = await kernel.plugin_health_checks()
        return [
            {
                "plugin_id": plugin_id,
                "state": kernel.plugin_states()[plugin_id].value,
                "health": status.status.value,
                "message": status.message,
                "details": dict(status.details),
            }
            for plugin_id, status in health.items()
        ]

    @app.get("/api/replay/status")
    async def replay_status() -> dict[str, object]:
        return await _replay_status(app)

    @app.post("/api/replay/start")
    async def start_replay() -> dict[str, object]:
        replay: ReplayEngine = app.state.replay_engine
        if app.state.dataset is None:
            raise HTTPException(status_code=409, detail="No candle dataset is configured.")
        if replay.state is not ReplayState.IDLE:
            raise HTTPException(status_code=409, detail="Replay can only start from the idle state.")
        app.state.replay_started = True
        await replay.play()
        return await _replay_status(app)

    @app.post("/api/replay/pause")
    async def pause_replay() -> dict[str, object]:
        replay: ReplayEngine = app.state.replay_engine
        if replay.state is not ReplayState.PLAYING:
            raise HTTPException(status_code=409, detail="Replay is not currently playing.")
        await replay.pause()
        return await _replay_status(app)

    @app.post("/api/replay/resume")
    async def resume_replay() -> dict[str, object]:
        replay: ReplayEngine = app.state.replay_engine
        if replay.state is not ReplayState.PAUSED:
            raise HTTPException(status_code=409, detail="Replay is not currently paused.")
        await replay.resume()
        return await _replay_status(app)

    @app.post("/api/replay/stop")
    async def stop_replay() -> dict[str, object]:
        replay: ReplayEngine = app.state.replay_engine
        if replay.state not in {ReplayState.PLAYING, ReplayState.PAUSED}:
            raise HTTPException(status_code=409, detail="Replay is not active.")
        await replay.stop()
        return await _replay_status(app)

    @app.post("/api/replay/step")
    async def step_replay() -> dict[str, object]:
        replay: ReplayEngine = app.state.replay_engine
        if app.state.dataset is None:
            raise HTTPException(status_code=409, detail="No candle dataset is configured.")
        if replay.state not in {ReplayState.IDLE, ReplayState.PAUSED, ReplayState.STOPPED}:
            raise HTTPException(status_code=409, detail="Replay cannot be stepped in its current state.")
        app.state.replay_started = True
        await replay.step()
        return await _replay_status(app)

    @app.get("/api/watchlist")
    async def watchlist() -> object:
        return await app.state.dashboard_read_model.snapshot("watchlist")

    @app.get("/api/market-regime")
    async def market_regime() -> object:
        return await app.state.dashboard_read_model.snapshot("market-regime")

    @app.get("/api/trade-health")
    async def trade_health() -> object:
        return await app.state.dashboard_read_model.snapshot("trade-health")

    @app.get("/api/active-trades")
    async def active_trades() -> object:
        return await app.state.dashboard_read_model.snapshot("active-trades")

    @app.get("/api/trade-journal")
    async def trade_journal() -> object:
        return await app.state.dashboard_read_model.snapshot("trade-journal")

    @app.get("/api/ai-reasoning")
    async def ai_reasoning() -> object:
        return await app.state.dashboard_read_model.snapshot("ai-reasoning")

    @app.get("/api/news")
    async def news() -> object:
        return await app.state.dashboard_read_model.snapshot("news")

    @app.get("/api/alerts")
    async def alerts() -> object:
        return await app.state.dashboard_read_model.snapshot("alerts")

    @app.get("/api/portfolio")
    async def portfolio() -> object:
        return await app.state.dashboard_read_model.snapshot("portfolio")

    @app.get("/api/performance")
    async def performance() -> object:
        return await app.state.dashboard_read_model.snapshot("performance")

    @app.get("/api/candles/{symbol}")
    async def candles(
        symbol: str,
        timeframe: str = Query(default="5m"),
        limit: int = Query(default=10_000, ge=1, le=10_000),
    ) -> object:
        try:
            candle_timeframe = Timeframe(timeframe)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=f"Unsupported candle timeframe: {timeframe}") from error
        repository: MarketDataRepository = app.state.kernel.container.resolve(MarketDataRepository)
        data = await repository.get_candles(
            symbol,
            app.state.runtime_settings.exchange,
            candle_timeframe,
            limit=limit,
        )
        return [
            {
                "time": candle.timestamp.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in data
        ]

    @app.websocket("/ws")
    async def realtime_updates(websocket: WebSocket) -> None:
        event_hub: WebSocketEventHub = websocket.app.state.websocket_event_hub
        await event_hub.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            event_hub.disconnect(websocket)

    return app


def _first_text(payload: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _number(value: object) -> float:
    return float(value) if _is_number(value) else 0.0


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _numeric_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): float(item) for key, item in value.items() if _is_number(item)}


def _has_numbers(payload: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return all(_is_number(payload.get(key)) for key in keys)


def _adapt_news(payload: Mapping[str, Any], timestamp: str) -> dict[str, Any] | None:
    title = _first_text(payload, "title", "headline", "summary")
    if not title:
        return None
    sentiment = _first_text(payload, "sentiment", default="neutral").lower()
    sentiment = {"bullish": "positive", "bearish": "negative"}.get(sentiment, sentiment)
    if sentiment not in {"positive", "negative", "neutral"}:
        sentiment = "neutral"
    symbols = payload.get("symbols", ())
    return {
        "id": _first_text(payload, "news_id", "article_id", default=title),
        "title": title,
        "source": _first_text(
            payload,
            "source",
            "source_name",
            "provider",
            default="Unknown",
        ),
        "sentiment": sentiment,
        "timestamp": timestamp,
        "symbols": list(symbols) if isinstance(symbols, list) else [],
    }


async def _replay_status(app: FastAPI) -> dict[str, object]:
    replay: ReplayEngine = app.state.replay_engine
    return {
        "state": replay.state.value,
        "progress": await replay.progress(),
        "started": app.state.replay_started,
        "dataset": app.state.dataset,
        "replay_time": replay.replay_time.isoformat() if replay.replay_time else None,
    }


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
