type EventHandler<T> = (data: T) => void;

interface EventMap {
  'watchlist-updated': WatchlistData;
  'candle-updated': CandleUpdateData;
  'market-regime-changed': MarketRegimeData;
  'trade-health-updated': TradeHealthData;
  'trade-opened': TradeData;
  'trade-updated': TradeData;
  'trade-closed': TradeData;
  'ai-reasoning': AIReasoningData;
  'news': NewsData;
  'portfolio-updated': PortfolioData;
  'performance-updated': PerformanceData;
  'alert': AlertData;
}

export interface WatchlistData {
  symbol: string;
  price: number;
  change: number;
  volume: number;
  timestamp: string;
}

export interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CandleUpdateData {
  symbol: string;
  timeframe: string;
  candle: CandleData;
}

export interface MarketRegimeData {
  symbol: string;
  regime: string;
  confidence: number;
  timestamp: string;
}

export interface TradeHealthData {
  symbol: string;
  health_score: number;
  confidence: [number, number];
  factors: Record<string, number>;
  timestamp: string;
}

export interface TradeData {
  trade_id: string;
  symbol: string;
  quantity: number;
  entry_price?: number;
  exit_price?: number;
  pnl?: number;
  last_price?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  commissions?: number;
  slippage?: number;
  status?: string;
  timestamp: string;
}

export interface AIReasoningData {
  signal_id: string;
  symbol: string;
  reasoning: string;
  timestamp: string;
  details?: string[];
}

export interface NewsData {
  id: string;
  title: string;
  source: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  timestamp: string;
  symbols: string[];
}

export interface PortfolioData {
  total_value: number;
  cash: number;
  positions: Record<string, number>;
  timestamp: string;
}

export interface PerformanceData {
  period: string;
  equity: number;
  pnl: number;
  total_return: number;
  drawdown: number;
  trade_count: number;
  timestamp: string;
}

export interface AlertData {
  id: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  timestamp: string;
}

export class WebSocketService {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Map<string, Set<EventHandler<any>>> = new Map();
  private statusHandlers: Set<(connected: boolean) => void> = new Set();
  private activityHandlers: Set<() => void> = new Set();
  private connected = false;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 3000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = true;

  constructor(url: string = 'ws://localhost:8000/ws') {
    this.url = url;
  }

  connect(): Promise<void> {
    this.shouldReconnect = true;
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectAttempts = 0;
          this.notifyStatus(true);
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            if (typeof message.type !== 'string') {
              return;
            }
            const { type, data } = message;
            if (type !== 'connection-established') {
              this.activityHandlers.forEach((handler) => handler());
            }
            this.emit(type, data);
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err);
          }
        };

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          reject(error);
        };

        this.ws.onclose = () => {
          console.log('WebSocket disconnected');
          this.notifyStatus(false);
          if (this.shouldReconnect) {
            this.attemptReconnect();
          }
        };
      } catch (err) {
        reject(err);
      }
    });
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      console.log(`Attempting reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect().catch(console.error);
      }, this.reconnectDelay);
    }
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.notifyStatus(false);
  }

  onStatusChange(handler: (connected: boolean) => void): void {
    this.statusHandlers.add(handler);
  }

  offStatusChange(handler: (connected: boolean) => void): void {
    this.statusHandlers.delete(handler);
  }

  onActivity(handler: () => void): void {
    this.activityHandlers.add(handler);
  }

  offActivity(handler: () => void): void {
    this.activityHandlers.delete(handler);
  }

  private notifyStatus(connected: boolean): void {
    if (this.connected === connected) {
      return;
    }
    this.connected = connected;
    this.statusHandlers.forEach((handler) => handler(connected));
  }

  on<K extends keyof EventMap>(event: K, handler: EventHandler<EventMap[K]>): void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
  }

  off<K extends keyof EventMap>(event: K, handler: EventHandler<EventMap[K]>): void {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.delete(handler);
    }
  }

  private emit<K extends keyof EventMap>(event: K, data: EventMap[K]): void {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(data);
        } catch (err) {
          console.error(`Error in ${event} handler:`, err);
        }
      });
    }
  }

  send(message: Record<string, any>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket not connected');
    }
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export const wsService = new WebSocketService();
