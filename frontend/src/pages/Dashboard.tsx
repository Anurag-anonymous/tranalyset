import React, { useEffect, useState } from 'react';
import { DashboardRuntimeData, fetchSnapshot } from '../services/api';
import { wsService, WatchlistData, MarketRegimeData, TradeData } from '../services/websocket';
import Chart from '../components/Chart';

const Dashboard: React.FC = () => {
  const [watchlistItems, setWatchlistItems] = useState<WatchlistData[]>([]);
  const [regimes, setRegimes] = useState<Record<string, MarketRegimeData>>({});
  const [openTrades, setOpenTrades] = useState<TradeData[]>([]);
  const [runtime, setRuntime] = useState<DashboardRuntimeData | null>(null);

  useEffect(() => {
    fetchSnapshot<DashboardRuntimeData>('runtime')
      .then(setRuntime)
      .catch((error: unknown) => console.error('Failed to load runtime configuration:', error));

    const handleWatchlist = (data: WatchlistData) => {
      setWatchlistItems((prev) => {
        const updated = prev.filter((item) => item.symbol !== data.symbol);
        return [...updated, data];
      });
    };

    const handleRegime = (data: MarketRegimeData) => {
      setRegimes((prev) => ({ ...prev, [data.symbol]: data }));
    };

    const handleTradeOpened = (data: TradeData) => {
      setOpenTrades((prev) => [
        ...prev.filter((trade) => trade.trade_id !== data.trade_id),
        data,
      ]);
    };

    const handleTradeUpdated = (data: TradeData) => {
      setOpenTrades((prev) => prev.map((trade) =>
        trade.trade_id === data.trade_id ? { ...trade, ...data } : trade,
      ));
    };

    const handleTradeClosed = (data: TradeData) => {
      setOpenTrades((prev) => prev.filter((trade) => trade.trade_id !== data.trade_id));
    };

    wsService.on('watchlist-updated', handleWatchlist);
    wsService.on('market-regime-changed', handleRegime);
    wsService.on('trade-opened', handleTradeOpened);
    wsService.on('trade-updated', handleTradeUpdated);
    wsService.on('trade-closed', handleTradeClosed);
    fetchSnapshot<WatchlistData[]>('watchlist')
      .then(setWatchlistItems)
      .catch((error: unknown) => console.error('Failed to load watchlist:', error));
    fetchSnapshot<MarketRegimeData[]>('market-regime')
      .then((items) => setRegimes(Object.fromEntries(items.map((item) => [item.symbol, item]))))
      .catch((error: unknown) => console.error('Failed to load market regimes:', error));
    fetchSnapshot<TradeData[]>('active-trades')
      .then(setOpenTrades)
      .catch((error: unknown) => console.error('Failed to load active trades:', error));

    return () => {
      wsService.off('watchlist-updated', handleWatchlist);
      wsService.off('market-regime-changed', handleRegime);
      wsService.off('trade-opened', handleTradeOpened);
      wsService.off('trade-updated', handleTradeUpdated);
      wsService.off('trade-closed', handleTradeClosed);
    };
  }, []);

  const topMovers = [...watchlistItems]
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))
    .slice(0, 5);

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-4xl font-bold text-emerald-400">Dashboard</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Top Movers */}
        <div className="lg:col-span-2 bg-slate-800 rounded-lg border border-slate-700 p-6">
          <h2 className="text-lg font-semibold text-slate-100 mb-4">Top Movers</h2>
          <div className="space-y-3">
            {topMovers.length > 0 ? (
              topMovers.map((item) => (
                <div key={item.symbol} className="flex items-center justify-between p-3 bg-slate-700 rounded">
                  <div>
                    <p className="font-semibold text-slate-100">{item.symbol}</p>
                    <p className="text-sm text-slate-400">${item.price.toFixed(2)}</p>
                  </div>
                  <div
                    className={`font-bold ${
                      item.change > 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}
                  >
                    {item.change > 0 ? '+' : ''}{item.change.toFixed(2)}%
                  </div>
                </div>
              ))
            ) : (
              <p className="text-slate-400">No watchlist data</p>
            )}
          </div>
        </div>

        {/* Active Trades */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <h2 className="text-lg font-semibold text-slate-100 mb-4">Active Trades</h2>
          <p className="text-3xl font-bold text-emerald-400">{openTrades.length}</p>
          <p className="text-sm text-slate-400 mt-2">Open positions</p>
        </div>
      </div>

      {/* Market Regimes */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Market Regimes</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {Object.entries(regimes).map(([symbol, regime]) => (
            <div key={symbol} className="bg-slate-700 p-4 rounded border border-slate-600">
              <p className="font-semibold text-slate-100">{symbol}</p>
              <p className="text-sm text-emerald-400 mt-2">{regime.regime}</p>
              <p className="text-xs text-slate-400 mt-1">Confidence: {(regime.confidence * 100).toFixed(0)}%</p>
            </div>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h2 className="text-lg font-semibold text-slate-100 mb-1">
          {runtime?.symbol ?? 'NIFTY'} {runtime?.timeframe ?? '5m'} Price Chart
        </h2>
        <p className="text-xs text-slate-400 mb-4">
          {runtime?.dataset
            ? `${runtime.dataset.name} · ${runtime.dataset.candle_count} candles`
            : 'No historical candle dataset configured'}
        </p>
        <Chart symbol={runtime?.symbol ?? 'NIFTY'} timeframe={runtime?.timeframe ?? '5m'} />
      </div>
    </div>
  );
};

export default Dashboard;
