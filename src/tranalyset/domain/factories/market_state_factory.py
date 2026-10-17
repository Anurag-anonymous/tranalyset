from __future__ import annotations

from dataclasses import dataclass

from tranalyset.domain.analytics.models import MarketStructure
from tranalyset.domain.services.market_structure_analyzer import MarketStructureResult


@dataclass(frozen=True, slots=True)
class MarketStateFactory:
    @staticmethod
    def create_market_structure(result: MarketStructureResult) -> MarketStructure:
        return MarketStructure(regime=result.structure_type)
