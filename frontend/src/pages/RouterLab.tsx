import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip,
  XAxis, YAxis, ZAxis,
} from "recharts";
import { Beaker, FlaskConical, SlidersHorizontal } from "lucide-react";
import { get, post } from "../api/client";
import { AXIS, ChartFrame, ChartTooltip, DIVERGING, GRID, Legend, SERIES } from "../components/charts";
import { Card, CardHeader, Explainer, KV, PageHeader, ProvenanceTag, Stat, StateView } from "../components/ui";
import { fmtDateTime, fmtInt, fmtNum, fmtPct, fmtUsd, ROUTE_LABEL } from "../lib/format";

const METHOD_LABEL: Record<string, string> = {
  calibrated_lr: "Calibrated logistic (deployed)", uncalibrated_lr: "Uncalibrated logistic",
  raw_logprob: "Raw log-prob threshold", calibrated_hgb: "Calibrated gradient boosting",
};

export default function RouterLab() {
  const q = useQuery({ queryKey: ["router"], queryFn: () => get<any>("/api/router") });
  const [th, setTh] = useState<number | null>(null);
  const preview = useMutation({ mutationFn: (t: number) => post<any>("/api/router/threshold-preview", { threshold: t }) });
  const meta = q.data?.meta;
  const ev = q.data?.evaluation;
  useEffect(() => { if (meta?.threshold != null && th === null) setTh(meta.threshold); }, [meta, th]);
  useEffect(() => {
    if (th == null) return;
    const id = setTimeout(() => preview.mutate(th), 180);
    return () => clearTimeout(id);
  }, [th]); // eslint-disable-line react-hooks/exhaustive-deps

  const rc = useMemo(() => {
    if (!ev?.risk_coverage) return [];
    const methods = Object.keys(ev.risk_coverage);
    const grid = Array.from({ length: 51 }, (_, i) => i / 50);
    return grid.map((c) => {
      const row: any = { coverage: c };
      methods.forEach((m) => {
        const pts: { coverage: number; risk: number }[] = ev.risk_coverage[m];
        const p = pts.reduce((best, x) => (Math.abs(x.coverage - c) < Math.abs(best.coverage - c) ? x : best), pts[0]);
        row[m] = p && Math.abs(p.coverage - c) <= 0.03 ? p.risk : null;
      });
      return row;
    });
  }, [ev]);

  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError) return <StateView kind="error" detail={String(q.error)} />;
  const methods = ev ? Object.keys(ev.risk_coverage ?? {}) : [];

  return (
    <>
      <PageHeader eyebrow="Research" title="Router Lab"
        subtitle="A trained, calibrated model estimates the probability that the local answer is unacceptable. Token confidence is one feature — never treated as correctness." />
      {!q.data.available && (
        <Card className="mb-6">
          <StateView kind="unavailable" title="Router artifacts not trained yet" detail={<>
            {q.data.error}<br />Run <span className="mono">make train-router</span> after generating router data with real local inference. The gateway serves requests without escalation until then (reason <span className="mono">ROUTER_UNAVAILABLE</span>).</>} />
        </Card>
      )}
      {meta && (
        <>
          <Card className="mb-6">
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 [&>*]:border-b [&>*]:border-hair">
              <Stat label="Model version" value={<span className="text-[18px]">{meta.version}</span>} sub={fmtDateTime(meta.trained_at)} prov="Benchmark" />
              <Stat label="Train samples" value={fmtInt(meta.n_train)} sub="grouped split" prov="Benchmark" />
              <Stat label="Calibration samples" value={fmtInt(meta.n_calibration)} sub={meta.calibration_method} prov="Benchmark" />
              <Stat label="Test samples" value={fmtInt(meta.n_test)} sub="untouched final test" prov="Benchmark" />
              <Stat label="Validation threshold" value={fmtNum(meta.threshold, 3)} sub={meta.threshold_rule} prov="Benchmark" />
              <Stat label="Dataset hash" value={<span className="mono !text-[14px]">{String(meta.dataset_sha256).slice(0, 12)}</span>} sub={meta.dataset_sources?.join(" · ")} prov="Public dataset" />
            </div>
            {ev && (
              <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7">
                <Stat label="Local accuracy" value={fmtPct(ev.baselines?.local_only_accuracy)} sub="local-only baseline" prov="Benchmark" />
                <Stat label="Larger tier accuracy" value={fmtPct(ev.baselines?.large_only_accuracy)} sub={ev.baselines?.large_model ?? "not measured"} prov="Benchmark" />
                <Stat label="Local coverage" value={fmtPct(ev.methods?.calibrated_lr?.coverage)} sub="at validation threshold" prov="Benchmark" />
                <Stat label="Selective accuracy" value={fmtPct(ev.methods?.calibrated_lr?.selective_accuracy)} sub="on accepted answers" prov="Benchmark" />
                <Stat label="ECE" value={fmtNum(ev.methods?.calibrated_lr?.ece, 3)} sub="15 equal-width bins" prov="Benchmark" />
                <Stat label="Brier / NLL" value={`${fmtNum(ev.methods?.calibrated_lr?.brier, 3)} / ${fmtNum(ev.methods?.calibrated_lr?.nll, 3)}`} prov="Benchmark" />
                <Stat label="AURC" value={fmtNum(ev.methods?.calibrated_lr?.aurc, 3)} sub="lower is better" prov="Benchmark" />
              </div>
            )}
          </Card>

          <Card className="mb-6">
            <CardHeader icon={<SlidersHorizontal className="h-4 w-4" />} eyebrow="Threshold studio" title="Trade coverage against risk"
              subtitle="The backend recomputes the trade-off on the frozen test predictions. Previews are not measurements of live traffic."
              right={<ProvenanceTag kind="Preview" />} />
            <div className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_1.2fr]">
              <div className="space-y-4">
                <label className="block text-[12.5px] font-medium text-ink-2" htmlFor="thr">p(error) threshold · {fmtNum(th, 3)}</label>
                <input id="thr" type="range" min={0.02} max={0.98} step={0.01} value={th ?? 0.5} onChange={(e) => setTh(Number(e.target.value))} className="w-full accent-[#3D4FB8]" data-testid="threshold-slider" />
                <div className="flex justify-between text-[11.5px] text-ink-3"><span>stricter · escalate more</span><span>looser · keep more local</span></div>
                <button className="btn-ghost !py-1.5 !text-xs" onClick={() => setTh(meta.threshold)}>Reset to validation-selected ({fmtNum(meta.threshold, 3)})</button>
              </div>
              <div data-testid="threshold-preview">
                {preview.isError ? <StateView kind="error" detail={String(preview.error)} /> : (
                  <KV rows={[
                    ["Current threshold", fmtNum(preview.data?.threshold, 3)], ["Validation-selected", fmtNum(meta.threshold, 3)],
                    ["Projected local coverage", fmtPct(preview.data?.coverage)], ["Projected risk (error on kept)", fmtPct(preview.data?.selective_risk)],
                    ["Selective accuracy", fmtPct(preview.data?.selective_accuracy)], ["Escalated / deferred", `${fmtInt(preview.data?.deferred)} of ${fmtInt(preview.data?.n)}`],
                    ["System accuracy with local-large escalation", fmtPct(preview.data?.system_accuracy_with_large_escalation)],
                  ]} />
                )}
              </div>
            </div>
          </Card>

          {ev && (
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Card className="p-6">
                <ChartFrame title="Risk–coverage curve" meta={`test n=${fmtInt(ev.n_test)} · run ${ev.run_id}`}
                  legend={<Legend items={methods.map((m, i) => ({ label: METHOD_LABEL[m] ?? m, color: SERIES[i], dashed: m === "raw_logprob" }))} />}>
                  <ResponsiveContainer>
                    <LineChart data={rc} margin={{ top: 8, right: 12, bottom: 16, left: 0 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="coverage" type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} {...AXIS} label={{ value: "coverage (answers kept local)", position: "insideBottom", offset: -8, fill: "#898781", fontSize: 11 }} />
                      <YAxis tickFormatter={(v) => `${Math.round(v * 100)}%`} {...AXIS} width={44} />
                      <Tooltip content={<ChartTooltip fmt={(v) => fmtPct(v)} labelFmt={(l) => `coverage ${fmtPct(l, 0)}`} />} />
                      {methods.map((m, i) => <Line key={m} dataKey={m} name={METHOD_LABEL[m] ?? m} stroke={SERIES[i]} strokeWidth={2} dot={false} connectNulls strokeDasharray={m === "raw_logprob" ? "5 4" : undefined} isAnimationActive={false} />)}
                      {preview.data?.coverage != null && <ReferenceLine x={preview.data.coverage} stroke="#0F1B33" strokeDasharray="3 3" label={{ value: "threshold", fill: "#4A5468", fontSize: 11, position: "top" }} />}
                    </LineChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
              <Card className="p-6">
                <ChartFrame title="Reliability diagram" meta="predicted p(error) vs observed error rate · point size = bin count"
                  legend={<Legend items={[{ label: "Calibrated", color: SERIES[0] }, { label: "Uncalibrated", color: SERIES[1] }, { label: "Perfect calibration", color: "#c3c2b7", dashed: true }]} />}>
                  <ResponsiveContainer>
                    <ScatterChart margin={{ top: 8, right: 12, bottom: 16, left: 0 }}>
                      <CartesianGrid stroke={GRID} />
                      <XAxis dataKey="mean_pred" type="number" domain={[0, 1]} {...AXIS} name="predicted" label={{ value: "mean predicted p(error)", position: "insideBottom", offset: -8, fill: "#898781", fontSize: 11 }} />
                      <YAxis dataKey="frac_pos" type="number" domain={[0, 1]} {...AXIS} width={40} name="observed" />
                      <ZAxis dataKey="count" range={[40, 260]} name="n" />
                      <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#c3c2b7" strokeDasharray="4 4" />
                      <Tooltip content={<ChartTooltip fmt={(v, n) => (n === "n" ? String(v) : v.toFixed(3))} />} />
                      <Scatter name="Calibrated" data={ev.reliability?.calibrated_lr ?? []} fill={SERIES[0]} line={{ stroke: SERIES[0], strokeWidth: 2 }} isAnimationActive={false} />
                      <Scatter name="Uncalibrated" data={ev.reliability?.uncalibrated_lr ?? []} fill={SERIES[1]} line={{ stroke: SERIES[1], strokeWidth: 2 }} isAnimationActive={false} />
                    </ScatterChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
              <Card className="p-6">
                <ChartFrame title="Baseline comparison" meta="AUROC for predicting local errors · test set">
                  <ResponsiveContainer>
                    <BarChart data={methods.map((m) => ({ name: METHOD_LABEL[m] ?? m, auroc: ev.methods[m].auroc, ece: ev.methods[m].ece }))} layout="vertical" margin={{ left: 8, right: 24 }}>
                      <CartesianGrid stroke={GRID} horizontal={false} />
                      <XAxis type="number" domain={[0.5, 1]} {...AXIS} />
                      <YAxis type="category" dataKey="name" width={190} {...AXIS} />
                      <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(15,27,51,0.04)" }} />
                      <Bar dataKey="auroc" name="AUROC" radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false}>
                        {methods.map((m, i) => <Cell key={m} fill={SERIES[i]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
              <Card className="p-6">
                <ChartFrame title="Feature influence" meta="standardized logistic coefficients · red raises p(error), blue lowers it">
                  <ResponsiveContainer>
                    <BarChart data={(ev.feature_importance ?? []).slice(0, 12)} layout="vertical" margin={{ left: 8, right: 24 }}>
                      <CartesianGrid stroke={GRID} horizontal={false} />
                      <XAxis type="number" {...AXIS} />
                      <YAxis type="category" dataKey="feature" width={170} {...AXIS} />
                      <ReferenceLine x={0} stroke="#c3c2b7" />
                      <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(15,27,51,0.04)" }} />
                      <Bar dataKey="coef" name="coefficient" barSize={12} radius={4} isAnimationActive={false}>
                        {(ev.feature_importance ?? []).slice(0, 12).map((f: any) => <Cell key={f.feature} fill={f.coef > 0 ? DIVERGING.pos : DIVERGING.neg} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
              <Card className="p-6">
                <ChartFrame title="Cost–quality frontier" meta="measured accuracy vs mean on-device compute seconds per request"
                  legend={<Legend items={(ev.cost_quality ?? []).map((p: any, i: number) => ({ label: p.label, color: SERIES[i % SERIES.length] }))} />}>
                  <ResponsiveContainer>
                    <ScatterChart margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
                      <CartesianGrid stroke={GRID} />
                      <XAxis dataKey="compute_s" type="number" {...AXIS} name="compute s" label={{ value: "mean compute seconds / request", position: "insideBottom", offset: -8, fill: "#898781", fontSize: 11 }} />
                      <YAxis dataKey="quality" type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} {...AXIS} width={44} name="accuracy" />
                      <Tooltip content={<ChartTooltip fmt={(v, n) => (n === "accuracy" ? fmtPct(v) : v.toFixed(2))}
                        extra={(p) => p && <div className="mt-1 text-ink-3">{p.label}{p.remote_usd_per_1k != null ? ` · remote ${fmtUsd(p.remote_usd_per_1k)}/1k req (Scenario)` : ""}</div>} />} />
                      {(ev.cost_quality ?? []).map((p: any, i: number) => <Scatter key={p.label} name={p.label} data={[p]} fill={SERIES[i % SERIES.length]} isAnimationActive={false} />)}
                    </ScatterChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
              <Card className="p-6">
                <ChartFrame title="Route distribution" meta="test set at validation threshold vs live traffic">
                  <ResponsiveContainer>
                    <BarChart data={[
                      ...Object.entries(ev.route_distribution_test ?? {}).map(([k, v]) => ({ name: `test · ${k}`, n: v as number })),
                      ...Object.entries(q.data.live_counts ?? {}).map(([k, v]) => ({ name: `live · ${ROUTE_LABEL[k] ?? k}`, n: v as number })),
                    ]} margin={{ left: 0, right: 12, bottom: 24 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="name" {...AXIS} interval={0} angle={-15} textAnchor="end" height={50} />
                      <YAxis {...AXIS} width={40} allowDecimals={false} />
                      <Tooltip content={<ChartTooltip fmt={(v) => String(v)} />} cursor={{ fill: "rgba(15,27,51,0.04)" }} />
                      <Bar dataKey="n" name="requests" fill={SERIES[0]} barSize={22} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartFrame>
              </Card>
            </div>
          )}
          <Card className="mt-6">
            <CardHeader icon={<Beaker className="h-4 w-4" />} title="Training provenance" />
            <div className="space-y-4 p-6">
              <KV cols={3} rows={[
                ["Target", "y_error = 1 when the local answer fails the task rubric"], ["Split", meta.split_rule],
                ["Features", `${fmtInt(meta.features?.length)} (schema ${meta.schema_version})`], ["Code commit", meta.code_commit ?? "—"],
                ["Local model", meta.local_model], ["Run id", meta.run_id],
              ]} />
              <Explainer>Calibration is fitted on a separate split from training, and the threshold is chosen on that validation split before the test set is scored once. Weak results are reported as measured; see <span className="mono">RESULTS.md</span>.</Explainer>
            </div>
          </Card>
        </>
      )}
      {!meta && <Card><CardHeader icon={<FlaskConical className="h-4 w-4" />} title="Why calibration matters" />
        <div className="p-6"><Explainer>A raw model score is not a probability. Isotonic calibration maps scores to observed error frequencies so a threshold of 0.3 means "about 30% of answers like this are wrong" — which is what a routing policy needs.</Explainer></div></Card>}
    </>
  );
}
