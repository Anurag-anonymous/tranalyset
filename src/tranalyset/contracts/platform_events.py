from tranalyset.contracts.events import EventType

APPLICATION_STARTED = EventType("ApplicationStarted")
TIMER_ELAPSED = EventType("TimerElapsed")

__all__ = ["APPLICATION_STARTED", "TIMER_ELAPSED"]
