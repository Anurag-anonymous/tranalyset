# Tranalyset Desktop Dashboard

A real-time React TypeScript trading dashboard with WebSocket support, TradingView Lightweight Charts, and dark theme.

## Features

- **Real-time Updates**: WebSocket integration for live market data, trade updates, and alerts
- **Multi-Panel Display**: 
  - Watchlist with live price updates
  - Market Regime monitoring
  - Trade Health scoring
  - Open Trades tracking
  - AI Reasoning insights
  - News aggregation with sentiment analysis
  - Portfolio overview
  - Paper Trading simulation
  - Performance metrics
  - Trade Journal
  - Alert notifications

- **Charts**: TradingView Lightweight Charts for candlestick analysis
- **Dark Theme**: Tailwind CSS with slate/emerald color scheme
- **Responsive Design**: Adapts to various screen sizes

## Installation

```bash
cd frontend
npm install
```

## Development

```bash
npm run dev
```

Server will run on `http://localhost:3000` and proxy API requests to `http://localhost:8000`.
The WebSocket connection targets `ws://localhost:8000/ws`; the backend accepts connections and
forwards events published on its kernel event bus. Read-only `/api` snapshots hydrate pages from
the same event-derived data. The current API composition starts a bare kernel without trading
plugins or a market-data provider, so snapshots remain empty until event-producing engines are
configured. A connected socket indicates transport connectivity, not live market data.

## Build

```bash
npm run build
```

## Testing

```bash
npm test
npm test:watch
```

## Architecture

- **Services**: `src/services/websocket.ts` - WebSocket client with type-safe event handling
- **Pages**: Individual dashboard pages for each feature
- **Components**: Reusable UI components (Chart, Sidebar, etc.)
- **Styles**: Tailwind CSS for dark theme UI

## WebSocket Events

The dashboard subscribes to the following event-derived WebSocket updates:

- `watchlist-updated`: Price and volume updates
- `market-regime-changed`: Market regime changes
- `trade-health-updated`: Trade health score updates
- `trade-opened`: New trades opened
- `trade-closed`: Trades closed
- `ai-reasoning`: AI reasoning insights
- `news`: Market news with sentiment
- `portfolio-updated`: Portfolio value and positions
- `performance-updated`: Paper engine period report (equity, P&L, return, drawdown, closed-trade count)
- `alert`: System alerts

## Environment

Server connects to WebSocket at `ws://localhost:8000/ws` by default.
Modify the WebSocketService constructor to change the endpoint.
