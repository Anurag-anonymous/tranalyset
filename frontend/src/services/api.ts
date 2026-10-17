export interface DashboardRuntimeData {
  paper_only: boolean;
  dataset: {
    name: string;
    identity: string;
    symbol: string;
    exchange: string;
    timeframe: string;
    timezone: string;
    candle_count: number;
    nonzero_volume_candle_count: number;
    start: string;
    end: string;
  } | null;
  symbol: string;
  timeframe: string;
  exchange: string;
  execution_assumptions: {
    initial_cash: number;
    commission_rate: number;
    slippage_rate: number;
    execution_mode: string;
  };
  plugin_count: number;
}

export async function fetchSnapshot<T>(path: string): Promise<T> {
  const response = await fetch(`/api/${path}`);
  if (!response.ok) {
    throw new Error(`Dashboard API request failed (${response.status}): /api/${path}`);
  }
  return response.json() as Promise<T>;
}
