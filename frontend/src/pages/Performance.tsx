import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, PerformanceData } from '../services/websocket';

const Performance: React.FC = () => {
  const [perf, setPerf] = useState<PerformanceData | null>(null);

  useEffect(() => {
    const handleUpdate = (data: PerformanceData) => {
      setPerf(data);
    };

    wsService.on('performance-updated', handleUpdate);
    fetchSnapshot<PerformanceData | null>('performance')
      .then((snapshot) => {
        if (snapshot) setPerf(snapshot);
      })
      .catch((error: unknown) => console.error('Failed to load performance:', error));
    return () => wsService.off('performance-updated', handleUpdate);
  }, []);

  if (!perf) {
    return (
      <div className="p-6 text-center text-slate-400">
        <h1 className="text-3xl font-bold text-emerald-400 mb-4">Paper Performance</h1>
        No paper-performance report has been received from the execution engine.
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-2">Paper Performance</h1>
      <p className="text-sm text-slate-400 mb-6">
        {perf.period} report · updated {new Date(perf.timestamp).toLocaleString()}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Period Return</p>
          <p className={`text-3xl font-bold mt-2 ${perf.total_return > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {perf.total_return > 0 ? '+' : ''}{perf.total_return.toFixed(2)}%
          </p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Equity</p>
          <p className="text-3xl font-bold text-slate-200 mt-2">${perf.equity.toFixed(2)}</p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Period P&amp;L</p>
          <p className="text-3xl font-bold text-slate-200 mt-2">${perf.pnl.toFixed(2)}</p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Drawdown</p>
          <p className="text-3xl font-bold text-red-400 mt-2">{perf.drawdown.toFixed(2)}%</p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Closed Trades</p>
          <p className="text-3xl font-bold text-slate-200 mt-2">{perf.trade_count.toFixed(0)}</p>
        </div>
      </div>
    </div>
  );
};

export default Performance;
