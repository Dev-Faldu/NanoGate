import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, X } from "lucide-react";
import { get } from "../api/client";
import type { Receipt } from "../api/types";
import { fmtDateTime, fmtInt, fmtMs, fmtNum, fmtUsd } from "../lib/format";
import DecisionPath from "./DecisionPath";
import { DataClassBadge, Hash, KV, ReasonBadge, RouteBadge, StateView } from "./ui";

export default function ReceiptDrawer({ receiptId, onClose }: { receiptId: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["receipt", receiptId], queryFn: () => get<Receipt>(`/api/receipts/${receiptId}`), enabled: !!receiptId });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!receiptId) return null;
  const b = q.data?.body;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Decision receipt">
      <button className="absolute inset-0 bg-ink/10 backdrop-blur-[3px]" aria-label="Close receipt" onClick={onClose} />
      <div className="glass-drawer relative h-full w-full max-w-[640px] animate-slidein overflow-y-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-ink/5 bg-[#fbfaf8]/90 px-6 py-4 backdrop-blur-md">
          <div>
            <div className="eyebrow">Decision receipt</div>
            <div className="mono truncate text-ink">{receiptId}</div>
          </div>
          <div className="flex items-center gap-2">
            <Link to={`/requests/${receiptId}`} className="btn-ghost !py-1.5" onClick={onClose}>
              Full receipt <ArrowUpRight className="h-4 w-4" />
            </Link>
            <button className="btn-ghost !p-2" onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></button>
          </div>
        </div>
        {q.isLoading && <StateView kind="loading" />}
        {q.isError && <StateView kind="error" detail={String(q.error)} />}
        {b && (
          <div className="space-y-6 p-6">
            <div className="flex flex-wrap items-center gap-3">
              <ReasonBadge code={q.data!.reason} className="!text-[12px]" />
              <RouteBadge route={b.decision?.chosen_route} />
              <DataClassBadge value={b.classification?.data_class} />
              <span className="text-[12px] text-ink-3">{fmtDateTime(b.identity?.timestamp)}</span>
            </div>
            <DecisionPath timeline={b.timeline} compact />
            <KV cols={2} rows={[
              ["Department", `${b.identity?.tenant_id ?? "—"} / ${b.identity?.department ?? "—"}`],
              ["Policy", b.identity?.policy_version],
              ["Model", b.model?.model],
              ["Tokens", b.model ? `${fmtInt(b.model.prompt_tokens)} in · ${fmtInt(b.model.completion_tokens)} out` : "—"],
              ["TTFT", fmtMs(b.model?.ttft_ms)],
              ["Latency", fmtMs(b.integrity?.latency_ms)],
              ["p(error)", b.router?.p_error != null ? fmtNum(b.router.p_error, 4) : b.router ? "Unavailable" : "—"],
              ["Threshold", b.router?.threshold != null ? fmtNum(b.router.threshold, 4) : "—"],
              ["Cost", fmtUsd(b.economics?.actual_cost_usd)],
              ["Avoided (calc.)", fmtUsd(b.economics?.avoided_cost_usd)],
              ["Remote bytes", fmtInt(b.decision?.egress?.remote_bytes_out)],
              ["Receipt hash", <Hash value={q.data!.hash} />],
            ]} />
            <div className="flex flex-wrap gap-1.5">
              {(b.decision?.reason_codes ?? []).map((c: string) => <ReasonBadge key={c} code={c} />)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
