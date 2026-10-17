from __future__ import annotations

import math
import os
from dataclasses import dataclass
from importlib import import_module

from tranalyset.contracts.plugins import Plugin
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.replay import PlaybackMode, ReplayEngine
from tranalyset.plugins.breakout_strategy.plugin import BreakoutStrategy
from tranalyset.plugins.mean_reversion_strategy.plugin import MeanReversionStrategy
from tranalyset.plugins.momentum_strategy.plugin import MomentumStrategy

_PLUGIN_MODULES = (
    "ai_reasoning_engine",
    "backtesting_engine",
    "context_engine",
    "feature_engine",
    "indicator_engine",
    "knowledge_engine",
    "learning",
    "market_data",
    "market_state_engine",
    "market_structure_engine",
    "news",
    "paper_trading_engine",
    "pattern_intelligence_engine",
    "portfolio_engine",
    "probability_engine",
    "quantitative_research_engine",
    "risk",
    "signal_engine",
    "signal_orchestrator",
    "trade_health_engine",
    "trade_manager",
)


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    candle_csv: str | None
    symbol: str
    exchange: str
    timeframe: Timeframe
    timezone: str
    timestamp_column: str
    replay_speed: PlaybackMode
    initial_cash: float
    commission_rate: float
    slippage_rate: float

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        candle_csv = os.getenv("TRANALYSET_CANDLE_CSV", "").strip() or None
        symbol = os.getenv("TRANALYSET_CANDLE_SYMBOL", "NIFTY").strip()
        exchange = os.getenv("TRANALYSET_CANDLE_EXCHANGE", "NSE").strip()
        timezone = os.getenv("TRANALYSET_CANDLE_TIMEZONE", "Asia/Kolkata").strip()
        timestamp_column = os.getenv("TRANALYSET_CANDLE_TIMESTAMP_COLUMN", "datetime_ist").strip()
        timeframe = Timeframe(os.getenv("TRANALYSET_CANDLE_TIMEFRAME", "5m").strip())
        replay_speed = PlaybackMode(os.getenv("TRANALYSET_REPLAY_SPEED", "100x").strip())
        initial_cash = _environment_float("TRANALYSET_PAPER_INITIAL_CASH", 100_000.0)
        commission_rate = _environment_float("TRANALYSET_COMMISSION_RATE", 0.001)
        slippage_rate = _environment_float("TRANALYSET_SLIPPAGE_RATE", 0.0005)
        if not symbol or not exchange or not timezone or not timestamp_column:
            raise ValueError("Configured candle symbol, exchange, timezone, and timestamp column must not be empty.")
        if initial_cash <= 0 or commission_rate < 0 or slippage_rate < 0:
            raise ValueError("Paper capital must be positive and fee/slippage rates non-negative.")
        return cls(
            candle_csv=candle_csv,
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            timezone=timezone,
            timestamp_column=timestamp_column,
            replay_speed=replay_speed,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
        )

    def plugin_configuration(self) -> dict[str, dict[str, float]]:
        return {
            "paper_trading_engine": {
                "initial_cash": self.initial_cash,
                "commission_rate": self.commission_rate,
                "slippage_rate": self.slippage_rate,
            },
            "backtesting_engine": {"initial_capital": self.initial_cash},
        }


def create_production_plugins() -> tuple[Plugin, ...]:
    plugins: list[Plugin] = []
    for name in _PLUGIN_MODULES:
        module = import_module(f"tranalyset.plugins.{name}.plugin")
        factory = getattr(module, "create_plugin")
        plugin = factory()
        if not isinstance(plugin, Plugin):
            raise TypeError(f"Production factory for {name} returned a non-plugin.")
        plugins.append(plugin)
    plugins.extend((BreakoutStrategy(), MeanReversionStrategy(), MomentumStrategy()))
    return tuple(plugins)


def create_production_kernel(settings: RuntimeSettings) -> Microkernel:
    return Microkernel(
        plugin_loader=StaticPluginLoader(create_production_plugins()),
        plugin_config=settings.plugin_configuration(),
    )


def configure_replay(replay: ReplayEngine, settings: RuntimeSettings) -> None:
    replay.playback_mode = settings.replay_speed


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a finite number.") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value
