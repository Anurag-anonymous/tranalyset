import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, AIReasoningData } from '../services/websocket';

const AIReasoning: React.FC = () => {
  const [reasoning, setReasoning] = useState<AIReasoningData[]>([]);

  useEffect(() => {
    const handleReasoning = (data: AIReasoningData) => {
      setReasoning((prev) => [data, ...prev].slice(0, 50));
    };

    wsService.on('ai-reasoning', handleReasoning);
    fetchSnapshot<AIReasoningData[]>('ai-reasoning')
      .then(setReasoning)
      .catch((error: unknown) => console.error('Failed to load AI reasoning:', error));
    return () => wsService.off('ai-reasoning', handleReasoning);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">AI Reasoning</h1>
      <div className="space-y-4">
        {reasoning.map((item) => (
          <div key={item.signal_id} className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <div className="flex items-start justify-between mb-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-100">{item.symbol}</h3>
                <p className="text-xs text-slate-400 mt-1">Signal ID: {item.signal_id}</p>
              </div>
              <span className="px-3 py-1 bg-blue-900 text-blue-300 text-xs rounded">
                Reasoning
              </span>
            </div>
            <p className="text-slate-300">{item.reasoning}</p>
            {item.details && item.details.length > 0 && (
              <ul className="text-sm text-slate-400 mt-2 list-disc list-inside">
                {item.details.map((detail) => <li key={detail}>{detail}</li>)}
              </ul>
            )}
            <p className="text-xs text-slate-400 mt-3">{new Date(item.timestamp).toLocaleString()}</p>
          </div>
        ))}
      </div>
      {reasoning.length === 0 && (
        <div className="text-center py-12 text-slate-400">No reasoning available</div>
      )}
    </div>
  );
};

export default AIReasoning;
