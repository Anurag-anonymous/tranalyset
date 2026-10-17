from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Self
from datetime import datetime, timezone

FINANCIAL_PRECISION = Decimal("0.000001")


def to_decimal(value: Decimal | int | float | str, *, field_name: str) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        msg = f"{field_name} must be a valid decimal value."
        raise ValueError(msg) from error


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(FINANCIAL_PRECISION, rounding=ROUND_HALF_UP)


def display_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return f"{normalized:.1f}"
    return f"{normalized:f}"


@dataclass(frozen=True, slots=True)
class Symbol:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not normalized:
            msg = "Symbol value cannot be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Exchange:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not normalized:
            msg = "Exchange value cannot be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Currency:
    code: str

    def __post_init__(self) -> None:
        normalized = self.code.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            msg = "Currency code must be a three-letter ISO-style code."
            raise ValueError(msg)
        object.__setattr__(self, "code", normalized)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = to_decimal(self.value, field_name="Quantity")
        if amount <= 0:
            msg = "Quantity must be greater than zero."
            raise ValueError(msg)
        object.__setattr__(self, "value", quantize_money(amount))

    def __str__(self) -> str:
        return display_decimal(self.value)

    def __add__(self, other: Quantity) -> Quantity:
        return Quantity(self.value + other.value)

    def __sub__(self, other: Quantity) -> Quantity:
        return Quantity(self.value - other.value)

    def __mul__(self, other: Decimal | int | float | str) -> Quantity:
        return Quantity(self.value * to_decimal(other, field_name="quantity multiplier"))

    def __truediv__(self, other: Decimal | int | float | str) -> Quantity:
        divisor = to_decimal(other, field_name="quantity divisor")
        if divisor == 0:
            msg = "Quantity divisor must not be zero."
            raise ValueError(msg)
        return Quantity(self.value / divisor)


@dataclass(frozen=True, slots=True)
class Volume:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = to_decimal(self.value, field_name="Volume")
        if amount < 0:
            msg = "Volume must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "value", quantize_money(amount))

    def __str__(self) -> str:
        return display_decimal(self.value)

    def __add__(self, other: Volume) -> Volume:
        return Volume(self.value + other.value)

    def __sub__(self, other: Volume) -> Volume:
        result = self.value - other.value
        if result < 0:
            msg = "Volume subtraction cannot produce a negative volume."
            raise ValueError(msg)
        return Volume(result)


@dataclass(frozen=True, slots=True)
class Price:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = to_decimal(self.value, field_name="Price")
        if amount < 0:
            msg = "Price cannot be negative."
            raise ValueError(msg)
        object.__setattr__(self, "value", quantize_money(amount))

    def __str__(self) -> str:
        return display_decimal(self.value)

    def __add__(self, other: Price) -> Price:
        return Price(self.value + other.value)

    def __sub__(self, other: Price) -> Price:
        result = self.value - other.value
        if result < 0:
            msg = "Price subtraction cannot produce a negative price."
            raise ValueError(msg)
        return Price(result)

    def __mul__(self, other: Decimal | int | float | str) -> Price:
        return Price(self.value * to_decimal(other, field_name="price multiplier"))

    def __truediv__(self, other: Decimal | int | float | str) -> Price:
        divisor = to_decimal(other, field_name="price divisor")
        if divisor == 0:
            msg = "Price divisor must not be zero."
            raise ValueError(msg)
        return Price(self.value / divisor)


@dataclass(frozen=True, slots=True)
class Spread:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = to_decimal(self.value, field_name="Spread")
        if amount < 0:
            msg = "Spread must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "value", quantize_money(amount))

    def __str__(self) -> str:
        return display_decimal(self.value)


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal | int | float | str
    currency: Currency = Currency("USD")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "amount",
            quantize_money(to_decimal(self.amount, field_name="Money amount")),
        )

    def ensure_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            msg = "Money values must have the same currency."
            raise ValueError(msg)

    def __add__(self, other: Money) -> Self:
        self.ensure_same_currency(other)
        return type(self)(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Self:
        self.ensure_same_currency(other)
        return type(self)(self.amount - other.amount, self.currency)

    def __mul__(self, other: Decimal | int | float | str) -> Self:
        return type(self)(self.amount * to_decimal(other, field_name="money multiplier"), self.currency)


@dataclass(frozen=True, slots=True)
class Liquidity:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = to_decimal(self.value, field_name="Liquidity")
        if amount < 0:
            msg = "Liquidity must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "value", quantize_money(amount))

    def __str__(self) -> str:
        return display_decimal(self.value)


@dataclass(frozen=True, slots=True)
class Ratio:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="Ratio")
        object.__setattr__(self, "value", quantize_money(value))

    @classmethod
    def bounded(cls, value: Decimal | int | float | str, *, field_name: str = "Ratio") -> Ratio:
        ratio = cls(value)
        if not Decimal("0") <= ratio.value <= Decimal("1"):
            msg = f"{field_name} must be between 0.0 and 1.0."
            raise ValueError(msg)
        return ratio


@dataclass(frozen=True, slots=True)
class Timeframe:
    seconds: int

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            msg = "Timeframe seconds must be positive."
            raise ValueError(msg)


# ---------- New Value Objects ----------


