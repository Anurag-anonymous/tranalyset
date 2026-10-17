import { WebSocketService } from './websocket';

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((error: Event) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;

  constructor() {
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }

  send(_data: string): void {
    // Mock implementation
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

global.WebSocket = MockWebSocket as any;

describe('WebSocketService', () => {
  let service: WebSocketService;

  beforeEach(() => {
    service = new WebSocketService('ws://test');
  });

  afterEach(() => {
    service.disconnect();
  });

  test('connects successfully', async () => {
    await service.connect();
    expect(service.isConnected()).toBe(true);
  });

  test('notifies connection status on connect and disconnect', async () => {
    const statusHandler = jest.fn();
    service.onStatusChange(statusHandler);

    await service.connect();
    service.disconnect();

    expect(statusHandler.mock.calls).toEqual([[true], [false]]);
  });

  test('reports application activity separately from the transport handshake', async () => {
    await service.connect();
    const activityHandler = jest.fn();
    service.onActivity(activityHandler);
    const ws = (service as any).ws;

    ws.onmessage({
      data: JSON.stringify({ type: 'connection-established', data: { status: 'connected' } }),
    });
    expect(activityHandler).not.toHaveBeenCalled();

    ws.onmessage({
      data: JSON.stringify({
        type: 'watchlist-updated',
        data: { symbol: 'AAPL', price: 150, change: 0, volume: 100, timestamp: '2026-01-15T10:00:00Z' },
      }),
    });
    expect(activityHandler).toHaveBeenCalledTimes(1);
  });

  test('subscribes to events', async () => {
    await service.connect();

    const handler = jest.fn();
    service.on('watchlist-updated', handler);

    // Simulate message
    const ws = (service as any).ws;
    ws.onmessage({
      data: JSON.stringify({
        type: 'watchlist-updated',
        data: { symbol: 'AAPL', price: 150, change: 2.5, volume: 1000000, timestamp: '2026-01-15T10:00:00Z' },
      }),
    });

    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: 'AAPL',
        price: 150,
        change: 2.5,
      })
    );
  });

  test('unsubscribes from events', async () => {
    await service.connect();

    const handler = jest.fn();
    service.on('watchlist-updated', handler);
    service.off('watchlist-updated', handler);

    // Simulate message
    const ws = (service as any).ws;
    ws.onmessage({
      data: JSON.stringify({
        type: 'watchlist-updated',
        data: { symbol: 'AAPL', price: 150, change: 2.5, volume: 1000000, timestamp: '2026-01-15T10:00:00Z' },
      }),
    });

    expect(handler).not.toHaveBeenCalled();
  });

  test('sends messages', async () => {
    await service.connect();

    const ws = (service as any).ws;
    ws.send = jest.fn();

    service.send({ type: 'subscribe', symbol: 'AAPL' });

    expect(ws.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', symbol: 'AAPL' })
    );
  });

  test('disconnects', async () => {
    await service.connect();
    expect(service.isConnected()).toBe(true);

    service.disconnect();
    expect(service.isConnected()).toBe(false);
  });
});
