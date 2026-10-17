import React from 'react';
import { Link, useLocation } from 'react-router-dom';

const Sidebar: React.FC = () => {
  const location = useLocation();

  const navItems = [
    { path: '/', label: 'Dashboard', icon: '📊' },
    { path: '/watchlist', label: 'Watchlist', icon: '👁️' },
    { path: '/market-regime', label: 'Market Regime', icon: '📈' },
    { path: '/trade-health', label: 'Trade Health', icon: '❤️' },
    { path: '/open-trades', label: 'Open Trades', icon: '🔓' },
    { path: '/ai-reasoning', label: 'AI Reasoning', icon: '🤖' },
    { path: '/news', label: 'News', icon: '📰' },
    { path: '/portfolio', label: 'Portfolio', icon: '💼' },
    { path: '/paper-trading', label: 'Paper Trading', icon: '📝' },
    { path: '/performance', label: 'Performance', icon: '⚡' },
    { path: '/trade-journal', label: 'Trade Journal', icon: '📓' },
    { path: '/alerts', label: 'Alerts', icon: '🔔' },
  ];

  return (
    <aside className="w-64 bg-slate-800 border-r border-slate-700 overflow-y-auto">
      <div className="p-6">
        <h1 className="text-2xl font-bold text-emerald-400">Tranalyset</h1>
        <p className="text-xs text-slate-400 mt-1">Trading Dashboard</p>
      </div>

      <nav className="px-3 py-4 space-y-2">
        {navItems.map((item) => (
          <Link
            key={item.path}
            to={item.path}
            className={`flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
              location.pathname === item.path
                ? 'bg-emerald-900 text-emerald-100'
                : 'text-slate-300 hover:bg-slate-700'
            }`}
          >
            <span className="text-lg">{item.icon}</span>
            <span className="text-sm font-medium">{item.label}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
};

export default Sidebar;
