import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, TradeHealthData } from '../services/websocket';

const TradeHealth: React.FC = () => {
  const [health, setHealth] = useState<Record<string, TradeHealthData>>({});

  useEffect(() => {
    const handleUpdate = (data: TradeHealthData) => {
      setHealth((prev) => ({ ...prev, [data.symbol]: data }));
    };

    wsService.on('trade-health-updated', handleUpdate);
    fetchSnapshot<TradeHealthData[]>('trade-health')
      .then((items) => setHealth(Object.fromEntries(items.map((item) => [item.symbol, item]))))
      .catch((error: unknown) => console.error('Failed to load trade health:', error));
    return () => wsService.off('trade-health-updated', handleUpdate);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Trade Health</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Object.entries(health).map(([symbol, data]) => (
          <div key={symbol} className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <h3 className="text-lg font-semibold text-slate-100">{symbol}</h3>
            <div className="mt-4 space-y-3">
              <div>
                <p className="text-sm text-slate-400">Health Score</p>
                <p className="text-2xl font-bold text-emerald-400">{data.health_score.toFixed(1)}/100</p>
              </div>
              <div>
                <p className="text-sm text-slate-400">Confidence Range</p>
                <p className="text-sm text-slate-200">
                  {data.confidence[0].toFixed(1)} - {data.confidence[1].toFixed(1)}
                </p>
              </div>
              <div>
                <p className="text-sm text-slate-400">Key Factors</p>
                <div className="text-sm text-slate-300 space-y-1 mt-2">
                  {Object.entries(data.factors).slice(0, 3).map(([factor, value]) => (
                    <p key={factor}>{factor}: {value.toFixed(2)}</p>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      {Object.keys(health).length === 0 && (
        <div className="text-center py-12 text-slate-400">No trade health data</div>
      )}
    </div>
  );
};

export default TradeHealth;
