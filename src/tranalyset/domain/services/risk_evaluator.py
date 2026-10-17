from __future__ import annotations

from dataclasses import dataclass

from tranalyset.domain.risk.models import RiskAssessment, RiskDecision, RiskDecisionOutcome, RiskScore


@dataclass(frozen=True, slots=True)
class RiskEvaluationResult:
    approved: bool
    decision: RiskDecision


class RiskEvaluator:
    @staticmethod
    def evaluate(assessment: RiskAssessment) -> RiskEvaluationResult:
        score = assessment.risk_score or RiskScore(0)
        if score.value <= 40:
            outcome = RiskDecisionOutcome.APPROVED
            approved = True
        elif score.value <= 70:
            outcome = RiskDecisionOutcome.REDUCED
            approved = True
        else:
            outcome = RiskDecisionOutcome.REJECTED
            approved = False

        decision = assessment.decision or RiskDecision(
            decision_id=f"decision:{assessment.trade_id}",
            outcome=outcome,
            score=score,
            reason=f"Evaluated from risk score {score.value}",
        )
        return RiskEvaluationResult(approved=approved, decision=decision)
