import type { ControlCommand, TwinState } from "../types/api";

export type SocketStatus = "connecting" | "open" | "closed";

export interface SocketHandlers {
  onState: (s: TwinState) => void;
  onStatus: (s: SocketStatus) => void;
  onLatency?: (ms: number) => void;
}

/** WebSocket to /ws/twin with automatic reconnect and round-trip latency probe. */
export class TwinSocket {
  private ws: WebSocket | null = null;
  private retry = 0;
  private closed = false;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private url: string,
    private h: SocketHandlers,
  ) {}

  connect(): void {
    this.closed = false;
    this.h.onStatus("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.h.onStatus("open");
      this.pingTimer = setInterval(() => this.send({ type: "ping", t: performance.now() }), 2000);
    };
    ws.onmessage = (ev) => this.handle(String(ev.data));
    ws.onclose = () => {
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.h.onStatus("closed");
      if (!this.closed) {
        const delay = Math.min(5000, 300 * 2 ** this.retry++);
        setTimeout(() => this.connect(), delay);
      }
    };
  }

  handle(raw: string): void {
    const msg = JSON.parse(raw);
    if (msg.type === "twin_state") this.h.onState(msg.data as TwinState);
    else if (msg.type === "pong" && typeof msg.t === "number")
      this.h.onLatency?.(performance.now() - msg.t);
  }

  private send(obj: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  sendControl(cmd: ControlCommand): boolean {
    return this.send({ type: "control", data: cmd });
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}

export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/twin`;
}
