import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { DatabaseZap, FlaskConical, Loader2, Play, Unplug } from "lucide-react";
import { get, post } from "../api/client";
import { AXIS, ChartFrame, ChartTooltip, GRID, Legend, SERIES } from "../components/charts";
import { Badge, Card, CardHeader, Explainer, KV, PageHeader, ProvenanceTag, ReasonBadge, Stat, StateView } from "../components/ui";
import { fmtDateTime, fmtInt, fmtNum, fmtPct } from "../lib/format";

const PAIRS = [
  { label: "True paraphrase", a: "How do I reset the VPN client?", b: "What steps restore my VPN connection?", tb: "acme" },
  { label: "Hard negative", a: "How do I reset the VPN client?", b: "How do I reset another employee's VPN password?", tb: "acme" },
  { label: "Cross-tenant", a: "How do I reset the VPN client?", b: "How do I reset the VPN client?", tb: "globex" },
  { label: "Scope escalation", a: "How do I reset the VPN client?", b: "How do I reset the VPN client for all users in the company?", tb: "acme" },
];

export default function CachePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cache"], queryFn: () => get<any>("/api/cache") });
  const [a, setA] = useState(PAIRS[0].a);
  const [b, setB] = useState(PAIRS[0].b);
  const [tb, setTb] = useState("acme");
  const pair = useMutation({ mutationFn: () => post<any>("/api/cache/test-pair", { a, b, tenant_a: "acme", department_a: "it", tenant_b: tb, department_b: "it" }) });
  const revoke = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => post<any>(`/api/cache/sources/${id}/${on ? "revoke" : "restore"}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cache"] }),
  });
  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError) return <StateView kind="error" detail={String(q.error)} />;
  const s = q.data.summary;
  const bm = q.data.benchmark;
  const verified = bm?.methods?.verified_semantic;
  const pts = bm?.pairs ?? [];
  const groups = [
    { key: "accepted", label: "Served (verified reuse)", color: SERIES[0] },
    { key: "hard_negative", label: "Above retrieval threshold, rejected by verifier", color: SERIES[2] },
    { key: "rejected", label: "Below retrieval threshold", color: SERIES[1] },
  ];
  const r = pair.data;
  return (
    <>
      <PageHeader eyebrow="Reuse" title="Verified Cache"
        subtitle="Answers are reused only when a second model verifies the new request means the same thing — inside the same tenant, department, policy version and context." />
      {!s && <Card className="mb-6"><StateView kind="unavailable" title="Semantic cache unavailable" detail={q.data.error} /></Card>}
      <Card className="mb-6">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">
          <Stat label="Hit rate" value={fmtPct(verified?.hit_rate)} sub={bm ? `benchmark n=${fmtInt(bm.n_queries)}` : "no benchmark run yet"} prov="Benchmark" hint={{ text: "In the benchmark, how often a question could be answered from the cache instead of running the model." }} />
          <Stat label="Verified precision" value={fmtPct(verified?.precision)} sub="served answers that were equivalent" prov="Benchmark" />
          <Stat label="False-hit rate" value={fmtPct(verified?.false_hit_rate)} sub="non-equivalent served" prov="Benchmark" hint={{ text: "How often the cache served an answer to a question that only looked similar but meant something different. Lower is better." }} tone={verified?.false_hit_rate > 0.02 ? "warn" : undefined} />
          <Stat hint={{ text: "Tricky look-alike questions, such as \"reset my password\" vs \"reset another employee's password\". This is how often the cache correctly refused to reuse the answer." }} label="Hard-negative rejection" value={fmtPct(verified?.hard_negative_rejection)} sub={verified ? `${fmtInt(verified.hard_negatives)} hard negatives` : ""} prov="Benchmark" />
          <Stat hint={{ text: "Times one company received a cached answer that belonged to another company in the isolation test. It must be 0." }} label="Cross-tenant leaks" value={bm ? fmtInt(bm.isolation?.cross_tenant_leaks) : "Unavailable"} sub={bm ? `${fmtInt(bm.isolation?.probes)} isolation probes` : ""} prov="Benchmark" tone={bm?.isolation?.cross_tenant_leaks > 0 ? "bad" : undefined} />
          <Stat label="Tokens avoided" value={fmtInt(s?.session_stats?.tokens_avoided)} sub={`${fmtInt(s?.session_stats?.hits)} live hits this session`} prov="Measured" />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.3fr_1fr]">
        <Card className="p-6">
          {pts.length === 0 ? <StateView kind="empty" title="No cache benchmark yet" detail="Run make benchmark to plot measured pairs." /> : (
            <ChartFrame title="Similarity vs verifier score" meta={<>benchmark run {bm._run_id} · n={fmtInt(pts.length)} pairs · <ProvenanceTag kind="Benchmark" /></>}
              legend={<Legend items={groups.map((g) => ({ label: g.label, color: g.color }))} />}>
              <ResponsiveContainer>
                <ScatterChart margin={{ top: 8, right: 16, bottom: 18, left: 0 }}>
                  <CartesianGrid stroke={GRID} />
                  <XAxis dataKey="similarity" type="number" domain={[0.3, 1]} {...AXIS} name="similarity" label={{ value: "retrieval cosine similarity", position: "insideBottom", offset: -8, fill: "#898781", fontSize: 11 }} />
                  <YAxis dataKey="verifier" type="number" domain={[0, 1]} {...AXIS} width={40} name="verifier" />
                  {s && <ReferenceLine x={s.thresholds.retrieval} stroke="#0F1B33" strokeDasharray="3 3" />}
                  {s && <ReferenceLine y={s.thresholds.verifier} stroke="#0F1B33" strokeDasharray="3 3" />}
                  <Tooltip content={<ChartTooltip extra={(p) => p && <div className="mt-1 max-w-[260px] text-ink-3">{p.a} ↔ {p.b}<br />{p.kind}{p.conflicts?.length ? ` · ${p.conflicts[0]}` : ""}</div>} />} />
                  {groups.map((g) => <Scatter key={g.key} name={g.label} data={pts.filter((p: any) => p.region === g.key)} fill={g.color} fillOpacity={0.8} stroke="#fcfcfb" strokeWidth={1} isAnimationActive={false} />)}
                </ScatterChart>
              </ResponsiveContainer>
            </ChartFrame>
          )}
          {bm?.methods && (
            <div className="mt-6 overflow-x-auto">
              <table className="w-full min-w-[640px]">
                <thead><tr className="border-b border-hair">{["Strategy", "Hit rate", "Precision", "False hits", "Hard-neg rejection", "P50 lookup", "P95 lookup"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
                <tbody>{Object.entries(bm.methods).map(([k, m]: any) => (
                  <tr key={k} className="border-b border-hair last:border-0">
                    <td className="table-cell font-medium">{k.replace(/_/g, " ")}</td><td className="table-cell num">{fmtPct(m.hit_rate)}</td>
                    <td className="table-cell num">{fmtPct(m.precision)}</td><td className="table-cell num">{fmtPct(m.false_hit_rate)}</td>
                    <td className="table-cell num">{fmtPct(m.hard_negative_rejection)}</td><td className="table-cell num">{m.p50_ms != null ? `${fmtNum(m.p50_ms, 1)} ms` : "—"}</td>
                    <td className="table-cell num">{m.p95_ms != null ? `${fmtNum(m.p95_ms, 1)} ms` : "—"}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader icon={<FlaskConical className="h-4 w-4" />} eyebrow="Pair tester" title="Would NanoGate reuse this answer?"
            subtitle="Runs the real embedding model, NLI verifier, slot checks and namespace rules." />
          <div className="space-y-3 p-6">
            <div className="flex flex-wrap gap-1.5">{PAIRS.map((p) => (
              <button key={p.label} className="rounded-full border border-line bg-white/70 px-3 py-1 text-[12px] text-ink-2 hover:text-ink" onClick={() => { setA(p.a); setB(p.b); setTb(p.tb); }}>{p.label}</button>))}
            </div>
            <label className="block text-[12px] font-medium text-ink-2" htmlFor="ra">Request A (cached) · acme / it</label>
            <input id="ra" className="input" value={a} onChange={(e) => setA(e.target.value)} />
            <label className="block text-[12px] font-medium text-ink-2" htmlFor="rb">Request B (incoming)</label>
            <input id="rb" className="input" value={b} onChange={(e) => setB(e.target.value)} />
            <select aria-label="Tenant for request B" className="input !w-auto" value={tb} onChange={(e) => setTb(e.target.value)}>
              <option value="acme">acme / it</option><option value="globex">globex / it</option>
            </select>
            <button className="btn-primary" onClick={() => pair.mutate()} disabled={pair.isPending || !s} data-testid="cache-test">
              {pair.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Evaluate pair
            </button>
            {pair.isError && <StateView kind="error" detail={String(pair.error)} />}
            {r && (
              <div className="animate-fadein space-y-3 pt-2" data-testid="cache-result">
                <div className="flex items-center gap-2">
                  <ReasonBadge code={r.decision} className="!text-[12.5px]" />
                  <span className="text-[14px] font-semibold text-ink">{r.equivalent ? "Equivalent" : r.decision === "CACHE_NAMESPACE_MISMATCH" ? "Namespace mismatch" : r.decision === "CACHE_HARD_NEGATIVE" ? "Not equivalent (hard negative)" : "Not equivalent"}</span>
                </div>
                <KV cols={1} rows={[
                  ["Retrieval similarity", `${fmtNum(r.similarity, 4)} (threshold ${r.retrieval_threshold})`],
                  ["Verifier score", `${fmtNum(r.verifier.score, 4)} (threshold ${r.verifier_threshold})`],
                  ["Entailment A→B / B→A", `${fmtNum(r.verifier.entail_ba, 3)} / ${fmtNum(r.verifier.entail_ab, 3)}`],
                  ["Contradiction", fmtNum(r.verifier.contradiction, 3)],
                  ["Namespace differences", r.namespace_differences.length ? r.namespace_differences.join(", ") : "none"],
                  ["Threshold source", r.threshold_source],
                ]} />
                {r.verifier.conflicts.length > 0 && <div className="flex flex-wrap gap-1.5">{r.verifier.conflicts.map((c: string) => <Badge key={c} tone="warn">{c}</Badge>)}</div>}
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[1fr_1.6fr]">
        <Card>
          <CardHeader icon={<Unplug className="h-4 w-4" />} title="Knowledge sources" subtitle="Cache entries are bound to the source version they were answered from." />
          <div className="space-y-3 p-6">
            {(q.data.sources ?? []).length === 0 && <StateView kind="empty" title="No sources registered" />}
            {(q.data.sources ?? []).map((src: any) => (
              <div key={src.source_id} className="flex items-center justify-between gap-3 rounded-xl border border-hair bg-white/60 px-4 py-3">
                <div className="min-w-0">
                  <div className="text-[13.5px] font-semibold text-ink">{src.source_id}</div>
                  <div className="mono truncate text-ink-3">{src.version}</div>
                </div>
                <button className="btn-ghost !py-1.5 !text-xs" onClick={() => revoke.mutate({ id: src.source_id, on: !src.revoked })}>
                  {src.revoked ? "Restore source" : "Revoke source"}
                </button>
              </div>
            ))}
            <Explainer>Revoking a source marks every entry answered from it as <span className="mono">CACHE_SOURCE_REVOKED</span>. Publishing a new policy version invalidates entries created under the old one.</Explainer>
          </div>
        </Card>
        <Card>
          <CardHeader icon={<DatabaseZap className="h-4 w-4" />} title="Cache entries" subtitle={s ? `${fmtInt(s.valid)} valid · ${fmtInt(s.invalidated)} invalidated · ${fmtInt(s.revoked)} revoked · ${fmtInt(s.namespaces)} namespaces` : ""} />
          {(q.data.entries ?? []).length === 0 ? <StateView kind="empty" title="Cache is empty" detail="Entries are written only by the gateway, from answers that passed output scanning and the router." /> : (
            <div className="max-h-[420px] overflow-auto">
              <table className="w-full min-w-[720px]">
                <thead className="sticky top-0 bg-white/95"><tr className="border-b border-hair">{["Query", "Scope", "Policy", "State", "Hits", "Created"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
                <tbody>{q.data.entries.map((e: any) => (
                  <tr key={e.cache_id} className="border-b border-hair last:border-0">
                    <td className="table-cell max-w-[280px] truncate" title={e.query_preview}>{e.query_preview}</td>
                    <td className="table-cell text-ink-2">{e.tenant_id}/{e.department_id}</td>
                    <td className="table-cell text-ink-2">{e.policy_version}</td>
                    <td className="table-cell"><Badge tone={e.validation_state === "valid" ? "good" : "warn"} title={e.invalidated_reason ?? undefined}>{e.validation_state}</Badge></td>
                    <td className="table-cell num">{fmtInt(e.hits)}</td>
                    <td className="table-cell text-ink-2">{fmtDateTime(e.created_at)}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
