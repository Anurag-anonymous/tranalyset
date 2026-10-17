import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { TradeData, wsService } from '../services/websocket';

const TradeJournal: React.FC = () => {
  const [trades, setTrades] = useState<TradeData[]>([]);

  useEffect(() => {
    const handleClosed = (data: TradeData) => {
      setTrades((previous) => [
        data,
        ...previous.filter((trade) => trade.trade_id !== data.trade_id),
      ].slice(0, 1000));
    };

    wsService.on('trade-closed', handleClosed);
    fetchSnapshot<TradeData[]>('trade-journal')
      .then(setTrades)
      .catch((error: unknown) => console.error('Failed to load trade journal:', error));
    return () => wsService.off('trade-closed', handleClosed);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Trade Journal</h1>
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Trading History</h2>
        <table className="w-full text-sm">
          <thead className="border-b border-slate-600">
            <tr>
              <th className="text-left py-2 text-slate-300">Date</th>
              <th className="text-left py-2 text-slate-300">Symbol</th>
              <th className="text-left py-2 text-slate-300">Entry</th>
              <th className="text-left py-2 text-slate-300">Exit</th>
              <th className="text-right py-2 text-slate-300">P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => (
              <tr key={trade.trade_id} className="border-b border-slate-700 hover:bg-slate-700">
                <td className="py-3 text-slate-400">{new Date(trade.timestamp).toLocaleString()}</td>
                <td className="py-3 font-semibold text-slate-100">{trade.symbol}</td>
                <td className="py-3 text-slate-300">
                  {trade.entry_price ? `$${trade.entry_price.toFixed(2)}` : '—'}
                </td>
                <td className="py-3 text-slate-300">
                  {trade.exit_price ? `$${trade.exit_price.toFixed(2)}` : '—'}
                </td>
                <td className="py-3 text-right text-slate-300">
                  {trade.pnl === undefined ? '—' : `$${trade.pnl.toFixed(2)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {trades.length === 0 && (
          <p className="py-8 text-center text-slate-400">No completed trades recorded</p>
        )}
      </div>
    </div>
  );
};

export default TradeJournal;
