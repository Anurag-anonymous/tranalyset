from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tranalyset.domain.common import OrderSide
from tranalyset.domain.common.value_objects import Price, Quantity
from tranalyset.domain.market import Instrument
from tranalyset.domain.trading.models import Trade


@dataclass(frozen=True, slots=True)
class TradeFactory:
    @staticmethod
    def create_trade(
        trade_id: str,
        order_id: str,
        instrument: Instrument,
        side: OrderSide,
        quantity: Quantity,
        price: Price,
        executed_at: datetime,
    ) -> Trade:
        return Trade(
            trade_id=trade_id,
            order_id=order_id,
            instrument=instrument,
            side=side,
            quantity=quantity,
            price=price,
            executed_at=executed_at,
        )
