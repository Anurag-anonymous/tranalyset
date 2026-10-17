import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, TradeData } from '../services/websocket';

const OpenTrades: React.FC = () => {
  const [trades, setTrades] = useState<TradeData[]>([]);

  useEffect(() => {
    const handleOpened = (data: TradeData) => {
      setTrades((prev) => [
        ...prev.filter((trade) => trade.trade_id !== data.trade_id),
        data,
      ]);
    };

    const handleUpdated = (data: TradeData) => {
      setTrades((prev) => prev.map((trade) =>
        trade.trade_id === data.trade_id ? { ...trade, ...data } : trade,
      ));
    };

    const handleClosed = (data: TradeData) => {
      setTrades((prev) => prev.filter((trade) => trade.trade_id !== data.trade_id));
    };

    wsService.on('trade-opened', handleOpened);
    wsService.on('trade-updated', handleUpdated);
    wsService.on('trade-closed', handleClosed);
    fetchSnapshot<TradeData[]>('active-trades')
      .then(setTrades)
      .catch((error: unknown) => console.error('Failed to load active trades:', error));

    return () => {
      wsService.off('trade-opened', handleOpened);
      wsService.off('trade-updated', handleUpdated);
      wsService.off('trade-closed', handleClosed);
    };
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Open Trades</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {trades.map((trade) => (
          <div key={trade.trade_id} className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold text-slate-100">{trade.symbol}</h3>
              <span className="px-2 py-1 bg-emerald-900 text-emerald-300 text-xs rounded">
                {trade.status ?? 'Open'}
              </span>
            </div>
            <div className="space-y-2 text-sm">
              <p className="text-slate-400">Quantity: <span className="text-slate-100">{trade.quantity}</span></p>
              {trade.entry_price && (
                <p className="text-slate-400">Entry: <span className="text-slate-100">${trade.entry_price.toFixed(2)}</span></p>
              )}
              {trade.last_price !== undefined && (
                <p className="text-slate-400">Last: <span className="text-slate-100">${trade.last_price.toFixed(2)}</span></p>
              )}
              {trade.unrealized_pnl !== undefined && (
                <p className="text-slate-400">Unrealized P&amp;L: <span className="text-slate-100">${trade.unrealized_pnl.toFixed(2)}</span></p>
              )}
              <p className="text-slate-400">Opened: <span className="text-slate-100">{new Date(trade.timestamp).toLocaleString()}</span></p>
            </div>
          </div>
        ))}
      </div>
      {trades.length === 0 && (
        <div className="text-center py-12 text-slate-400">No open trades</div>
      )}
    </div>
  );
};

export default OpenTrades;
