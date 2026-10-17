# Portfolio Engine Plugin

The Portfolio Engine owns its published portfolio view and risk allocations. It never generates
trading signals, trade candidates, entries, exits, orders, or recommendations. Filled paper state
is authoritative: quantities, cash, and equity are synchronized from `PaperPortfolioUpdated`
rather than inferred from risk approval or requested trades.

## Subscriptions

The plugin subscribes to:

- `PaperPortfolioUpdated`
- `PaperTradeClosed`
- `TradeOpened`
- `RiskApproved`

Risk allocations remain pending until a filled position appears in the paper snapshot. Closed
trades remove active risk. Snapshot equity must reconcile to cash plus marked open-position value;
invalid or inconsistent snapshots are rejected.

## Tracked State

The engine tracks:

- capital,
- cash,
- open positions,
- closed positions,
- exposure,
- sector exposure,
- correlation concentration,
- daily PnL,
- weekly PnL,
- monthly PnL,
- drawdown,
- portfolio risk.

Capital is the reconciled paper equity (cash plus marked open position value). Drawdown is measured
against peak equity. Portfolio risk combines approved risk evidence only for positions reflected in
the paper portfolio.

Daily, weekly, and monthly P&L are changes from the last observed equity before each period (or
initial capital when no earlier observation exists). Period values are not aliases for lifetime
realized plus unrealized P&L.

## Published Events

The plugin registers and publishes:

- `PortfolioUpdated`
- `ExposureChanged`
- `DrawdownChanged`
- `CapitalUpdated`

Payloads describe portfolio state only. They must not contain signal generation, trade intent, or
execution instructions.
