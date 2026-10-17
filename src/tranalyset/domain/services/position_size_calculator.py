from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tranalyset.domain.common.value_objects import Money, Price, Ratio
from tranalyset.domain.risk.models import StopLoss, StopLossType


@dataclass(frozen=True, slots=True)
class PositionSizeResult:
    quantity: Decimal
    max_risk_amount: Money
    percentage_of_equity: Ratio

    def __post_init__(self) -> None:
        if self.quantity < 0:
            msg = "Quantity must not be negative."
            raise ValueError(msg)
        if self.max_risk_amount.amount < 0:
            msg = "Max risk amount must not be negative."
            raise ValueError(msg)
        if not Decimal("0") <= self.percentage_of_equity.value <= Decimal("1"):
            msg = "Percentage of equity must be between 0 and 1."
            raise ValueError(msg)


class PositionSizeCalculator:
    @staticmethod
    def fixed_fractional(
        account_equity: Money,
        risk_per_trade: Ratio,
        entry_price: Price,
        stop_loss: StopLoss,
    ) -> PositionSizeResult:
        if account_equity.amount <= 0:
            msg = "Account equity must be positive."
            raise ValueError(msg)
        if not Decimal("0") < risk_per_trade.value <= Decimal("1"):
            msg = "Risk per trade must be between 0 and 1."
            raise ValueError(msg)

        risk_amount = account_equity.amount * risk_per_trade.value
        if stop_loss.type is StopLossType.PRICE:
            risk_per_unit = abs(entry_price.value - stop_loss.price.value)
        else:
            risk_per_unit = entry_price.value * stop_loss.percent.value
        if risk_per_unit <= 0:
            msg = "Risk per unit must be positive."
            raise ValueError(msg)

        quantity = risk_amount / risk_per_unit
        position_value = entry_price.value * quantity
        return PositionSizeResult(
            quantity=quantity,
            max_risk_amount=Money(risk_amount, account_equity.currency),
            percentage_of_equity=Ratio(position_value / account_equity.amount),
        )

    @staticmethod
    def fixed_money(
        risk_amount: Money,
        entry_price: Price,
        stop_loss: StopLoss,
    ) -> PositionSizeResult:
        if risk_amount.amount <= 0:
            msg = "Risk amount must be positive."
            raise ValueError(msg)
        if stop_loss.type is StopLossType.PRICE:
            risk_per_unit = abs(entry_price.value - stop_loss.price.value)
        else:
            risk_per_unit = entry_price.value * stop_loss.percent.value
        if risk_per_unit <= 0:
            msg = "Risk per unit must be positive."
            raise ValueError(msg)

        quantity = risk_amount.amount / risk_per_unit
        return PositionSizeResult(
            quantity=quantity,
            max_risk_amount=Money(risk_amount.amount, risk_amount.currency),
            percentage_of_equity=Ratio(0),
        )

    @staticmethod
    def kelly(
        win_probability: float,
        win_loss_ratio: float,
        account_equity: Money,
    ) -> PositionSizeResult:
        if not 0 <= win_probability <= 1:
            msg = "Win probability must be between 0 and 1."
            raise ValueError(msg)
        if win_loss_ratio <= 0:
            msg = "Win/loss ratio must be positive."
            raise ValueError(msg)
        if account_equity.amount <= 0:
            msg = "Account equity must be positive."
            raise ValueError(msg)

        kelly_fraction = ((win_loss_ratio * win_probability) - (1 - win_probability)) / win_loss_ratio
        recommended_fraction = max(Decimal("0"), Decimal(str(kelly_fraction)) * Decimal("0.5"))
        recommended_fraction = min(recommended_fraction, Decimal("1"))

        risk_amount = account_equity.amount * recommended_fraction
        return PositionSizeResult(
            quantity=Decimal("0"),
            max_risk_amount=Money(risk_amount, account_equity.currency),
            percentage_of_equity=Ratio(recommended_fraction),
        )
