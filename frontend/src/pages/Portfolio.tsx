import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, PortfolioData } from '../services/websocket';

const Portfolio: React.FC = () => {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);

  useEffect(() => {
    const handleUpdate = (data: PortfolioData) => {
      setPortfolio(data);
    };

    wsService.on('portfolio-updated', handleUpdate);
    fetchSnapshot<PortfolioData | null>('portfolio')
      .then((snapshot) => {
        if (snapshot) setPortfolio(snapshot);
      })
      .catch((error: unknown) => console.error('Failed to load portfolio:', error));
    return () => wsService.off('portfolio-updated', handleUpdate);
  }, []);

  if (!portfolio) {
    return <div className="p-6 text-center text-slate-400">No portfolio data</div>;
  }

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Portfolio</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Total Value</p>
          <p className="text-3xl font-bold text-emerald-400 mt-2">${portfolio.total_value.toFixed(2)}</p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Cash</p>
          <p className="text-3xl font-bold text-slate-200 mt-2">${portfolio.cash.toFixed(2)}</p>
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
          <p className="text-sm text-slate-400">Positions</p>
          <p className="text-3xl font-bold text-slate-200 mt-2">{Object.keys(portfolio.positions).length}</p>
        </div>
      </div>
      <div className="mt-6 bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Holdings</h2>
        <div className="space-y-2">
          {Object.entries(portfolio.positions).map(([symbol, quantity]) => (
            <div key={symbol} className="flex justify-between p-3 bg-slate-700 rounded">
              <span className="font-semibold text-slate-100">{symbol}</span>
              <span className="text-slate-300">{quantity} shares</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Portfolio;
