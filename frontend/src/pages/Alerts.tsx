import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, AlertData } from '../services/websocket';

const Alerts: React.FC = () => {
  const [alerts, setAlerts] = useState<AlertData[]>([]);

  useEffect(() => {
    const handleAlert = (data: AlertData) => {
      setAlerts((prev) => [data, ...prev].slice(0, 100));
    };

    wsService.on('alert', handleAlert);
    fetchSnapshot<AlertData[]>('alerts')
      .then(setAlerts)
      .catch((error: unknown) => console.error('Failed to load alerts:', error));
    return () => wsService.off('alert', handleAlert);
  }, []);

  const getAlertColor = (level: string) => {
    switch (level) {
      case 'error':
        return 'bg-red-900 text-red-300 border-red-700';
      case 'warning':
        return 'bg-yellow-900 text-yellow-300 border-yellow-700';
      default:
        return 'bg-blue-900 text-blue-300 border-blue-700';
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Alerts</h1>
      <div className="space-y-3">
        {alerts.map((alert) => (
          <div
            key={alert.id}
            className={`rounded-lg border p-4 ${getAlertColor(alert.level)}`}
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="font-semibold">{alert.message}</p>
                <p className="text-xs mt-1 opacity-75">{new Date(alert.timestamp).toLocaleString()}</p>
              </div>
              <span className="text-xs uppercase font-bold">{alert.level}</span>
            </div>
          </div>
        ))}
      </div>
      {alerts.length === 0 && (
        <div className="text-center py-12 text-slate-400">No alerts</div>
      )}
    </div>
  );
};

export default Alerts;
