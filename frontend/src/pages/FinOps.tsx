import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, Calculator, Receipt as ReceiptIcon, Wallet } from "lucide-react";
import { get } from "../api/client";
import { AXIS, ChartFrame, ChartTooltip, GRID, Legend, SERIES } from "../components/charts";
import { Card, CardHeader, Explainer, KV, PageHeader, ProvenanceTag, Stat, StateView, Tabs } from "../components/ui";
import { fmtInt, fmtNum, fmtPct, fmtUsd, ROUTE_COLORS, ROUTE_LABEL } from "../lib/format";

export default function FinOps() {
  const [tab, setTab] = useState<"measured" | "scenario">("measured");
  return (
    <>
      <PageHeader eyebrow="Finance" title="FinOps"
        subtitle="Measured spend comes from metered tokens and published rates. Projections live on a separate tab and are always labelled as scenario assumptions."
        right={<Tabs value={tab} onChange={setTab} items={[{ id: "measured", label: "Measured" }, { id: "scenario", label: "Scenario" }]} />} />
      {tab === "measured" ? <Measured /> : <Scenario />}
    </>
  );
}

function Measured() {
  const q = useQuery({ queryKey: ["finops", "measured"], queryFn: () => get<any>("/api/finops/measured?days=30") });
  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError) return <StateView kind="error" detail={String(q.error)} />;
  const d = q.data;
  const routes = Object.entries(d.by_route ?? {}).map(([k, v]: any) => ({ route: ROUTE_LABEL[k] ?? k, key: k, ...v, avoided: Math.max(0, v.counterfactual_usd - v.cost_usd) }));
  return (
    <div data-testid="finops-measured">
      <Card className="mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 [&>*]:border-b [&>*]:border-hair xl:[&>*]:border-b-0">
          <Stat label="Requests" value={fmtInt(d.requests)} sub="answered, last 30 days" prov="Measured" />
          <Stat label="Tokens" value={fmtInt(d.tokens)} sub="metered by the runtime" prov="Measured" />
          <Stat label="Actual cost" value={fmtUsd(d.actual_cost_usd)} sub="configured rates × tokens" prov="Measured" />
          <Stat label="Same workload, hosted" value={fmtUsd(d.counterfactual_usd)} sub={`${d.pricing.counterfactual_reference.provider}/${d.pricing.counterfactual_reference.model}`} prov="Measured" />
          <Stat label="Avoided" value={fmtUsd(Math.max(0, d.counterfactual_usd - d.actual_cost_usd))} sub="counterfactual − actual" prov="Measured" />
          <Stat label="Tokens avoided by cache" value={fmtInt(d.tokens_avoided)} sub={fmtUsd(d.cache_avoided_usd) + " at reference rate"} prov="Measured" />
          <Stat label="GPU energy" value={d.energy_j ? `${fmtNum(d.energy_j / 3600, 2)} Wh` : "Unavailable"} sub="NVML energy counter" prov="Live device telemetry" />
        </div>
      </Card>
      {d.requests === 0 ? <Card><StateView kind="empty" title="No completed requests in this window" detail="Costs appear once real requests are answered." /></Card> : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Card className="p-6">
            <ChartFrame title="Tokens over time" meta={`hourly buckets · ${fmtInt(d.series.length)} hours with traffic`}
              legend={<Legend items={[{ label: "Tokens processed", color: SERIES[0] }, { label: "Tokens avoided (cache)", color: SERIES[1] }]} />}>
              <ResponsiveContainer>
                <LineChart data={d.series} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="bucket" {...AXIS} tickFormatter={(v) => String(v).slice(11, 16)} />
                  <YAxis {...AXIS} width={52} />
                  <Tooltip content={<ChartTooltip fmt={(v) => fmtInt(v)} />} />
                  <Line dataKey="tokens" name="Tokens processed" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                  <Line dataKey="tokens_avoided" name="Tokens avoided (cache)" stroke={SERIES[1]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartFrame>
          </Card>
          <Card className="p-6">
            <ChartFrame title="Cost by route" meta="actual vs same tokens at the reference hosted rate"
              legend={<Legend items={[{ label: "Actual", color: SERIES[0] }, { label: "Hosted counterfactual", color: SERIES[1] }]} />}>
              <ResponsiveContainer>
                <BarChart data={routes} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="route" {...AXIS} />
                  <YAxis {...AXIS} width={60} tickFormatter={(v) => `$${Number(v).toFixed(4)}`} />
                  <Tooltip content={<ChartTooltip fmt={(v) => fmtUsd(v)} extra={(p) => p && <div className="mt-1 text-ink-3">{fmtInt(p.requests)} requests · {fmtInt(p.tokens)} tokens</div>} />} cursor={{ fill: "rgba(15,27,51,0.04)" }} />
                  <Bar dataKey="cost_usd" name="Actual" fill={SERIES[0]} radius={[4, 4, 0, 0]} barSize={18} isAnimationActive={false} />
                  <Bar dataKey="counterfactual_usd" name="Hosted counterfactual" fill={SERIES[1]} radius={[4, 4, 0, 0]} barSize={18} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
          </Card>
          <Card>
            <CardHeader icon={<Wallet className="h-4 w-4" />} title="Department budgets" subtitle="Atomic reserve-and-settle ledger · current month" right={<ProvenanceTag kind="Measured" />} />
            <div className="space-y-4 p-6">
              {(d.budgets ?? []).length === 0 && <StateView kind="empty" title="No budget activity this month" />}
              {(d.budgets ?? []).map((b: any) => {
                const used = b.spent_tokens / b.limit_tokens;
                return (
                  <div key={b.tenant_id + b.department_id}>
                    <div className="mb-1.5 flex items-baseline justify-between text-[13px]">
                      <span className="font-medium capitalize text-ink">{b.tenant_id} / {b.department_id}</span>
                      <span className="num text-ink-2">{fmtInt(b.spent_tokens)} / {fmtInt(b.limit_tokens)} tokens · {fmtUsd(b.spent_usd)} of {fmtUsd(b.limit_usd, 2)}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-hair" role="progressbar" aria-valuenow={Math.round(used * 100)} aria-valuemin={0} aria-valuemax={100}>
                      <div className="h-full rounded-full" style={{ width: `${Math.min(100, used * 100)}%`, background: used > 0.9 ? "#d03b3b" : SERIES[0] }} />
                    </div>
                    {(b.reserved_tokens > 0 || b.reserved_usd > 0) && <div className="mt-1 text-[11.5px] text-ink-3">{fmtInt(b.reserved_tokens)} tokens reserved in flight</div>}
                  </div>
                );
              })}
            </div>
          </Card>
          <Card>
            <CardHeader icon={<ReceiptIcon className="h-4 w-4" />} title="Savings attribution" subtitle={d.attribution_note} />
            <div className="overflow-x-auto p-2">
              <table className="w-full">
                <thead><tr className="border-b border-hair">{["Route", "Requests", "Tokens", "Actual", "Counterfactual", "Avoided", "Energy"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
                <tbody>{routes.map((r) => (
                  <tr key={r.key} className="border-b border-hair last:border-0">
                    <td className="table-cell"><span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: ROUTE_COLORS[r.key] }} />{r.route}</span></td>
                    <td className="table-cell num">{fmtInt(r.requests)}</td><td className="table-cell num">{fmtInt(r.tokens)}</td>
                    <td className="table-cell num">{fmtUsd(r.cost_usd)}</td><td className="table-cell num">{fmtUsd(r.counterfactual_usd)}</td>
                    <td className="table-cell num font-medium">{fmtUsd(r.avoided)}</td><td className="table-cell num">{r.energy_j ? `${fmtNum(r.energy_j, 0)} J` : "—"}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          </Card>
          <Card className="xl:col-span-2">
            <CardHeader icon={<Calculator className="h-4 w-4" />} title="Rate card" subtitle={`config/pricing.yaml · version ${d.pricing.version} · sha ${d.pricing.sha256}`} right={<ProvenanceTag kind="Configuration" />} />
            <div className="overflow-x-auto p-2">
              <table className="w-full min-w-[900px]">
                <thead><tr className="border-b border-hair">{["Provider", "Model", "Tier", "Input / 1M", "Output / 1M", "Effective", "Source"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
                <tbody>{d.pricing.rates.map((r: any) => (
                  <tr key={r.provider + r.model} className="border-b border-hair last:border-0">
                    <td className="table-cell">{r.provider}</td><td className="table-cell font-medium">{r.model}</td><td className="table-cell text-ink-2">{r.tier}</td>
                    <td className="table-cell num">{fmtUsd(r.input_per_mtok, 2)}</td><td className="table-cell num">{fmtUsd(r.output_per_mtok, 2)}</td>
                    <td className="table-cell text-ink-2">{r.effective_date}</td><td className="table-cell max-w-[380px] text-[12px] text-ink-3">{r.source}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Scenario() {
  const q = useQuery({ queryKey: ["finops", "scenario"], queryFn: () => get<any>("/api/finops/scenario") });
  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError) return <StateView kind="error" detail={String(q.error)} />;
  const d = q.data;
  const a = d.assumptions;
  const p = d.projection;
  return (
    <div data-testid="finops-scenario">
      <div className="mb-6 flex items-start gap-3 rounded-2xl border border-warn/25 bg-warn-soft/70 px-5 py-4">
        <AlertTriangle className="mt-0.5 h-5 w-5 text-warn" />
        <div>
          <div className="text-[14px] font-semibold text-warn">Scenario assumption — not a measurement</div>
          <div className="text-[13px] text-ink-2">{d.warning} Edit <span className="mono">config/scenario.yaml</span> to model your own environment.</div>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Assumptions" right={<ProvenanceTag kind="Scenario" />} />
          <div className="p-6">
            <KV cols={1} rows={[
              ["Hardware", a.hardware.name], ["Purchase price", fmtUsd(a.hardware.purchase_price_usd, 0)], ["Useful life", `${a.hardware.useful_life_years} years`],
              ["Electricity", `${fmtUsd(a.electricity.price_usd_per_kwh, 3)} / kWh`], ["Annual requests", fmtInt(a.workload.annual_requests)],
              ["Avg prompt / completion tokens", `${fmtInt(a.workload.avg_prompt_tokens)} / ${fmtInt(a.workload.avg_completion_tokens)}`],
              ["Compared against", `${a.comparison.provider}/${a.comparison.model}`],
            ]} />
          </div>
        </Card>
        <Card>
          <CardHeader title="Inputs taken from measurement" right={<ProvenanceTag kind="Measured" />} />
          <div className="p-6">
            <KV cols={1} rows={[
              ["Cache hit rate", fmtPct(d.inputs_from_measurement.cache_hit_rate)], ["Local coverage", fmtPct(d.inputs_from_measurement.local_coverage)],
              ["Avg GPU energy / request", d.inputs_from_measurement.avg_gpu_energy_j_per_request != null ? `${fmtNum(d.inputs_from_measurement.avg_gpu_energy_j_per_request, 1)} J` : "Unavailable"],
              ["Sample size", fmtInt(d.inputs_from_measurement.sample_requests)],
            ]} />
          </div>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Annual projection" right={<ProvenanceTag kind="Scenario" />} />
          <div className="grid grid-cols-2 md:grid-cols-5">
            <Stat label="All-remote API cost" value={fmtUsd(p.all_remote_api_cost_usd, 0)} prov="Scenario" />
            <Stat label="Hardware amortization" value={fmtUsd(p.hardware_amortization_usd_per_year, 0)} prov="Scenario" />
            <Stat label="GPU energy" value={fmtUsd(p.gpu_energy_cost_usd_per_year, 2)} prov="Scenario" />
            <Stat label="Residual remote" value={fmtUsd(p.residual_remote_cost_usd_per_year, 0)} prov="Scenario" />
            <Stat label="Projected NanoGate total" value={fmtUsd(p.projected_nanogate_cost_usd_per_year, 0)} prov="Scenario" />
          </div>
          <div className="p-6 pt-0"><Explainer>Projection = hardware amortization + measured GPU energy × assumed volume × electricity price + hosted cost of traffic the router would escalate. It excludes staff, networking and whole-system power (not measurable here).</Explainer></div>
        </Card>
      </div>
    </div>
  );
}
