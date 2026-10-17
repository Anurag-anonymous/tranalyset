from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tranalyset.domain.common.value_objects import ATR, Price, Ratio
from tranalyset.domain.portfolio import Position
from tranalyset.domain.risk.models import StopLoss, StopLossType
from tranalyset.domain.trading.models import Confidence, RewardRisk, TradeHealth, TradeReason, TradeStatus, TradeEvidence


@dataclass(frozen=True, slots=True)
class TradeHealthResult:
    score: Ratio
    factors: dict[str, Decimal]


class TradeHealthCalculator:
    @staticmethod
    def calculate(
        position: Position,
        current_price: Price,
        stop_loss: StopLoss,
        atr: ATR,
        risk_per_trade: Ratio,
    ) -> TradeHealth:
        if atr.value <= 0:
            sl_multiple = Decimal("0")
        else:
            if stop_loss.type is StopLossType.PRICE:
                sl_distance = abs(current_price.value - stop_loss.price.value)
            else:
                sl_distance = current_price.value * stop_loss.percent.value
            sl_multiple = Decimal(str(sl_distance / atr.value))

        if sl_multiple >= Decimal("1.5") and sl_multiple <= Decimal("3"):
            sl_score = Decimal("1")
        elif sl_multiple < Decimal("1.5"):
            sl_score = sl_multiple / Decimal("1.5")
        else:
            sl_score = max(Decimal("0"), Decimal("1") - ((sl_multiple - Decimal("3")) / Decimal("2")))

        risk_alignment = max(Decimal("0"), Decimal("1") - abs(risk_per_trade.value - Decimal("0.02")) * Decimal("10"))
        health_score = max(Decimal("0"), min(Decimal("1"), (sl_score + risk_alignment) / Decimal("2")))

        return TradeHealth(
            trade_id=f"{position.instrument.instrument_id}:{position.direction}",
            score=int((health_score * Decimal("100")).to_integral_value()),
            confidence=Confidence(health_score),
            reward_risk=RewardRisk(max(Decimal("0.1"), Decimal("1") + sl_score)),
            status=TradeStatus.PROPOSED,
            reason=TradeReason("Calculated from stop distance and risk alignment"),
            evidence=TradeEvidence(data={"sl_multiple": float(sl_multiple), "risk_alignment": float(risk_alignment)}),
        )
