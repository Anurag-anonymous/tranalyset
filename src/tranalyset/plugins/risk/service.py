from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from tranalyset.domain.common.value_objects import Currency, Money, Price, Ratio
from tranalyset.domain.risk.models import (
    LiquidityRisk,
    RiskAssessment,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidence,
    RiskScore,
    RiskDecisionOutcome,
    RiskScore,
    StopLoss,
    StopLossType,
    TakeProfit,
    TakeProfitType,
    TrailingStop,
    TrailingStopType,
)
from tranalyset.domain.services.position_size_calculator import PositionSizeCalculator
from tranalyset.domain.services.reward_risk_calculator import RewardRiskCalculator
from tranalyset.domain.services.risk_evaluator import RiskEvaluator


@dataclass(frozen=True, slots=True)
class RiskInput:
    trade_id: str
    symbol: str
    timeframe: str
    source_event_type: str
    entry_price: float
    side: str
    probability: float
    confidence: float
    probability_distribution: dict[str, float]
    health_score: float
    portfolio_capital: float
    portfolio_exposure: float
    portfolio_drawdown: float
    portfolio_correlation: float
    sector_correlation: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    liquidity: float
    gap_risk: float
    tail_risk: float
    expected_drawdown: float
    expected_value: float
    reward_risk: float
    account_risk_per_trade: float = 0.01
    max_drawdown: float = 0.2
    max_daily_loss: float = 0.02
    max_weekly_loss: float = 0.05
    max_monthly_loss: float = 0.1
    max_exposure: float = 0.25
    max_concurrent_trades: int = 10


@dataclass(frozen=True, slots=True)
class RiskEvaluationResult:
    decision: RiskDecision
    position_size: float | None
    stop_loss_price: float | None
    take_profit_price: float | None
    trailing_stop_price: float | None
    evidence: dict[str, Any]


