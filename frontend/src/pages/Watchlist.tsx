import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, WatchlistData } from '../services/websocket';

const Watchlist: React.FC = () => {
  const [items, setItems] = useState<WatchlistData[]>([]);

  useEffect(() => {
    const handleUpdate = (data: WatchlistData) => {
      setItems((prev) => {
        const updated = prev.filter((item) => item.symbol !== data.symbol);
        return [...updated, data];
      });
    };

    wsService.on('watchlist-updated', handleUpdate);
    fetchSnapshot<WatchlistData[]>('watchlist')
      .then(setItems)
      .catch((error: unknown) => console.error('Failed to load watchlist:', error));
    return () => wsService.off('watchlist-updated', handleUpdate);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Watchlist</h1>
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-700 border-b border-slate-600">
            <tr>
              <th className="px-6 py-3 text-left text-sm font-semibold text-slate-200">Symbol</th>
              <th className="px-6 py-3 text-right text-sm font-semibold text-slate-200">Price</th>
              <th className="px-6 py-3 text-right text-sm font-semibold text-slate-200">Change</th>
              <th className="px-6 py-3 text-right text-sm font-semibold text-slate-200">Volume</th>
              <th className="px-6 py-3 text-left text-sm font-semibold text-slate-200">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {items.map((item) => (
              <tr key={item.symbol} className="hover:bg-slate-700 transition-colors">
                <td className="px-6 py-3 font-semibold text-slate-100">{item.symbol}</td>
                <td className="px-6 py-3 text-right text-slate-100">${item.price.toFixed(2)}</td>
                <td
                  className={`px-6 py-3 text-right font-semibold ${
                    item.change > 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  {item.change > 0 ? '+' : ''}{item.change.toFixed(2)}%
                </td>
                <td className="px-6 py-3 text-right text-slate-400">
                  {(item.volume / 1e6).toFixed(2)}M
                </td>
                <td className="px-6 py-3 text-slate-400 text-sm">
                  {new Date(item.timestamp).toLocaleTimeString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <div className="p-8 text-center text-slate-400">No watchlist data</div>
        )}
      </div>
    </div>
  );
};

export default Watchlist;
