"""Domain-specific exception hierarchy."""

from __future__ import annotations


class DomainValidationError(ValueError):
    """Base exception for all domain validation failures."""


class InvalidTradeTransition(DomainValidationError):
    """Raised when a trade transition is not allowed."""


class InvalidOrder(DomainValidationError):
    """Raised when an order violates domain rules."""


class InvalidRiskConfiguration(DomainValidationError):
    """Raised when risk configuration is inconsistent or unsafe."""


class InvalidMarketState(DomainValidationError):
    """Raised when a market state violates domain invariants."""


class InvalidPortfolioState(DomainValidationError):
    """Raised when a portfolio state violates domain invariants."""


class InvalidPosition(DomainValidationError):
    """Raised when a position violates domain invariants."""


class InvalidIndicator(DomainValidationError):
    """Raised when an indicator model violates domain invariants."""


class InvalidPrice(DomainValidationError):
    """Raised when a price violates domain invariants."""


class InvalidQuantity(DomainValidationError):
    """Raised when a quantity violates domain invariants."""
