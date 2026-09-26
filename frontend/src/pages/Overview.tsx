import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, BadgeDollarSign, Clock3, Cpu, DatabaseZap, Gauge, Layers, Lock, Timer,
} from "lucide-react";
import { get } from "../api/client";
import { useEvents } from "../api/events";
import type { Overview as OverviewT, Receipt, RequestRow, RequestsResp } from "../api/types";
import DecisionPath from "../components/DecisionPath";
import ReceiptDrawer from "../components/ReceiptDrawer";
import TryItPanel from "../components/TryItPanel";
import { Card, CardHeader, DataClassBadge, PageHeader, ReasonBadge, RouteBadge, Stat, StateView } from "../components/ui";
import { fmtBytes, fmtInt, fmtMs, fmtNum, fmtPct, fmtTime, fmtUsd } from "../lib/format";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

type StreamRow = Pick<RequestRow, "request_id" | "receipt_id" | "ts" | "department_id" | "intent" | "data_class" | "route" | "p_error" | "latency_ms" | "cost_usd" | "reason" | "status"> & { tokens: number | null };

export default function Overview() {
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => get<OverviewT>("/api/overview?window_h=24"), refetchInterval: 30_000 });
  const recent = useQuery({ queryKey: ["requests", "recent"], queryFn: () => get<RequestsResp>("/api/requests?limit=25") });
  const { subscribe, events } = useEvents();
  const [live, setLive] = useState<StreamRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [focusReceipt, setFocusReceipt] = useState<string | null>(null);

  useEffect(() => subscribe((e) => {
    if (e.type !== "receipt_sealed") return;
    const d = e.data;
    setLive((prev) => [{
      request_id: e.request_id!, receipt_id: d.receipt_id, ts: e.ts, department_id: d.department, intent: d.intent,
      data_class: d.data_class, route: d.route, p_error: d.p_error, latency_ms: d.latency_ms, cost_usd: d.cost_usd,
      reason: d.reason, status: d.status, tokens: d.tokens,
    }, ...prev].slice(0, 50));
    setFocusReceipt(d.receipt_id);
  }), [subscribe]);

  const rows: StreamRow[] = useMemo(() => {
    const base = (recent.data?.rows ?? []).map((r) => ({ ...r, tokens: (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0) }));
    const seen = new Set(live.map((r) => r.receipt_id));
    return [...live, ...base.filter((r) => !seen.has(r.receipt_id))].slice(0, 25);
  }, [recent.data, live]);

  const pathReceiptId = focusReceipt ?? rows[0]?.receipt_id ?? null;
  const path = useQuery({ queryKey: ["receipt", pathReceiptId], queryFn: () => get<Receipt>(`/api/receipts/${pathReceiptId}`), enabled: !!pathReceiptId });
  const inflight = events.find((e) => e.type === "request_started" && !events.some((x) => x.type === "receipt_sealed" && x.request_id === e.request_id));

  const o = ov.data;
  const safe = o && o.requests > 0 && o.sensitive_egress_bytes === 0 && o.errors === 0;
  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title={`${greeting()}, Dev`}
        subtitle={safe ? "Your AI traffic is safe, fast, and fully accountable."
          : "Every request is decided on this device and sealed with a verifiable receipt."}
      />

      <Card className="mb-6">
        {ov.isError ? <StateView kind="error" detail={String(ov.error)} /> : (
          <div className="grid grid-cols-2 divide-hair md:grid-cols-3 xl:grid-cols-5 [&>*]:border-b [&>*]:border-hair xl:[&>*:nth-last-child(-n+5)]:border-b-0">
            <Stat testid="kpi-requests" label="Requests (24h)" icon={<Activity className="h-4 w-4" />} prov="Measured"
              hint={{ text: "Every AI request that reached NanoGate in the last 24 hours, whether it was answered or blocked.", note: "Denied requests were stopped by policy, the data-leak scanner or a budget, and still got a receipt." }}
              value={o ? fmtInt(o.requests) : "…"} sub={o ? `${fmtInt(o.answered)} answered · ${fmtInt(o.denied)} denied` : ""} />
            <Stat label="Local coverage" icon={<Cpu className="h-4 w-4" />} prov="Measured"
              hint={{ text: "Share of answered requests that never left this device: answered by the local model, the larger local model, or a verified cached answer.", note: "Higher means more privacy and lower cost." }}
              value={o ? fmtPct(o.local_coverage) : "…"} sub="answered on-device (local, local-large, cache)" />
            <Stat label="Cost avoided" icon={<BadgeDollarSign className="h-4 w-4" />} prov="Measured"
              hint={{ text: "What these tokens would have cost at the reference hosted-API price, minus what they actually cost here.", note: "A calculation from real token counts and the price list in config/pricing.yaml, not an invoice." }}
              value={o ? fmtUsd(o.cost_avoided_usd) : "…"} sub={o?.cost_basis ? "vs. reference hosted-API rate" : ""} />
            <Stat label="Sensitive egress" icon={<Lock className="h-4 w-4" />} prov="Measured"
              hint={{ text: "Bytes of sensitive requests (personal or confidential data) sent to any remote service. It should stay at 0 B.", note: "Measured at the application layer by NanoGate's egress meter." }}
              tone={o && o.sensitive_egress_bytes > 0 ? "bad" : undefined}
              value={o ? fmtBytes(o.sensitive_egress_bytes) : "…"} sub={o ? `${fmtInt(o.sensitive_requests)} sensitive requests · app-layer meter` : ""} />
            <Stat label="Average latency" icon={<Clock3 className="h-4 w-4" />} prov="Measured"
              hint={{ text: "Average time from receiving a request to sending the full answer, including every check along the way.", note: "P50 is the typical request: half were faster." }}
              value={o ? fmtMs(o.latency_avg_ms) : "…"} sub={o ? `P50 ${fmtMs(o.latency_p50_ms)} · n=${o.latency_samples}` : ""} />
            <Stat label="P95 latency" icon={<Timer className="h-4 w-4" />} prov="Measured"
              hint={{ text: "95% of requests finished faster than this. It shows how slow the slow requests get." }}
              value={o ? fmtMs(o.latency_p95_ms) : "…"} sub={o ? `n=${o.latency_samples}` : ""} />
            <Stat label="Cache verification" icon={<DatabaseZap className="h-4 w-4" />} prov="Measured"
              hint={{ text: "How often a previous answer was safely reused. A cached answer is only reused after a second model checks that both questions really mean the same thing.", note: "Answers are never shared between tenants." }}
              value={o ? fmtPct(o.cache_verification_rate) : "…"} sub={o ? `${fmtInt(o.cache_hits)} verified of ${fmtInt(o.cache_lookups)} lookups` : ""} />
            <Stat label="Current queue" icon={<Layers className="h-4 w-4" />} prov="Live device telemetry"
              hint={{ text: "Requests waiting for the local model right now, and how many are being generated at this moment." }}
              value={o ? fmtInt(o.queue_depth) : "…"} sub={o ? `${fmtInt(o.in_flight)} generating now` : ""} />
            <Stat label="Model health" icon={<Gauge className="h-4 w-4" />} prov="Live device telemetry"
              hint={{ text: "Whether the local model on the ZGX Nano is loaded and answering, and its recent generation speed.", note: o && o.model.state !== "ready" ? "Unavailable: check the model servers with scripts/runtime.sh status (or zrt status)." : undefined }}
              tone={o && o.model.state !== "ready" ? "bad" : undefined}
              value={o ? (o.model.state === "ready" ? "Ready" : "Unavailable") : "…"}
              sub={o ? (o.model.state === "ready" ? `${o.model.name} · ${fmtNum(o.model.tokens_per_s, 1)} tok/s` : o.model.reason ?? o.model.name) : ""} />
            <Stat label="Tokens avoided" icon={<DatabaseZap className="h-4 w-4" />} prov="Measured"
              hint={{ text: "Tokens that did not have to be generated because a verified cached answer was reused." }}
              value={o ? fmtInt(o.tokens_avoided) : "…"} sub={o ? `${fmtInt(o.tokens_total)} tokens processed` : ""} />
          </div>
        )}
      </Card>

      <Card className="mb-6">
        <CardHeader eyebrow="Decision path" title="What NanoGate decided, stage by stage"
          subtitle={pathReceiptId ? <>Showing receipt <span className="mono">{pathReceiptId}</span>{inflight ? " · a new request is in flight" : ""}</> : "Send a request to see every stage evaluate in order."}
          right={pathReceiptId && <button className="btn-ghost !py-1.5" onClick={() => setSelected(pathReceiptId)}>Open receipt</button>} />
        <div className="p-6">
          <DecisionPath timeline={path.data?.body.timeline ?? null} />
          {path.data && (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-[12.5px] text-ink-2">
              <span>Outcome</span><ReasonBadge code={path.data.reason} /><RouteBadge route={path.data.body.decision?.chosen_route} />
              {path.data.body.router?.p_error != null && <span className="num">p(error) {fmtNum(path.data.body.router.p_error, 3)} vs threshold {fmtNum(path.data.body.router.threshold, 3)}</span>}
            </div>
          )}
        </div>
      </Card>

      <div>
        <Card>
          <CardHeader eyebrow="Decision stream" title="Live requests" subtitle="Rows appear when a receipt is sealed (server-sent events)." />
          {recent.isLoading ? <StateView kind="loading" /> : rows.length === 0 ? (
            <StateView kind="empty" title="No requests yet" detail="Press Try it (bottom right) to send a real request through the gateway, or point an OpenAI SDK at /v1." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px]" data-testid="decision-stream">
                <thead className="border-b border-hair">
                  <tr>
                    {["Time", "Dept", "Intent", "Class", "Route", "p(error)", "Latency", "Tokens", "Cost", "Reason"].map((h) => <th key={h} className="table-head">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.receipt_id} tabIndex={0} onClick={() => setSelected(r.receipt_id)} onKeyDown={(e) => e.key === "Enter" && setSelected(r.receipt_id)}
                      className="cursor-pointer border-b border-hair transition-colors duration-150 last:border-0 hover:bg-white/80 focus:bg-white">
                      <td className="table-cell num text-ink-2">{fmtTime(r.ts)}</td>
                      <td className="table-cell capitalize">{r.department_id}</td>
                      <td className="table-cell text-ink-2">{r.intent ?? "—"}</td>
                      <td className="table-cell"><DataClassBadge value={r.data_class} /></td>
                      <td className="table-cell"><RouteBadge route={r.route} /></td>
                      <td className="table-cell num">{r.p_error != null ? r.p_error.toFixed(3) : "—"}</td>
                      <td className="table-cell num">{fmtMs(r.latency_ms)}</td>
                      <td className="table-cell num">{r.tokens ? fmtInt(r.tokens) : "—"}</td>
                      <td className="table-cell num">{r.status === "ok" ? fmtUsd(r.cost_usd) : "—"}</td>
                      <td className="table-cell"><ReasonBadge code={r.reason} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      <TryItPanel onResult={(r) => r.headers["x-nanogate-receipt-id"] && setFocusReceipt(r.headers["x-nanogate-receipt-id"])} />
      <ReceiptDrawer receiptId={selected} onClose={() => setSelected(null)} />
    </>
  );
}
