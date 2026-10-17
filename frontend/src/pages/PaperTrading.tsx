import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { DashboardRuntimeData, fetchSnapshot } from '../services/api';

interface ReplayStatus {
  state: string;
  progress: number;
  started: boolean;
  dataset: DashboardRuntimeData['dataset'];
  replay_time: string | null;
}

const PaperTrading: React.FC = () => {
  const [runtime, setRuntime] = useState<DashboardRuntimeData | null>(null);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshStatus = async () => {
    const status = await fetchSnapshot<ReplayStatus>('replay/status');
    setReplay(status);
  };

  useEffect(() => {
    fetchSnapshot<DashboardRuntimeData>('runtime')
      .then(setRuntime)
      .catch((loadError: unknown) => {
        console.error('Failed to load runtime configuration:', loadError);
        setError('Unable to load engine configuration.');
      });
    refreshStatus().catch((loadError: unknown) => {
      console.error('Failed to load replay status:', loadError);
      setError('Unable to load replay status.');
    });
    const timer = window.setInterval(() => {
      refreshStatus().catch((loadError: unknown) =>
        console.error('Failed to refresh replay status:', loadError),
      );
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const runAction = async (action: string) => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/replay/${action}`, { method: 'POST' });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail ?? `Replay request failed (${response.status})`);
      }
      setReplay(await response.json() as ReplayStatus);
    } catch (actionError) {
      console.error(`Failed to ${action} replay:`, actionError);
      setError(actionError instanceof Error ? actionError.message : 'Replay action failed.');
    } finally {
      setBusy(false);
    }
  };

  const dataset = runtime?.dataset;
  const replayState = replay?.state ?? 'unknown';

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold text-emerald-400 mb-6">Paper Trading</h1>
      <div className="max-w-3xl bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h2 className="text-lg font-semibold text-slate-100 mb-3">Historical paper replay</h2>
        {dataset ? (
          <>
            <p className="text-slate-300">
              {dataset.name} · {dataset.symbol} {dataset.timeframe} · {dataset.candle_count} candles
            </p>
            <p className="text-sm text-slate-400 mt-1">
              {new Date(dataset.start).toLocaleString()} – {new Date(dataset.end).toLocaleString()}
            </p>
            <p className="text-xs text-slate-500 mt-1 break-all">
              Dataset SHA-256: {dataset.identity}
            </p>
            {dataset.nonzero_volume_candle_count === 0 && (
              <p className="mt-3 rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200">
                This dataset contains no nonzero volume observations. Volume-confirmed strategies
                will not produce entry candidates; volume is not inferred or fabricated.
              </p>
            )}
            <div className="mt-4 text-sm text-slate-300">
              Replay state: <span className="font-semibold text-emerald-300">{replayState}</span>
              {replay?.replay_time && ` · simulated time ${new Date(replay.replay_time).toLocaleString()}`}
              {replay && ` · ${(replay.progress * 100).toFixed(1)}%`}
            </div>
            <div className="flex flex-wrap gap-3 mt-5">
              {replayState === 'idle' && (
                <button disabled={busy} onClick={() => runAction('start')} className="rounded bg-emerald-700 px-4 py-2 text-white disabled:opacity-50">
                  {replay?.started ? 'Continue paper replay' : 'Start paper replay'}
                </button>
              )}
              {['idle', 'paused'].includes(replayState) && (
                <button disabled={busy} onClick={() => runAction('step')} className="rounded bg-slate-600 px-4 py-2 text-white disabled:opacity-50">
                  Step one candle
                </button>
              )}
              {replayState === 'playing' && (
                <>
                  <button disabled={busy} onClick={() => runAction('pause')} className="rounded bg-amber-700 px-4 py-2 text-white disabled:opacity-50">
                    Pause
                  </button>
                  <button disabled={busy} onClick={() => runAction('stop')} className="rounded bg-red-800 px-4 py-2 text-white disabled:opacity-50">
                    Stop
                  </button>
                </>
              )}
              {replayState === 'paused' && (
                <button disabled={busy} onClick={() => runAction('resume')} className="rounded bg-emerald-700 px-4 py-2 text-white disabled:opacity-50">
                  Resume
                </button>
              )}
            </div>
          </>
        ) : (
          <p className="text-slate-300">
            No historical dataset is configured. Set <code>TRANALYSET_CANDLE_CSV</code> before
            starting the API to load the NIFTY 5-minute candles.
          </p>
        )}
        {error && <p role="alert" className="mt-4 text-sm text-red-400">{error}</p>}
        <p className="text-xs text-slate-500 mt-5">
          Paper-only execution. Orders, fees, and slippage are simulated; no broker order endpoint is used.
        </p>
        <div className="flex gap-4 mt-5 text-emerald-300">
          <Link to="/portfolio" className="underline">Paper portfolio</Link>
          <Link to="/performance" className="underline">Paper performance</Link>
          <Link to="/trade-journal" className="underline">Trade journal</Link>
        </div>
      </div>
    </div>
  );
};

export default PaperTrading;
