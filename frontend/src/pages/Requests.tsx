import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Filter, X } from "lucide-react";
import { download, get } from "../api/client";
import type { RequestsResp } from "../api/types";
import { Badge, Card, DataClassBadge, PageHeader, ReasonBadge, RouteBadge, StateView } from "../components/ui";
import { fmtDateTime, fmtInt, fmtMs, fmtUsd, ROUTE_LABEL } from "../lib/format";

const FILTERS: { key: string; label: string; facet: string }[] = [
  { key: "department", label: "Department", facet: "department_id" },
  { key: "data_class", label: "Data class", facet: "data_class" },
  { key: "route", label: "Route", facet: "route" },
  { key: "reason", label: "Reason", facet: "reason" },
  { key: "status", label: "Outcome", facet: "status" },
  { key: "cache", label: "Cache", facet: "cache_status" },
];

const TIME = [
  { label: "All time", s: 0 }, { label: "Last hour", s: 3600 }, { label: "Last 24h", s: 86400 }, { label: "Last 7d", s: 604800 },
];

export default function Requests() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [page, setPage] = useState(0);
  const [range, setRange] = useState(0);
  const limit = 50;
  const qs = useMemo(() => {
    const p = new URLSearchParams();
    FILTERS.forEach((f) => sp.get(f.key) && p.set(f.key, sp.get(f.key)!));
    if (sp.get("q")) p.set("q", sp.get("q")!);
    if (range) p.set("since", String(Date.now() / 1000 - range));
    p.set("limit", String(limit));
    p.set("offset", String(page * limit));
    return p.toString();
  }, [sp, page, range]);
  const q = useQuery({ queryKey: ["requests", qs], queryFn: () => get<RequestsResp>(`/api/requests?${qs}`) });
  const active = FILTERS.filter((f) => sp.get(f.key));
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n);
    setPage(0);
  };

  return (
    <>
      <PageHeader eyebrow="Audit" title="Requests" subtitle="Every request the gateway has decided — answered or denied — with its sealed receipt."
        right={<>
          <button className="btn-ghost" onClick={() => download(`/api/export/requests.csv${range ? `?since=${Math.floor(Date.now() / 1000 - range)}` : ""}`, "nanogate-requests.csv")}>
            <Download className="h-4 w-4" />Export CSV</button>
          <button className="btn-ghost" onClick={() => download(`/api/export/receipts.jsonl${range ? `?since=${Math.floor(Date.now() / 1000 - range)}` : ""}`, "nanogate-receipts.jsonl")}>
            <Download className="h-4 w-4" />Receipts</button>
        </>} />
      <Card className="mb-6 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="h-4 w-4 text-ink-3" />
          {FILTERS.map((f) => (
            <select key={f.key} aria-label={f.label} className="input !w-auto !py-1.5 !text-[13px]" value={sp.get(f.key) ?? ""}
              onChange={(e) => set(f.key, e.target.value)}>
              <option value="">{f.label}: all</option>
              {(q.data?.facets[f.facet] ?? []).map((v) => <option key={v} value={v}>{f.key === "route" ? ROUTE_LABEL[v] ?? v : v}</option>)}
            </select>
          ))}
          <select aria-label="Time range" className="input !w-auto !py-1.5 !text-[13px]" value={range} onChange={(e) => { setRange(Number(e.target.value)); setPage(0); }}>
            {TIME.map((t) => <option key={t.s} value={t.s}>{t.label}</option>)}
          </select>
          {sp.get("q") && <Badge tone="info">search: {sp.get("q")} <button aria-label="Clear search" onClick={() => set("q", "")}><X className="h-3 w-3" /></button></Badge>}
          {active.length > 0 && <button className="btn-ghost !py-1 !text-xs" onClick={() => { setSp(new URLSearchParams()); setPage(0); }}>Clear filters</button>}
          <span className="ml-auto text-[12.5px] text-ink-3 num">{q.data ? `${fmtInt(q.data.total)} requests` : ""}</span>
        </div>
      </Card>
      <Card>
        {q.isLoading ? <StateView kind="loading" /> : q.isError ? <StateView kind="error" detail={String(q.error)} /> :
          q.data!.rows.length === 0 ? <StateView kind="empty" title="No matching requests" /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px]" data-testid="requests-table">
                <thead className="border-b border-hair">
                  <tr>{["Time", "Department", "Intent", "Classification", "Route", "p(error)", "Latency", "Tokens", "Cost", "Status"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr>
                </thead>
                <tbody>
                  {q.data!.rows.map((r) => (
                    <tr key={r.request_id} tabIndex={0} className="cursor-pointer border-b border-hair transition-colors duration-150 last:border-0 hover:bg-white/80"
                      onClick={() => nav(`/requests/${r.receipt_id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/requests/${r.receipt_id}`)}>
                      <td className="table-cell num text-ink-2">{fmtDateTime(r.ts)}</td>
                      <td className="table-cell"><span className="capitalize">{r.department_id}</span> <span className="text-ink-3">· {r.tenant_id}</span></td>
                      <td className="table-cell text-ink-2">{r.intent ?? "—"}</td>
                      <td className="table-cell"><DataClassBadge value={r.data_class} /></td>
                      <td className="table-cell"><RouteBadge route={r.route} /></td>
                      <td className="table-cell num">{r.p_error != null ? r.p_error.toFixed(3) : "—"}</td>
                      <td className="table-cell num">{fmtMs(r.latency_ms)}</td>
                      <td className="table-cell num">{r.status === "ok" ? fmtInt((r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0)) : "—"}</td>
                      <td className="table-cell num">{r.status === "ok" ? fmtUsd(r.cost_usd) : "—"}</td>
                      <td className="table-cell"><div className="flex items-center gap-2">
                        <Badge tone={r.status === "ok" ? "good" : r.status === "denied" ? "warn" : "bad"}>{r.status === "ok" ? "Answered" : r.status === "denied" ? `Denied ${r.http_status}` : `Error ${r.http_status}`}</Badge>
                        <ReasonBadge code={r.reason} />
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        {q.data && q.data.total > limit && (
          <div className="flex items-center justify-end gap-2 border-t border-hair px-4 py-3">
            <button className="btn-ghost !p-2" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></button>
            <span className="text-[12.5px] text-ink-2 num">Page {page + 1} of {Math.ceil(q.data.total / limit)}</span>
            <button className="btn-ghost !p-2" disabled={(page + 1) * limit >= q.data.total} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></button>
          </div>
        )}
      </Card>
    </>
  );
}
