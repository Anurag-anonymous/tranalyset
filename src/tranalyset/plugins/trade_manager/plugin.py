from __future__ import annotations

import math
from datetime import UTC, datetime

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.risk.contracts import (
    RISK_APPROVED,
    RISK_REJECTED,
    TRADE_REQUESTED,
    TradeRequestedPayload,
)
from tranalyset.plugins.trade_manager.contracts import (
    PUBLISHED_EVENTS,
    TRADE_CLOSED,
    TRADE_HEALTH_UPDATED,
    TRADE_MODIFIED,
    TRADE_OPENED,
    TRADE_SIGNAL_GENERATED,
    TRAILING_STOP_UPDATED,
    TradeClosedPayload,
    TradeModifiedPayload,
    TradeOpenedPayload,
    TrailingStopUpdatedPayload,
)

ACCEPTED_CANDIDATE = EventType("AcceptedCandidate")
PAPER_EXECUTION_RECORDED = EventType("PaperExecutionRecorded")
PAPER_TRADE_CLOSED = EventType("PaperTradeClosed")


class TradeManagerPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="trade_manager",
                name="Trade Manager",
                version="1.0.0",
            )
        )
        self._signal_events_seen = 0
        self._risk_events_seen = 0
        self._health_events_seen = 0
        self._trade_states: dict[str, str] = {}
        self._handled_signals: set[str] = set()
        self._active_trade_by_market: dict[tuple[str, str], str] = {}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_event(
            TRADE_REQUESTED,
            description="Trade manager publishes an intent for risk assessment.",
        )
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Trade manager plugin publishes {event_type}.",
            )

    @subscribes_to(TRADE_SIGNAL_GENERATED)
    async def handle_trade_signal_generated(self, event: EventEnvelope) -> None:
        self._signal_events_seen += 1

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        self._risk_events_seen += 1
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) == "requested":
            self._trade_states[trade_id] = "approved"

    @subscribes_to(ACCEPTED_CANDIDATE)
    async def handle_accepted_candidate(self, event: EventEnvelope) -> None:
        payload = event.payload
        if payload.get("decision") != "accepted":
            return
        if payload.get("candidate_type") not in {"EntryCandidate", "TradeSignalCandidate"}:
            return
        signal_id = _text(payload.get("signal_id"))
        if not signal_id or signal_id in self._handled_signals:
            return
        self._handled_signals.add(signal_id)
        evidence = _mapping(payload.get("supporting_evidence"))
        entry_price = _number(
            evidence.get("entry_price", evidence.get("close_price", evidence.get("last_price")))
        )
        side = _text(evidence.get("side")) or "long"
        if side.lower() != "long":
            self._trade_states[f"trade:{signal_id}"] = "rejected"
            return
        if not math.isfinite(entry_price) or entry_price <= 0:
            self._trade_states[f"trade:{signal_id}"] = "not_actionable"
            return
        trade_id = f"trade:{signal_id}"
        if trade_id in self._trade_states:
            return
        market_key = (
            _text(payload.get("symbol")),
            _text(payload.get("timeframe")),
        )
        active_trade_id = self._active_trade_by_market.get(market_key)
        if active_trade_id is not None:
            active_state = self._trade_states.get(active_trade_id)
            if active_state not in {"closed", "rejected", "not_actionable"}:
                self._trade_states[trade_id] = "rejected"
                return
            self._active_trade_by_market.pop(market_key, None)
        self._trade_states[trade_id] = "requested"
        self._active_trade_by_market[market_key] = trade_id
        await self.publish(
            TRADE_REQUESTED,
            payload=TradeRequestedPayload(
                trade_id=trade_id,
                signal_id=signal_id,
                strategy_id=_text(payload.get("strategy_id")),
                symbol=_text(payload.get("symbol")),
                timeframe=_text(payload.get("timeframe")),
                requested_at=_timestamp(payload.get("decision_at"), event.timestamp),
                entry_price=entry_price,
                confidence=_number(payload.get("confidence")),
                reward_risk=_number(payload.get("reward_risk_estimate")),
                side=side,
                supporting_evidence={
                    str(key): value
                    for key, value in evidence.items()
                    if isinstance(value, (int, float, str, bool))
                },
                source_event_type=str(event.event_type),
                order_type=_text(evidence.get("order_type")) or "market",
                sector=_text(evidence.get("sector")) or None,
                correlation_group=_text(evidence.get("correlation_group")) or None,
            ).to_payload(),
        )

    @subscribes_to(RISK_REJECTED)
    async def handle_risk_rejected(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) == "requested":
            self._trade_states[trade_id] = "rejected"
            self._release_market_slot(trade_id)

    @subscribes_to(PAPER_EXECUTION_RECORDED)
    async def handle_paper_execution_recorded(self, event: EventEnvelope) -> None:
        if event.payload.get("action") not in {"order_rejected", "fill_rejected"}:
            return
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) in {"requested", "approved"}:
            self._trade_states[trade_id] = "rejected"
            self._release_market_slot(trade_id)

    @subscribes_to(TRADE_OPENED)
    async def handle_trade_opened(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) == "approved":
            self._trade_states[trade_id] = "open"

    @subscribes_to(TRADE_MODIFIED)
    async def handle_trade_modified(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) in {"open", "partially_closed"}:
            changes = _mapping(event.payload.get("changes"))
            if changes.get("status") == "partially_closed":
                self._trade_states[trade_id] = "partially_closed"

    @subscribes_to(TRADE_CLOSED)
    async def handle_trade_closed(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) in {"open", "partially_closed"}:
            self._trade_states[trade_id] = "closed"
            self._release_market_slot(trade_id)

    @subscribes_to(PAPER_TRADE_CLOSED)
    async def handle_paper_trade_closed(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"))
        if self._trade_states.get(trade_id) in {"open", "partially_closed"}:
            self._trade_states[trade_id] = "closed"
            self._release_market_slot(trade_id)

    def _release_market_slot(self, trade_id: str) -> None:
        for market_key, active_trade_id in tuple(self._active_trade_by_market.items()):
            if active_trade_id == trade_id:
                self._active_trade_by_market.pop(market_key, None)

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        self._health_events_seen += 1

    async def publish_trade_opened(self, payload: TradeOpenedPayload) -> None:
        await self.publish(TRADE_OPENED, payload=payload.to_payload())

    async def publish_trade_modified(self, payload: TradeModifiedPayload) -> None:
        await self.publish(TRADE_MODIFIED, payload=payload.to_payload())

    async def publish_trade_closed(self, payload: TradeClosedPayload) -> None:
        await self.publish(TRADE_CLOSED, payload=payload.to_payload())

    async def publish_trailing_stop_updated(self, payload: TrailingStopUpdatedPayload) -> None:
        await self.publish(TRAILING_STOP_UPDATED, payload=payload.to_payload())

    @property
    def trade_states(self) -> dict[str, str]:
        return dict(self._trade_states)

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if (
            self._signal_events_seen == 0
            or self._risk_events_seen == 0
            or self._health_events_seen == 0
        ):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Trade manager plugin is running before complete trade evidence.",
                details={
                    "signal_events_seen": self._signal_events_seen,
                    "risk_events_seen": self._risk_events_seen,
                    "health_events_seen": self._health_events_seen,
                },
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Trade manager plugin is receiving trade, risk, and health evidence.",
            details={
                "signal_events_seen": self._signal_events_seen,
                "risk_events_seen": self._risk_events_seen,
                "health_events_seen": self._health_events_seen,
            },
        )


def create_plugin() -> TradeManagerPlugin:
    return TradeManagerPlugin()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if hasattr(value, "items") else {}


def _timestamp(value: object, default: datetime) -> datetime:
    if not isinstance(value, str):
        return default
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
