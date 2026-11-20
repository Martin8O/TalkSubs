// The live connection: one WebSocket, reconnected with a short backoff. The first frame of every
// connection is a snapshot, so a reconnect can never leave the window with a stale list.
import { useEffect, useReducer, useState } from "react";

import { apply, emptyQueue, type QueueState } from "../state/queue";
import type { ServerMessage } from "./contract.gen";

const RETRY_MS = [250, 500, 1000, 2000];
const OFFLINE_AFTER_MS = 1000; // a reload or a quick reconnect should not flash the banner

export interface Live {
  queue: QueueState;
  online: boolean;
}

export function useLive(): Live {
  const [queue, dispatch] = useReducer(apply, emptyQueue);
  const [online, setOnline] = useState(true);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let retry: number | undefined;
    let offline: number | undefined;

    const connect = () => {
      ws = new WebSocket(`ws://${location.host}/api/events`);
      ws.onmessage = (e: MessageEvent<string>) => {
        const msg = JSON.parse(e.data) as ServerMessage;
        if (msg.type === "snapshot") {
          attempt = 0;
          window.clearTimeout(offline);
          offline = undefined;
          setOnline(true);
        }
        dispatch(msg);
      };
      ws.onclose = () => {
        if (stopped) return;
        offline ??= window.setTimeout(() => setOnline(false), OFFLINE_AFTER_MS);
        retry = window.setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      window.clearTimeout(offline);
      ws?.close();
    };
  }, []);

  return { queue, online };
}
