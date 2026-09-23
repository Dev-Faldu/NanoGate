/** Live event stream (SSE). Events originate only from real backend state changes. */
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getToken } from "./client";
import type { GatewayEvent } from "./types";

type ConnState = "connecting" | "live" | "reconnecting" | "offline";

interface EventsCtx {
  state: ConnState;
  events: GatewayEvent[];
  lastTelemetry: Record<string, any> | null;
  subscribe: (fn: (e: GatewayEvent) => void) => () => void;
}

const Ctx = createContext<EventsCtx>({ state: "offline", events: [], lastTelemetry: null, subscribe: () => () => {} });

export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<ConnState>("connecting");
  const [events, setEvents] = useState<GatewayEvent[]>([]);
  const [lastTelemetry, setTelemetry] = useState<Record<string, any> | null>(null);
  const listeners = useRef(new Set<(e: GatewayEvent) => void>());
  const lastSeq = useRef(0);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;
    const connect = () => {
      const token = getToken();
      if (!token) {
        setState("offline");
        return;
      }
      es = new EventSource(`/api/events?since=${lastSeq.current}&token=${encodeURIComponent(token)}`);
      es.onopen = () => setState("live");
      es.onerror = () => {
        setState("reconnecting");
        es?.close();
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
      es.onmessage = () => {};
      const handle = (msg: MessageEvent) => {
        const ev = JSON.parse(msg.data) as GatewayEvent;
        if (ev.seq <= lastSeq.current) return;
        lastSeq.current = ev.seq;
        if (ev.type === "telemetry_updated") {
          setTelemetry(ev.data);
        } else {
          setEvents((prev) => [ev, ...prev].slice(0, 300));
        }
        if (ev.type === "receipt_sealed") {
          qc.invalidateQueries({ queryKey: ["overview"] });
          qc.invalidateQueries({ queryKey: ["requests"] });
          qc.invalidateQueries({ queryKey: ["finops"] });
        }
        if (ev.type === "service_state_changed" || ev.type === "policy_published") {
          qc.invalidateQueries({ queryKey: ["status"] });
          qc.invalidateQueries({ queryKey: ["policies"] });
          qc.invalidateQueries({ queryKey: ["cache"] });
        }
        listeners.current.forEach((fn) => fn(ev));
      };
      // Named SSE events: attach a generic listener for every type we render.
      const types = ["request_started", "request_classified", "cache_candidate_found", "cache_verified", "cache_miss",
        "local_inference_started", "token_stream_started", "local_inference_completed", "router_scored", "route_selected",
        "output_scanned", "budget_reserved", "budget_settled", "receipt_sealed", "telemetry_updated",
        "service_state_changed", "policy_published", "offline_verification", "stage:identity", "stage:rate_limit",
        "stage:dlp", "stage:policy", "stage:transform", "stage:retrieval", "stage:budget", "stage:cache",
        "stage:inference", "stage:router", "stage:route", "stage:egress", "stage:output_scan", "stage:settle",
        "stage:cache_write", "stage:receipt"];
      types.forEach((t) => es!.addEventListener(t, handle as EventListener));
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      es?.close();
    };
  }, [qc]);

  const subscribe = (fn: (e: GatewayEvent) => void) => {
    listeners.current.add(fn);
    return () => listeners.current.delete(fn);
  };

  return <Ctx.Provider value={{ state, events, lastTelemetry, subscribe }}>{children}</Ctx.Provider>;
}

export const useEvents = () => useContext(Ctx);
