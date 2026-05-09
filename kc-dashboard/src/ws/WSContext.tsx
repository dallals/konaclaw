import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { getBaseUrl } from "../api/client";
import { useApprovals } from "../store/approvals";

type Ctx = {
  send: (msg: unknown) => void;
  connected: boolean;
};
const WSContext = createContext<Ctx | null>(null);

export function WSProvider({ children }: { children: React.ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const { addRequest, resolveLocal } = useApprovals();

  useEffect(() => {
    const url = getBaseUrl().replace(/^http/, "ws") + "/ws/approvals";
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "approval_request") addRequest(msg);
    };
    return () => ws.close();
  }, [addRequest]);

  const send = (msg: unknown) => {
    wsRef.current?.send(JSON.stringify(msg));
  };
  // wrap resolveLocal so callers can do "respond and locally clear"
  (window as any).__kcResolveLocal = resolveLocal; // for the Permissions view

  return <WSContext.Provider value={{ send, connected }}>{children}</WSContext.Provider>;
}

export function useWS() {
  const v = useContext(WSContext);
  if (!v) throw new Error("useWS outside WSProvider");
  return v;
}
