import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, UTCTimestamp } from 'lightweight-charts';
import { fetchSnapshot } from '../services/api';
import { CandleData, wsService } from '../services/websocket';

interface ChartProps {
  symbol: string;
  timeframe?: string;
}

const Chart: React.FC<ChartProps> = ({ symbol, timeframe = '5m' }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const emptyStateRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let active = true;

    const chart = createChart(containerRef.current, {
      layout: {
        textColor: '#cbd5e1',
        background: { type: ColorType.Solid, color: '#1e293b' },
      },
      width: containerRef.current.clientWidth,
      height: 400,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
      borderUpColor: '#10b981',
      wickDownColor: '#ef4444',
      wickUpColor: '#10b981',
    });

    const toChartCandle = (candle: CandleData) => {
      const timestamp = Date.parse(candle.time);
      if (
        !Number.isFinite(timestamp)
        || ![candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)
      ) {
        return null;
      }
      return {
        time: Math.floor(timestamp / 1000) as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      };
    };
    const pendingUpdates = new Map<string, CandleData>();
    let initialized = false;

    const handleCandle = (update: { symbol: string; timeframe: string; candle: CandleData }) => {
      if (update.symbol !== symbol || update.timeframe !== timeframe) return;
      if (!toChartCandle(update.candle)) return;
      if (!initialized) {
        pendingUpdates.set(update.candle.time, update.candle);
        return;
      }
      const candle = toChartCandle(update.candle);
      if (!candle) return;
      candlestickSeries.update(candle);
      if (emptyStateRef.current) emptyStateRef.current.hidden = true;
    };
    wsService.on('candle-updated', handleCandle);
    fetchSnapshot<CandleData[]>(
      `candles/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe)}`,
    )
      .then((candles) => {
        if (!active) return;
        const mergedCandles = new Map(candles.map((candle) => [candle.time, candle]));
        pendingUpdates.forEach((candle, time) => mergedCandles.set(time, candle));
        const chartCandles = [...mergedCandles.values()]
          .map(toChartCandle)
          .filter((candle) => candle !== null)
          .sort((left, right) => left.time - right.time);
        candlestickSeries.setData(chartCandles);
        initialized = true;
        chart.timeScale().fitContent();
        if (emptyStateRef.current) emptyStateRef.current.hidden = chartCandles.length > 0;
      })
      .catch((error: unknown) => console.error(`Failed to load ${symbol} candles:`, error));

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      active = false;
      wsService.off('candle-updated', handleCandle);
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [symbol, timeframe]);

  return (
    <div className="relative">
      <div ref={containerRef} className="w-full rounded-lg bg-slate-700" />
      <p
        ref={emptyStateRef}
        className="absolute inset-0 flex items-center justify-center text-slate-400"
      >
        No candle data available for {symbol}
      </p>
    </div>
  );
};

export default Chart;
