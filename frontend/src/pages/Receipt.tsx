import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft, BadgeCheck, BadgeDollarSign, Cpu, DatabaseZap, Fingerprint, Gauge, Hammer, Link2, Route as RouteIcon,
  ScanSearch, ShieldAlert, ShieldCheck, Waypoints,
} from "lucide-react";
import { get, post } from "../api/client";
import type { Receipt, Verification } from "../api/types";
import DecisionPath from "../components/DecisionPath";
import {
  Badge, Card, CardHeader, CopyButton, DataClassBadge, Hash, KV, ProvenanceTag, ReasonBadge, RouteBadge, StateView,
} from "../components/ui";
import { fmtBytes, fmtDateTime, fmtInt, fmtMs, fmtNum, fmtUsd, isNum } from "../lib/format";

function Section({ icon, title, children, right }: { icon: ReactNode; title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <Card>
      <CardHeader icon={icon} title={title} right={right} />
      <div className="p-6">{children}</div>
    </Card>
  );
}

export default function ReceiptPage() {
  const { receiptId } = useParams();
  const q = useQuery({ queryKey: ["receipt", receiptId], queryFn: () => get<Receipt>(`/api/receipts/${receiptId}`) });
  const verify = useMutation({ mutationFn: () => post<Verification>(`/api/receipts/${receiptId}/verify`) });
  const tamper = useMutation({ mutationFn: () => post<any>(`/api/receipts/${receiptId}/tamper-demo`, { field: "decision.chosen_route", value: "remote" }) });
  const [pulse, setPulse] = useState(0);

  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError || !q.data) return <StateView kind="error" title="Receipt not found" detail={String(q.error)} />;
  const r = q.data;
  const b = r.body;
  const v = verify.data;
  const sealed = v ? v.valid : null;

  return (
    <>
      <Link to="/requests" className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="h-4 w-4" /> Requests</Link>

      <Card className="mb-6 overflow-hidden">
        <div className="relative grid grid-cols-1 gap-8 p-8 lg:grid-cols-[1fr_auto]">
          <div>
            <div className="eyebrow mb-2">Decision receipt · #{fmtInt(r.seq)}</div>
            <h1 className="text-[34px] font-semibold tracking-[-0.02em] text-ink">Decision Receipt</h1>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="mono text-ink-2">{r.receipt_id}</span>
              <CopyButton text={r.receipt_id} label="Copy ID" />
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <ReasonBadge code={r.reason} className="!px-2.5 !py-1 !text-[13px]" />
              <RouteBadge route={b.decision?.chosen_route} />
              <DataClassBadge value={b.classification?.data_class} />
              <Badge tone={b.status === "ok" ? "good" : b.status === "denied" ? "warn" : "bad"}>HTTP {b.http_status}</Badge>
              <span className="text-[13px] text-ink-3">{fmtDateTime(b.identity?.timestamp)}</span>
            </div>
            {b.error && <div className="mt-4 rounded-xl bg-warn-soft px-4 py-2.5 text-[13px] text-warn">{b.error}</div>}
          </div>
          <div className="flex flex-col items-start gap-3 lg:items-end">
            <div key={pulse} className={clsx("flex items-center gap-3 rounded-2xl border px-5 py-4",
              sealed === null && "border-line bg-white/70", sealed === true && "animate-seal border-mint/30 bg-mint-soft",
              sealed === false && "border-danger/30 bg-danger-soft")} data-testid="seal-state">
              {sealed === false ? <ShieldAlert className="h-7 w-7 text-danger" /> : <BadgeCheck className={clsx("h-7 w-7", sealed ? "text-mint" : "text-ink-3")} />}
              <div>
                <div className={clsx("text-[18px] font-semibold tracking-tight", sealed === false ? "text-danger" : sealed ? "text-mint" : "text-ink")}>
                  {sealed === null ? "SEALED · not yet re-verified" : sealed ? "SEALED" : "VERIFICATION FAILED"}
                </div>
                <div className="text-[12px] text-ink-3">SHA-256 hash chain + HMAC-SHA256</div>
              </div>
            </div>
            <button className="btn-primary" data-testid="verify-receipt" onClick={() => { verify.mutate(); setPulse((p) => p + 1); }} disabled={verify.isPending}>
              <ShieldCheck className="h-4 w-4" /> Verify integrity
            </button>
          </div>
        </div>
        <div className="border-t border-hair bg-white/40 px-8 py-5"><DecisionPath timeline={b.timeline} /></div>
      </Card>

      {v && (
        <Card className="mb-6 p-6">
          <div className="eyebrow mb-3">Verification · {fmtDateTime(v.verified_at)}</div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {Object.entries(v.checks).map(([k, ok]) => (
              <div key={k} className={clsx("rounded-xl border px-3 py-2 text-[12.5px]", ok ? "border-mint/20 bg-mint-soft/60 text-mint" : "border-danger/20 bg-danger-soft text-danger")}>
                <div className="font-semibold">{ok ? "Pass" : "Fail"}</div>
                <div className="text-ink-2">{k.replace(/_/g, " ")}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Section icon={<Fingerprint className="h-4 w-4" />} title="Identity">
          <KV rows={[
            ["Request ID", <span className="mono">{b.identity?.request_id}</span>], ["Tenant", b.identity?.tenant_id],
            ["Tenant hash", <Hash value={b.identity?.tenant_hash} />], ["Department", b.identity?.department],
            ["Role", b.identity?.role], ["API key", b.identity?.key_id ? <span className="mono">{b.identity.key_id} · {b.identity.key_hash}</span> : "—"],
            ["Policy version", b.identity?.policy_version], ["Timestamp", fmtDateTime(b.identity?.timestamp)],
          ]} />
        </Section>

        <Section icon={<ScanSearch className="h-4 w-4" />} title="Classification">
          <KV rows={[
            ["Data class", <DataClassBadge value={b.classification?.data_class} />], ["Intent", b.classification?.intent],
            ["Secret detected", b.classification?.secret_detected == null ? "—" : b.classification.secret_detected ? "Yes" : "No"],
            ["Transformation", b.classification?.transformation ?? "none"],
            ["DLP layers", (b.classification?.dlp_layers ?? []).join(" · ") || "—"],
            ["Prompt hash", <Hash value={b.classification?.prompt_sha256} />], ["Raw prompt stored", b.classification?.raw_prompt_stored ? "Yes" : "No"],
            ["Entities", Object.entries(b.classification?.entity_counts ?? {}).map(([k, n]) => `${k}×${n}`).join(", ") || "none"],
          ]} />
          {(b.classification?.entities ?? []).length > 0 && (
            <div className="mt-4 overflow-x-auto rounded-xl border border-hair">
              <table className="w-full text-[12.5px]">
                <thead><tr className="border-b border-hair">{["Entity", "Offsets", "Layer", "Fingerprint"].map((h) => <th key={h} className="table-head !py-2">{h}</th>)}</tr></thead>
                <tbody>{b.classification.entities.map((e: any, i: number) => (
                  <tr key={i} className="border-b border-hair last:border-0">
                    <td className="px-4 py-2 font-medium">{e.type}</td><td className="px-4 py-2 num">{e.start}–{e.end}</td>
                    <td className="px-4 py-2">{e.layer}</td><td className="px-4 py-2"><span className="mono">{e.fingerprint}</span></td>
                  </tr>))}</tbody>
              </table>
              <div className="px-4 py-2 text-[11.5px] text-ink-3">Values are never stored — only type, offsets and a salted fingerprint.</div>
            </div>
          )}
        </Section>

        <Section icon={<ShieldCheck className="h-4 w-4" />} title="Policy">
          {b.policy ? (
            <>
              <KV rows={[
                ["Policy", b.policy.policy_version], ["Action", b.policy.action],
                ["Allowed routes", (b.policy.allowed_routes ?? []).join(", ") || "none"],
                ["Egress", b.policy.egress?.permitted ? "Permitted" : "Blocked"], ["Egress reason", b.policy.egress?.reason],
                ["Cache eligible", b.policy.cache_eligible ? "Yes" : "No"],
              ]} />
              {Object.keys(b.policy.denied_routes ?? {}).length > 0 && (
                <div className="mt-4 space-y-1.5">
                  {Object.entries(b.policy.denied_routes).map(([k, why]) => (
                    <div key={k} className="flex items-baseline justify-between gap-4 text-[12.5px]"><span className="font-medium text-ink">{k}</span><span className="text-right text-ink-3">{String(why)}</span></div>
                  ))}
                </div>
              )}
            </>
          ) : <StateView kind="empty" title="Policy not evaluated" detail="The request ended before policy evaluation (e.g. authentication failure)." />}
        </Section>

        <Section icon={<DatabaseZap className="h-4 w-4" />} title="Cache">
          {b.cache?.decision ? (
            <>
              <KV rows={[
                ["Decision", <ReasonBadge code={b.cache.decision} />], ["Similarity", fmtNum(b.cache.similarity, 4)],
                ["Verifier score", b.cache.verifier ? fmtNum(b.cache.verifier.score, 4) : "—"],
                ["Thresholds", b.cache.thresholds ? `retrieval ≥ ${b.cache.thresholds.retrieval} · verifier ≥ ${b.cache.thresholds.verifier}` : "—"],
                ["Cache ID", b.cache.entry?.cache_id ?? "—"], ["Tokens avoided", fmtInt(b.cache.tokens_avoided ?? 0)],
                ["Source", b.cache.entry?.source_id ? `${b.cache.entry.source_id} @ ${b.cache.entry.source_hash}` : "none"],
                ["Namespace", <span className="mono">{b.cache.namespace_id ?? "—"}</span>],
                ["Written entry", b.cache.written_cache_id ?? "—"],
              ]} />
              {(b.cache.evidence ?? []).length > 0 && (
                <ul className="mt-4 space-y-1.5 text-[12.5px] text-ink-2">{b.cache.evidence.map((e: string, i: number) => <li key={i} className="rounded-lg bg-white/70 px-3 py-2">{e}</li>)}</ul>
              )}
              {b.cache.verifier?.conflicts?.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">{b.cache.verifier.conflicts.map((c: string) => <Badge key={c} tone="warn">{c}</Badge>)}</div>
              )}
            </>
          ) : <StateView kind="empty" title="No cache lookup" />}
        </Section>

        <Section icon={<Cpu className="h-4 w-4" />} title="Inference">
          {b.model ? (
            <KV rows={[
              ["Model", b.model.model], ["Tier", b.model.tier], ["Revision", b.model.revision ?? "—"],
              ["Runtime", `${b.model.runtime ?? "—"} ${b.model.runtime_version ?? ""}`], ["Quantization", b.model.quantization ?? "—"],
              ["Input tokens", fmtInt(b.model.prompt_tokens)], ["Output tokens", fmtInt(b.model.completion_tokens)],
              ["TTFT", fmtMs(b.model.ttft_ms)], ["Generation", fmtMs(b.model.total_ms)], ["Queue", fmtMs(b.model.queue_ms)],
              ["Tokens / s", fmtNum(b.model.tokens_per_s, 1)], ["Finish", b.model.finish_reason ?? "—"],
              ["Answer hash", <Hash value={b.model.answer_sha256} />],
              ["First pass", b.model.first_pass ? `${b.model.first_pass.model} · ${fmtInt(b.model.first_pass.completion_tokens)} tok` : "—"],
            ]} />
          ) : <StateView kind="empty" title={b.cache?.decision === "CACHE_VERIFIED" ? "Served from verified cache" : "No inference ran"} />}
        </Section>

        <Section icon={<Gauge className="h-4 w-4" />} title="Router">
          {b.router ? (b.router.available ? (
            <>
              <KV rows={[
                ["Raw score", fmtNum(b.router.raw_score, 4)], ["Calibrated p(error)", fmtNum(b.router.p_error, 4)],
                ["Threshold", fmtNum(b.router.threshold, 4)], ["Accept local", b.router.accept_local ? "Yes" : "No"],
                ["Router version", b.router.version], ["Schema", b.router.schema_version],
                ["Calibration", b.router.calibration ?? "—"], ["Dataset hash", <Hash value={b.router.dataset_hash} />],
              ]} />
              <div className="mt-4 space-y-1.5">
                <div className="eyebrow">Strongest factors</div>
                {(b.router.factors ?? []).map((f: any) => (
                  <div key={f.feature} className="flex items-center justify-between text-[12.5px]">
                    <span className="mono text-ink">{f.feature}</span>
                    <span className={clsx("num", f.contribution > 0 ? "text-warn" : "text-mint")}>{f.contribution > 0 ? "+" : ""}{fmtNum(f.contribution, 3)} logit</span>
                  </div>
                ))}
              </div>
            </>
          ) : <StateView kind="unavailable" title="Router unavailable" detail={b.router.reason} />) : <StateView kind="empty" title="Router not evaluated" />}
        </Section>

        <Section icon={<RouteIcon className="h-4 w-4" />} title="Decision">
          <KV rows={[
            ["Chosen route", <RouteBadge route={b.decision?.chosen_route} />], ["Requested", b.decision?.requested_route],
            ["Primary reason", <ReasonBadge code={b.decision?.reason_code} />], ["Fallback", b.decision?.fallback_state ?? "none"],
            ["Connector", `${b.decision?.connector?.label ?? "—"} (${b.decision?.connector?.health ?? "—"})`],
            ["Output action", b.decision?.output_action ?? "—"],
            ["Remote requests", fmtInt(b.decision?.egress?.remote_requests)], ["Remote bytes out", fmtBytes(b.decision?.egress?.remote_bytes_out)],
          ]} />
          <div className="mt-4 flex flex-wrap gap-1.5">{(b.decision?.reason_codes ?? []).map((c: string) => <ReasonBadge key={c} code={c} />)}</div>
          <p className="mt-3 text-[11.5px] text-ink-3">Remote bytes are counted by the application-layer egress guard every remote call must pass through.</p>
        </Section>

        <Section icon={<BadgeDollarSign className="h-4 w-4" />} title="Economics" right={<ProvenanceTag kind="Measured" />}>
          {b.economics ? (
            <>
              <KV rows={[
                ["Actual cost", fmtUsd(b.economics.actual_cost_usd)],
                ["Counterfactual", `${fmtUsd(b.economics.counterfactual?.cost_usd)} (${b.economics.counterfactual?.provider}/${b.economics.counterfactual?.model})`],
                ["Avoided (calculated)", fmtUsd(b.economics.avoided_cost_usd)], ["Tokens avoided", fmtInt(b.economics.tokens_avoided_by_cache)],
                ["GPU energy", isNum(b.economics.energy_j) ? `${fmtNum(b.economics.energy_j, 1)} J` : "Unavailable"],
                ["Pricing version", `${b.economics.pricing_version} · ${b.economics.pricing_sha256}`],
              ]} />
              <p className="mt-3 text-[11.5px] text-ink-3">{b.economics.counterfactual?.basis}. Rate source: {b.economics.counterfactual?.source}. {b.economics.energy_note}.</p>
            </>
          ) : <StateView kind="empty" title="No economics (request did not complete)" />}
        </Section>

        <Section icon={<Waypoints className="h-4 w-4" />} title="Device" right={<ProvenanceTag kind={b.device?.telemetry_mode === "demo" ? "Demo telemetry" : "Live device telemetry"} />}>
          <KV rows={[
            ["Sampled", fmtDateTime(b.device?.sampled_at)], ["GPU utilization", isNum(b.device?.gpu_util_pct) ? `${b.device.gpu_util_pct}%` : "Unavailable"],
            ["GPU power", isNum(b.device?.gpu_power_w) ? `${fmtNum(b.device.gpu_power_w, 1)} W` : "Unavailable"],
            ["GPU temperature", isNum(b.device?.gpu_temp_c) ? `${b.device.gpu_temp_c} °C` : "Unavailable"],
            ["Unified memory", isNum(b.device?.mem_used_bytes) ? `${fmtBytes(b.device.mem_used_bytes)} / ${fmtBytes(b.device.mem_total_bytes)}` : "Unavailable"],
            ["Throughput", isNum(b.device?.tokens_per_s) ? `${fmtNum(b.device.tokens_per_s, 1)} tok/s` : "Unavailable"],
            ["Model health", b.device?.model_health], ["Queue depth", fmtInt(b.device?.queue_depth)],
          ]} />
        </Section>

        <Section icon={<Link2 className="h-4 w-4" />} title="Integrity">
          <KV cols={1} rows={[
            ["Receipt hash", <span className="mono break-all">{r.hash}</span>],
            ["Previous receipt hash", <span className="mono break-all">{r.prev_hash}</span>],
            ["HMAC", <Hash value={r.hmac} n={24} />], ["Key id", b.integrity?.hmac_key_id], ["Algorithm", b.integrity?.algorithm],
            ["Code commit", b.integrity?.git_commit ?? "—"], ["Router run", b.integrity?.router_run_id ?? "—"],
          ]} />
          <div className="mt-5 rounded-xl border border-dashed border-line p-4">
            <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink"><Hammer className="h-4 w-4" />Tamper test</div>
            <p className="mb-3 text-[12.5px] text-ink-2">Changes <span className="mono">decision.chosen_route</span> to "remote" on a detached copy and recomputes the hash. The sealed chain is not modified.</p>
            <button className="btn-ghost" onClick={() => tamper.mutate()} disabled={tamper.isPending} data-testid="tamper-demo">Run tamper test</button>
            {tamper.data && (
              <div className="mt-3 space-y-1.5 text-[12.5px]">
                <div className={clsx("font-semibold", tamper.data.hash_matches ? "text-ink" : "text-danger")}>{tamper.data.verdict}</div>
                <div className="flex justify-between gap-4"><span className="text-ink-3">Stored hash</span><span className="mono">{tamper.data.stored_hash.slice(0, 24)}…</span></div>
                <div className="flex justify-between gap-4"><span className="text-ink-3">Hash after edit</span><span className="mono text-danger">{tamper.data.tampered_hash.slice(0, 24)}…</span></div>
                <div className="flex justify-between gap-4"><span className="text-ink-3">HMAC still valid</span><span>{tamper.data.hmac_matches ? "yes" : "no"}</span></div>
              </div>
            )}
          </div>
        </Section>
      </div>

      <Card className="mt-6">
        <CardHeader icon={<Waypoints className="h-4 w-4" />} title="Provenance timeline" subtitle="Every stage the request passed through, with offsets from arrival." />
        <ol className="divide-y divide-hair">
          {b.timeline.map((t, i) => (
            <li key={i} className="grid grid-cols-[88px_140px_110px_1fr] items-start gap-4 px-6 py-3 text-[12.5px]">
              <span className="num text-ink-3">+{fmtMs(t.t_ms)}</span>
              <span className="font-semibold text-ink">{t.stage}</span>
              <span><Badge tone={["denied", "failed", "blocked", "rejected"].includes(t.status) ? "bad" : ["skipped", "miss", "unavailable", "fallback"].includes(t.status) ? "neutral" : "good"}>{t.status}</Badge></span>
              <span className="mono break-all text-ink-2">{JSON.stringify(t.detail)}</span>
            </li>
          ))}
        </ol>
      </Card>
    </>
  );
}