class RiskEngine:
    def evaluate(self, input: RiskInput) -> RiskEvaluationResult:
        evidence = self._build_evidence(input)
        score_value = self._build_score(input, evidence)
        risk_score = RiskScore(
            value=score_value,
            confidence=Ratio(min(max(input.confidence, 0.0), 1.0)),
            label="AdvancedRiskEngine",
            evidence=evidence,
        )
        decision = RiskDecision(
            decision_id=f"risk:{input.trade_id}",
            outcome=self._outcome(score_value, input),
            score=risk_score,
            reason=self._reason(score_value, input),
            evidence=RiskEvidence(factors=evidence, description="Risk evaluation factors."),
        )
        assessment = RiskAssessment(
            trade_id=input.trade_id,
            approved=decision.outcome != RiskDecisionOutcome.REJECTED,
            observed_risk=Ratio(score_value / Decimal("100")),
            limits=(),
            evidence=evidence,
            stop_loss=Price(input.entry_price) if input.entry_price > 0 else None,
            risk_score=risk_score,
            decision=decision,
        )

        position_size = self._calculate_position_size(input, decision)
        stop_loss_price = self._calculate_stop_loss(input)
        take_profit_price = self._calculate_take_profit(input, stop_loss_price)
        trailing_stop_price = self._calculate_trailing_stop(input)

        return RiskEvaluationResult(
            decision=decision,
            position_size=position_size.quantity if position_size is not None else None,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            trailing_stop_price=trailing_stop_price,
            evidence=evidence,
        )

    def _build_evidence(self, input: RiskInput) -> dict[str, Any]:
        return {
            "probability": input.probability,
            "confidence": input.confidence,
            "probability_distribution": dict(input.probability_distribution),
            "health_score": input.health_score,
            "portfolio_drawdown": input.portfolio_drawdown,
            "portfolio_exposure": input.portfolio_exposure,
            "portfolio_correlation": input.portfolio_correlation,
            "sector_correlation": input.sector_correlation,
            "daily_pnl": input.daily_pnl,
            "weekly_pnl": input.weekly_pnl,
            "monthly_pnl": input.monthly_pnl,
            "liquidity": input.liquidity,
            "gap_risk": input.gap_risk,
            "tail_risk": input.tail_risk,
            "expected_drawdown": input.expected_drawdown,
            "expected_value": input.expected_value,
            "reward_risk": input.reward_risk,
            "account_allocation": input.account_risk_per_trade,
        }

    def _build_score(self, input: RiskInput, evidence: dict[str, Any]) -> Decimal:
        base = Decimal(100)
        performance = Decimal(str(input.probability)) * Decimal(20) + Decimal(str(input.health_score)) * Decimal(0.4)
        correlation_penalty = Decimal(str(input.portfolio_correlation)) * Decimal(25)
        volatility_penalty = Decimal(str(input.liquidity)) * Decimal(12)
        drawdown_penalty = Decimal(str(input.portfolio_drawdown)) * Decimal(30)
        gap_tail_penalty = Decimal(str(input.gap_risk + input.tail_risk)) * Decimal(20)
        reward_bonus = Decimal(str(input.reward_risk)) * Decimal(8)
        expected_value_bonus = Decimal(str(input.expected_value)) * Decimal(10)
        score = base - performance - correlation_penalty - volatility_penalty - drawdown_penalty - gap_tail_penalty + reward_bonus + expected_value_bonus
        if input.daily_pnl < -input.max_daily_loss * input.portfolio_capital:
            score += Decimal(30)
        if input.weekly_pnl < -input.max_weekly_loss * input.portfolio_capital:
            score += Decimal(20)
        if input.monthly_pnl < -input.max_monthly_loss * input.portfolio_capital:
            score += Decimal(10)
        return max(Decimal(0), min(Decimal(100), score))

    def _outcome(self, score: Decimal, input: RiskInput) -> RiskDecisionOutcome:
        if input.portfolio_drawdown > input.max_drawdown:
            return RiskDecisionOutcome.REJECTED
        if input.portfolio_exposure > input.max_exposure:
            return RiskDecisionOutcome.REJECTED
        if score <= Decimal(40):
            return RiskDecisionOutcome.APPROVED
        if score <= Decimal(70):
            return RiskDecisionOutcome.REDUCED
        return RiskDecisionOutcome.REJECTED

    def _reason(self, score: Decimal, input: RiskInput) -> str:
        if input.portfolio_drawdown > input.max_drawdown:
            return "Portfolio drawdown exceeds configured limits."
        if input.portfolio_exposure > input.max_exposure:
            return "Portfolio exposure exceeds configured allocation."
        if score > Decimal(70):
            return "Advanced risk score exceeds acceptable threshold."
        if score > Decimal(40):
            return "Risk is elevated but may still be acceptable with reduced sizing."
        return "Risk profile is acceptable for the proposed trade."

    def _calculate_position_size(self, input: RiskInput, decision: RiskDecision) -> PositionSizeResult | None:
        if input.entry_price <= 0 or input.portfolio_capital <= 0:
            return None
        equity = Money(Decimal(str(input.portfolio_capital)), Currency("USD"))
        if decision.outcome == RiskDecisionOutcome.REJECTED:
            return None
        if input.probability > 0.55:
            kelly = PositionSizeCalculator.kelly(
                win_probability=input.probability,
                win_loss_ratio=max(input.reward_risk, 1.0),
                account_equity=equity,
            )
            quantity = (
                equity.amount * kelly.percentage_of_equity.value / Decimal(str(input.entry_price))
            )
            return replace(kelly, quantity=quantity)
        return PositionSizeCalculator.fixed_fractional(
            account_equity=equity,
            risk_per_trade=Ratio(min(max(input.account_risk_per_trade, 0.0), 1.0)),
            entry_price=Price(input.entry_price),
            stop_loss=self._stop_loss_object(input),
        )

    def _stop_loss_object(self, input: RiskInput) -> StopLoss:
        if input.side.lower() == "short":
            stop_price = input.entry_price + max(input.expected_drawdown, 0.01) * input.entry_price
            return StopLoss(type=StopLossType.PRICE, price=Price(stop_price))
        stop_price = input.entry_price - max(input.expected_drawdown, 0.01) * input.entry_price
        return StopLoss(type=StopLossType.PRICE, price=Price(stop_price))

    def _calculate_stop_loss(self, input: RiskInput) -> float | None:
        if input.entry_price <= 0:
            return None
        atr = max(input.expected_drawdown, 0.005) * input.entry_price
        if input.side.lower() == "short":
            return input.entry_price + atr
        return input.entry_price - atr

    def _calculate_take_profit(self, input: RiskInput, stop_loss_price: float | None) -> float | None:
        if stop_loss_price is None or input.entry_price <= 0:
            return None
        reward_price = abs(input.entry_price - stop_loss_price) * max(input.reward_risk, 1.5)
        if input.side.lower() == "short":
            return input.entry_price - reward_price
        return input.entry_price + reward_price

    def _calculate_trailing_stop(self, input: RiskInput) -> float | None:
        if input.entry_price <= 0:
            return None
        anchor = max(input.expected_drawdown, 0.01) * input.entry_price
        if input.side.lower() == "short":
            return input.entry_price + anchor
        return input.entry_price - anchor
