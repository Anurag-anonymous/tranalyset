"""Risk plugin and event contracts."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tranalyset.plugins.risk.contracts import (
        PORTFOLIO_UPDATED,
        POSITION_SIZE_CALCULATED,
        RISK_APPROVED,
        RISK_REJECTED,
        STOP_LOSS_UPDATED,
        TAKE_PROFIT_UPDATED,
        TRAILING_STOP_UPDATED,
        TRADE_REQUESTED,
        TRADE_UPDATED,
        PositionSizeCalculatedPayload,
        RiskApprovedPayload,
        RiskRejectedPayload,
        StopLossUpdatedPayload,
        TakeProfitUpdatedPayload,
        TradeRequestedPayload,
    )
    from tranalyset.plugins.risk.plugin import RiskPlugin, create_plugin


_CONTRACT_EXPORTS = {
    "PORTFOLIO_UPDATED",
    "POSITION_SIZE_CALCULATED",
    "RISK_APPROVED",
    "RISK_REJECTED",
    "STOP_LOSS_UPDATED",
    "TAKE_PROFIT_UPDATED",
    "TRAILING_STOP_UPDATED",
    "TRADE_REQUESTED",
    "TRADE_UPDATED",
    "PositionSizeCalculatedPayload",
    "RiskApprovedPayload",
    "RiskRejectedPayload",
    "StopLossUpdatedPayload",
    "TakeProfitUpdatedPayload",
    "TradeRequestedPayload",
}


def __getattr__(name: str) -> object:
    if name in _CONTRACT_EXPORTS:
        from tranalyset.plugins.risk import contracts

        return getattr(contracts, name)
    if name in {"RiskPlugin", "create_plugin"}:
        from tranalyset.plugins.risk import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_CONTRACT_EXPORTS | {"RiskPlugin", "create_plugin"})