@dataclass(frozen=True, slots=True)
class Percentage:
    """A percentage value (0 to 100)."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="Percentage")
        if not (Decimal("0") <= value <= Decimal("100")):
            raise ValueError("Percentage must be between 0 and 100 inclusive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return f"{display_decimal(self.value)}%"

    def to_ratio(self) -> Ratio:
        """Convert to a Ratio (0 to 1)."""
        return Ratio(self.value / Decimal("100"))


@dataclass(frozen=True, slots=True)
class Probability:
    """A probability value (0 to 1)."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="Probability")
        if not (Decimal("0") <= value <= Decimal("1")):
            raise ValueError("Probability must be between 0 and 1 inclusive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return f"{display_decimal(self.value * Decimal('100'))}%"


@dataclass(frozen=True, slots=True)
class Confidence:
    """A confidence value (0 to 1)."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="Confidence")
        if not (Decimal("0") <= value <= Decimal("1")):
            raise ValueError("Confidence must be between 0 and 1 inclusive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return f"{display_decimal(self.value * Decimal('100'))}%"


@dataclass(frozen=True, slots=True)
class Timestamp:
    """A timezone-aware timestamp."""
    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            msg = "Timestamp must be timezone-aware."
            raise ValueError(msg)
        # Ensure UTC for consistency
        object.__setattr__(self, "value", self.value.astimezone(timezone.utc))

    def __str__(self) -> str:
        return self.value.isoformat()

    def __add__(self, other: float) -> "Timestamp":
        """Add seconds (as float) to timestamp."""
        from datetime import timedelta

        return Timestamp(self.value + timedelta(seconds=other))

    def __sub__(self, other: float) -> "Timestamp":
        """Subtract seconds (as float) from timestamp."""
        from datetime import timedelta

        return Timestamp(self.value - timedelta(seconds=other))

    def diff(self, other: "Timestamp") -> float:
        """Return difference in seconds (self - other)."""
        return (self.value - other.value).total_seconds()


@dataclass(frozen=True, slots=True)
class ExchangeCode:
    """A stock exchange code (e.g., NYSE, NASDAQ)."""
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not normalized:
            msg = "Exchange code must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TickSize:
    """The minimum price increment of a security."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="TickSize")
        if value <= 0:
            raise ValueError("Tick size must be positive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class LotSize:
    """The minimum quantity increment of a security."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="LotSize")
        if value <= 0:
            raise ValueError("Lot size must be positive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class ATR:
    """Average True Range (a measure of volatility)."""
    value: Decimal | int | float | str  # in price units

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="ATR")
        if value < 0:
            raise ValueError("ATR must be non-negative.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class RSI:
    """Relative Strength Index (0 to 100)."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="RSI")
        if not (Decimal("0") <= value <= Decimal("100")):
            raise ValueError("RSI must be between 0 and 100 inclusive.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return f"{display_decimal(self.value)}"


@dataclass(frozen=True, slots=True)
class EMA:
    """Exponential Moving Average (a price average)."""
    value: Decimal | int | float | str  # in price units

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="EMA")
        if value < 0:
            raise ValueError("EMA must be non-negative.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class VWAP:
    """Volume Weighted Average Price."""
    value: Decimal | int | float | str  # in price units

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="VWAP")
        if value < 0:
            raise ValueError("VWAP must be non-negative.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class MACDValue:
    """MACD (Moving Average Convergence Divergence) value."""
    value: Decimal | int | float | str  # can be positive or negative

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="MACDValue")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class Distance:
    """A distance (e.g., in price units)."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="Distance")
        if value < 0:
            raise ValueError("Distance must be non-negative.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class RewardRiskRatio:
    """Ratio of potential reward to potential risk."""
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        value = to_decimal(self.value, field_name="RewardRiskRatio")
        if value < 0:
            raise ValueError("Reward/risk ratio must be non-negative.")
        object.__setattr__(self, "value", quantize_money(value))

    def __str__(self) -> str:
        return str(display_decimal(self.value))


@dataclass(frozen=True, slots=True)
class PositionSize:
    """The size of a position (in quantity of contracts/shares)."""
    value: Quantity  # reuse Quantity for validation

    def __post_init__(self) -> None:
        if not isinstance(self.value, Quantity):
            raise TypeError("Position size must be a Quantity.")

    def __str__(self) -> str:
        return str(self.value)

    # Delegate arithmetic to the underlying Quantity
    def __add__(self, other: PositionSize) -> PositionSize:
        return PositionSize(self.value + other.value)

    def __sub__(self, other: PositionSize) -> PositionSize:
        return PositionSize(self.value - other.value)

    def __mul__(self, other: Decimal | int | float | str) -> PositionSize:
        return PositionSize(self.value * other)

    def __truediv__(self, other: Decimal | int | float | str) -> PositionSize:
        return PositionSize(self.value / other)


@dataclass(frozen=True, slots=True)
class PnL:
    """Profit and Loss (in currency)."""
    value: Money  # reuse Money for validation and currency

    def __post_init__(self) -> None:
        if not isinstance(self.value, Money):
            raise TypeError("PnL must be a Money.")

    def __str__(self) -> str:
        return str(self.value)

    # Delegate arithmetic to the underlying Money
    def __add__(self, other: PnL) -> PnL:
        return PnL(self.value + other.value)

    def __sub__(self, other: PnL) -> PnL:
        return PnL(self.value - other.value)

    def __mul__(self, other: Decimal | int | float | str) -> PnL:
        return PnL(self.value * other)

    def __truediv__(self, other: Decimal | int | float | str) -> PnL:
        return PnL(self.value / other)