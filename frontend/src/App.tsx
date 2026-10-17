import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { wsService } from './services/websocket';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import Watchlist from './pages/Watchlist';
import TradeHealth from './pages/TradeHealth';
import OpenTrades from './pages/OpenTrades';
import AIReasoning from './pages/AIReasoning';
import News from './pages/News';
import Portfolio from './pages/Portfolio';
import PaperTrading from './pages/PaperTrading';
import Performance from './pages/Performance';
import TradeJournal from './pages/TradeJournal';
import Alerts from './pages/Alerts';
import MarketRegime from './pages/MarketRegime';

const App: React.FC = () => {
  const [connected, setConnected] = useState(false);
  const [receivedEngineData, setReceivedEngineData] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleConnectionChange = (isConnected: boolean) => {
      setConnected(isConnected);
      if (!isConnected) {
        setReceivedEngineData(false);
      }
      setError(isConnected ? null : 'Failed to connect to real-time updates');
    };
    const handleActivity = () => setReceivedEngineData(true);

    wsService.onStatusChange(handleConnectionChange);
    wsService.onActivity(handleActivity);
    wsService.connect().catch((err) => {
      console.error('Failed to connect WebSocket:', err);
      handleConnectionChange(false);
    });

    return () => {
      wsService.offStatusChange(handleConnectionChange);
      wsService.offActivity(handleActivity);
      wsService.disconnect();
    };
  }, []);

  return (
    <BrowserRouter>
      <div className="flex h-screen bg-slate-900 text-slate-100">
        {/* Sidebar */}
        <Sidebar />

        {/* Main Content */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Connection Status Bar */}
          <div className="bg-slate-800 border-b border-slate-700 px-6 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  connected ? 'bg-emerald-500' : 'bg-red-500'
                }`}
              />
              <span className="text-sm text-slate-400">
                {connected ? 'Connected' : 'Disconnected'}
              </span>
            </div>
            {error ? (
              <span className="text-sm text-red-400">{error}</span>
            ) : connected && !receivedEngineData ? (
              <span className="text-sm text-amber-300">No engine data received yet</span>
            ) : null}
          </div>

          {/* Routes */}
          <div className="flex-1 overflow-auto">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/watchlist" element={<Watchlist />} />
              <Route path="/market-regime" element={<MarketRegime />} />
              <Route path="/trade-health" element={<TradeHealth />} />
              <Route path="/open-trades" element={<OpenTrades />} />
              <Route path="/ai-reasoning" element={<AIReasoning />} />
              <Route path="/news" element={<News />} />
              <Route path="/portfolio" element={<Portfolio />} />
              <Route path="/paper-trading" element={<PaperTrading />} />
              <Route path="/performance" element={<Performance />} />
              <Route path="/trade-journal" element={<TradeJournal />} />
              <Route path="/alerts" element={<Alerts />} />
            </Routes>
          </div>
        </main>
      </div>
    </BrowserRouter>
  );
};

export default App;
