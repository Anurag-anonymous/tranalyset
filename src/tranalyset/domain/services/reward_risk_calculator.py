from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tranalyset.domain.common.value_objects import Price, Ratio
from tranalyset.domain.risk.models import StopLoss, StopLossType, TakeProfit, TakeProfitType


@dataclass(frozen=True, slots=True)
class RewardRiskResult:
    reward: Price
    risk: Price
    ratio: Ratio

    def __post_init__(self) -> None:
        if self.reward.value < 0 or self.risk.value < 0:
            msg = "Reward and risk must not be negative."
            raise ValueError(msg)


class RewardRiskCalculator:
    @staticmethod
    def calculate(entry_price: Price, stop_loss: StopLoss, take_profit: TakeProfit) -> RewardRiskResult:
        if stop_loss.type is StopLossType.PRICE:
            risk_value = abs(entry_price.value - stop_loss.price.value)
        else:
            risk_value = entry_price.value * stop_loss.percent.value

        if take_profit.type is TakeProfitType.PRICE:
            reward_value = abs(take_profit.price.value - entry_price.value)
        else:
            reward_value = entry_price.value * take_profit.percent.value

        ratio = Decimal("0") if risk_value == 0 else reward_value / risk_value
        return RewardRiskResult(
            reward=Price(reward_value),
            risk=Price(risk_value),
            ratio=Ratio(ratio),
        )

    @staticmethod
    def calculate_for_short(entry_price: Price, stop_loss: StopLoss, take_profit: TakeProfit) -> RewardRiskResult:
        if stop_loss.type is StopLossType.PRICE:
            risk_value = stop_loss.price.value - entry_price.value
            if risk_value < 0:
                msg = "For a short position, stop loss must be above entry price."
                raise ValueError(msg)
        else:
            risk_value = entry_price.value * stop_loss.percent.value

        if take_profit.type is TakeProfitType.PRICE:
            reward_value = entry_price.value - take_profit.price.value
            if reward_value < 0:
                msg = "For a short position, take profit must be below entry price."
                raise ValueError(msg)
        else:
            reward_value = entry_price.value * take_profit.percent.value

        ratio = Decimal("0") if risk_value == 0 else reward_value / risk_value
        return RewardRiskResult(
            reward=Price(reward_value),
            risk=Price(risk_value),
            ratio=Ratio(ratio),
        )
