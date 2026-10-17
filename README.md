# Tranalyset

Tranalyset is the foundation for a production-grade AI Trading Operating System using an
event-driven microkernel architecture.

The system does not predict markets. It evaluates probabilities from evidence-bearing events,
keeps recommendations explainable, and treats risk management as a hard architectural boundary.

## Architecture

- `src/tranalyset/contracts`: shared immutable contracts used by the kernel and plugins.
- `src/tranalyset/kernel`: lifecycle, dependency injection, plugin registry, loader, and event bus.
- `src/tranalyset/plugin_sdk`: plugin authoring SDK.
- `src/tranalyset/plugins`: production plugins owned by this repository.
- `src/tranalyset/api`: FastAPI composition root that owns kernel startup and shutdown.
- `docs/architecture.md`: full design rationale and extension rules.
- `docs/plugin-sdk.md`: plugin authoring guide.
- `docs/plugins`: plugin-specific documentation.
- `tests`: architecture-level tests for event immutability, routing, lifecycle, and DI.

Every feature is implemented as a plugin. Plugins communicate only by publishing and subscribing
to events. The kernel owns plugin lifecycle and dependency ordering.

The kernel foundation currently supports plugin loading and unloading, service registration,
event registration, dependency-aware startup, reverse-order shutdown, and async event routing. It
contains no trading business logic.

The event bus supports async publish/subscribe, exact and wildcard topic routing, event filters,
priorities, retries, dead-letter handling, logging, and metrics snapshots.

## Development

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

## Running the API and Dashboard

Run the backend from the project root and the dashboard from a second terminal:

```powershell
# Use Python 3.12 or newer and install the package first (see Development).
$env:TRANALYSET_CANDLE_CSV = "C:\path\to\NIFTY_5min.csv"
$env:TRANALYSET_CANDLE_SYMBOL = "NIFTY"
$env:TRANALYSET_CANDLE_EXCHANGE = "NSE"
$env:TRANALYSET_CANDLE_TIMEFRAME = "5m"
$env:TRANALYSET_CANDLE_TIMEZONE = "Asia/Kolkata"
$env:TRANALYSET_CANDLE_TIMESTAMP_COLUMN = "datetime_ist"
python -m uvicorn tranalyset.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The backend exposes `/health`, `/ws`, and read-only dashboard
snapshots under `/api` for watchlist, candles, market regimes, trade health, paper trades, portfolio,
performance, reasoning, and news. Configure `TRANALYSET_CANDLE_CSV` to load a validated historical
candle file at startup. The API starts the production plugin set in paper-only mode; use the Paper
Trading page to step through candles or start/pause/resume/stop replay. Engine events populate
dashboard snapshots and WebSocket updates. Without a configured data source, engines still start,
but market-dependent pages remain empty.

The CSV reader expects a header row and columns named
`datetime_ist,open,high,low,close,volume` by default. Naive timestamps are interpreted in
`TRANALYSET_CANDLE_TIMEZONE`; timezone-aware timestamps are converted to UTC. Prices, volume, candle
bounds, and strictly increasing timestamps are validated before data enters the market-data
repository. The dashboard shows the dataset SHA-256 and its actual time range. Paper orders are
simulated only; this configuration does not enable live broker execution.
If all candle volumes are zero, the Paper Trading page calls this out; strategies that require
volume confirmation intentionally do not receive fabricated volume or entry signals.
