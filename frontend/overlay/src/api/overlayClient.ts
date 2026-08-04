import type { OverlayFrame } from "../types";

// Uses the `ws` package in Electron (browser WebSocket lacks auth/custom
// headers and can't reach the backend's token the same way). The overlay
// appends ?token= for auth; the backend upgrades if the user exists.
export type FrameHandler = (frame: OverlayFrame) => void;

export interface OverlayClientOptions {
  origin: string;
  userId: string;
  token: string;
  onFrame: FrameHandler;
  onStatus?: (connected: boolean) => void;
  onError?: (err: unknown) => void;
}

export class OverlayClient {
  private ws: WebSocket | null = null;
  private readonly opts: OverlayClientOptions;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;

  constructor(opts: OverlayClientOptions) {
    this.opts = opts;
  }

  connect(): void {
    this.closedByUser = false;
    const url = `${this.opts.origin.replace(/^http/, "ws")}/api/v1/modules/realtime/ws/overlay/${this.opts.userId}?token=${encodeURIComponent(this.opts.token)}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => this.opts.onStatus?.(true);
    ws.onmessage = (ev: MessageEvent) => {
      try {
        const frame = JSON.parse(ev.data as string) as OverlayFrame;
        this.opts.onFrame(frame);
      } catch {
        // Ignore malformed frames.
      }
    };
    ws.onerror = (ev: Event) => this.opts.onError?.(ev);
    ws.onclose = () => {
      this.opts.onStatus?.(false);
      if (!this.closedByUser) this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 2000);
  }

  /** Send an inbound frame (e.g. a manual alert or current cast report). */
  send(frame: OverlayFrame): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame));
    }
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }
}
