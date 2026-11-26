"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

const WS_BASE = (process.env.NEXT_PUBLIC_WS_URL ?? "").replace(/\/$/, "");

function wsUrl(path: string) {
  if (WS_BASE) return `${WS_BASE}${path}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

/**
 * Subscribes to a backend WebSocket. A single-use ticket is fetched through the BFF
 * and sent as the first frame, so no long-lived token ever appears in a URL.
 * Reconnects with backoff unless the server closes normally or with a 44xx code.
 */
export function useBackendSocket<T>(path: string | null, onMessage: (msg: T) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onMessage);
  useEffect(() => {
    handler.current = onMessage;
  });

  useEffect(() => {
    if (!path) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      if (stopped) return;
      attempt++;
      timer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** attempt));
    };

    async function connect() {
      try {
        const { ticket } = await api.post<{ ticket: string }>("/auth/ws-ticket");
        if (stopped) return;
        ws = new WebSocket(wsUrl(`/api/v1${path}`));
        ws.onopen = () => {
          ws?.send(JSON.stringify({ type: "auth", ticket }));
          attempt = 0;
          setConnected(true);
        };
        ws.onmessage = (e) => {
          try {
            handler.current(JSON.parse(e.data) as T);
          } catch {
            /* ignore malformed frames */
          }
        };
        ws.onclose = (e) => {
          setConnected(false);
          if (stopped || e.code === 1000 || (e.code >= 4400 && e.code < 4500)) return;
          schedule();
        };
      } catch {
        schedule();
      }
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [path]);

  return connected;
}
