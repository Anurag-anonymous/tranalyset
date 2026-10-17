from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tranalyset.domain.common.value_objects import Ratio
from tranalyset.domain.risk.models import RiskAssessment, RiskLimits, RiskProfile, RiskScore


@dataclass(frozen=True, slots=True)
class RiskFactory:
    @staticmethod
    def create_profile(profile_id: str, name: str, *, limits: RiskLimits | None = None) -> RiskProfile:
        return RiskProfile(profile_id=profile_id, name=name, limits=limits or RiskLimits())

    @staticmethod
    def create_score(value: float | int | str, *, label: str | None = None) -> RiskScore:
        return RiskScore(value=value, label=label)

    @staticmethod
    def create_assessment(
        trade_id: str,
        approved: bool,
        observed_risk: float | int | str,
        *,
        risk_score: RiskScore | None = None,
        created_at: datetime | None = None,
    ) -> RiskAssessment:
        kwargs = {"created_at": created_at} if created_at is not None else {}
        return RiskAssessment(
            trade_id=trade_id,
            approved=approved,
            observed_risk=Ratio(observed_risk),
            risk_score=risk_score,
            **kwargs,
        )
