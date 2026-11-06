// One WebSocket to the brain, with auto-reconnect and a tiny pub/sub on top.
import "./bridge";
import { backendUrl } from "./bridge";
import type { BrainEvent, ClientMessage } from "./protocol";

type Handler = (e: BrainEvent) => void;

class BrainSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private queue: string[] = [];
  private retry = 500;
  connected = false;
  private statusHandlers = new Set<(up: boolean) => void>();

  connect(): void {
    const url = backendUrl.replace(/^http/, "ws") + "/ws";
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.retry = 500;
      this.statusHandlers.forEach((fn) => fn(true));
      this.queue.splice(0).forEach((m) => ws.send(m));
    };
    ws.onmessage = (m) => {
      const event = JSON.parse(m.data as string) as BrainEvent;
      this.handlers.forEach((fn) => fn(event));
    };
    ws.onclose = () => {
      this.connected = false;
      // A question or recording from before the drop would be answered minutes late: keep only
      // settings changes.
      this.queue = this.queue.filter((m) => m.startsWith('{"type":"set"'));
      this.statusHandlers.forEach((fn) => fn(false));
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 8000);
    };
  }

  send(msg: ClientMessage): void {
    const text = JSON.stringify(msg);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(text);
    else this.queue.push(text);
  }

  on(fn: Handler): () => void {
    this.handlers.add(fn);
    return () => this.handlers.delete(fn);
  }

  /** Feed an event as if the brain sent it (browser previews and UI tests only). */
  inject(event: BrainEvent): void {
    this.handlers.forEach((fn) => fn(event));
  }

  onStatus(fn: (up: boolean) => void): () => void {
    this.statusHandlers.add(fn);
    return () => this.statusHandlers.delete(fn);
  }
}

export const brain = new BrainSocket();
// The desktop app always connects. A plain browser preview only connects to a running brain
// when asked (?live=1), so a forgotten preview tab can never talk over the real app.
if (window.pyxiBridge || new URLSearchParams(location.search).get("live") === "1") brain.connect();
// Test hook: the browser preview and dev builds of the app can inject events (never in production).
if (!window.pyxiBridge || import.meta.env.DEV) (window as unknown as { __pyxi: BrainSocket }).__pyxi = brain;
