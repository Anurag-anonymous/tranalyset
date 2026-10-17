import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { MarketRegimeData, wsService } from '../services/websocket';

const MarketRegime: React.FC = () => {
  const [regimes, setRegimes] = useState<MarketRegimeData[]>([]);

  useEffect(() => {
    const handleUpdate = (data: MarketRegimeData) => {
      setRegimes((previous) => [
        ...previous.filter((item) => item.symbol !== data.symbol),
        data,
      ]);
    };

    wsService.on('market-regime-changed', handleUpdate);
    fetchSnapshot<MarketRegimeData[]>('market-regime')
      .then(setRegimes)
      .catch((error: unknown) => console.error('Failed to load market regimes:', error));
    return () => wsService.off('market-regime-changed', handleUpdate);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Market Regime</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {regimes.map((item) => (
          <article key={item.symbol} className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <h2 className="text-lg font-semibold text-slate-100">{item.symbol}</h2>
            <p className="text-emerald-400 mt-2">{item.regime}</p>
            <p className="text-sm text-slate-400 mt-2">
              Confidence: {(item.confidence * 100).toFixed(0)}%
            </p>
            <p className="text-xs text-slate-500 mt-2">
              Updated {new Date(item.timestamp).toLocaleString()}
            </p>
          </article>
        ))}
      </div>
      {regimes.length === 0 && (
        <p className="text-center py-12 text-slate-400">No market regime data received</p>
      )}
    </div>
  );
};

export default MarketRegime;
