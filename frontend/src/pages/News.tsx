import React, { useEffect, useState } from 'react';
import { fetchSnapshot } from '../services/api';
import { wsService, NewsData } from '../services/websocket';

const News: React.FC = () => {
  const [news, setNews] = useState<NewsData[]>([]);

  useEffect(() => {
    const handleNews = (data: NewsData) => {
      setNews((prev) => [data, ...prev].slice(0, 50));
    };

    wsService.on('news', handleNews);
    fetchSnapshot<NewsData[]>('news')
      .then(setNews)
      .catch((error: unknown) => console.error('Failed to load news:', error));
    return () => wsService.off('news', handleNews);
  }, []);

  const getSentimentColor = (sentiment: string) => {
    switch (sentiment) {
      case 'positive':
        return 'text-emerald-400 bg-emerald-900';
      case 'negative':
        return 'text-red-400 bg-red-900';
      default:
        return 'text-slate-400 bg-slate-700';
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">News</h1>
      <div className="space-y-4">
        {news.map((item) => (
          <div key={item.id} className="bg-slate-800 rounded-lg border border-slate-700 p-6">
            <div className="flex items-start justify-between mb-3">
              <div className="flex-1">
                <h3 className="text-lg font-semibold text-slate-100">{item.title}</h3>
                <p className="text-sm text-slate-400 mt-1">{item.source}</p>
              </div>
              <span className={`px-3 py-1 text-xs rounded ${getSentimentColor(item.sentiment)}`}>
                {item.sentiment}
              </span>
            </div>
            <div className="flex items-center justify-between mt-3">
              <p className="text-xs text-slate-400">
                Symbols: {item.symbols.join(', ')}
              </p>
              <p className="text-xs text-slate-400">{new Date(item.timestamp).toLocaleString()}</p>
            </div>
          </div>
        ))}
      </div>
      {news.length === 0 && (
        <div className="text-center py-12 text-slate-400">No news available</div>
      )}
    </div>
  );
};

export default News;
