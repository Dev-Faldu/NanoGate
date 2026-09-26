import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import clsx from "clsx";
import {
  Boxes, Cpu, Database, Gauge, Globe2, HardDrive, Loader2, MemoryStick, Network, PlugZap, Server, ShieldCheck, Thermometer, WifiOff, Zap,
} from "lucide-react";
import { get, post } from "../api/client";
import { useEvents } from "../api/events";
import type { Field } from "../api/types";
import { AXIS, ChartFrame, ChartTooltip, GRID, SERIES } from "../components/charts";
import { Badge, Card, CardHeader, ConnectorBadge, KV, PageHeader, ProvenanceTag, StateView, StatusDot } from "../components/ui";
import { fmtAge, fmtBytes, fmtDateTime, fmtDuration, fmtInt, fmtMs, fmtNum, fmtPct, fmtTime, isNum } from "../lib/format";

function FieldCell({ f, fmt }: { f?: Field; fmt?: (v: any) => string }) {
  if (!f) return <span className="text-ink-3">Unavailable</span>;
  if (f.status === "unavailable") return <span className="text-ink-3" title={f.reason ?? undefined}>Unavailable</span>;
  return <span title={`source: ${f.source}`}>{fmt ? fmt(f.value) : String(f.value)}{f.unit && !fmt ? ` ${f.unit}` : ""}</span>;
}

function ProofRow({ label, f, fmt, ts }: { label: string; f?: Field; fmt?: (v: any) => string; ts?: number }) {
  return (
    <tr className="border-b border-hair last:border-0">
      <td className="table-cell text-ink-2">{label}</td>
      <td className="table-cell min-w-[200px] font-medium"><FieldCell f={f} fmt={fmt} /></td>
      <td className="table-cell mono text-ink-3">{f?.source ?? "—"}</td>
      <td className="table-cell text-ink-3 num">{ts ? fmtTime(ts) : "—"}</td>
      <td className="table-cell">{f?.status === "live" ? <Badge tone="good">available</Badge> : f?.status === "demo" ? <Badge tone="bad">demo</Badge> :
        <Badge tone="neutral" title={f?.reason ?? undefined}>unavailable</Badge>}</td>
    </tr>
  );
}

function Box({ icon, title, sub, tone = "in", className }: { icon: React.ReactNode; title: string; sub?: React.ReactNode; tone?: "in" | "out" | "hw"; className?: string }) {
  return (
    <div className={clsx("flex items-center gap-3 rounded-2xl border px-4 py-3 text-left", tone === "in" && "border-line bg-white/85",
      tone === "hw" && "border-ink/80 bg-ink text-white", tone === "out" && "border-dashed border-line bg-white/40", className)}>
      <div className={tone === "hw" ? "text-mint-soft" : "text-ink-3"}>{icon}</div>
      <div><div className="text-[13px] font-semibold">{title}</div>{sub && <div className={clsx("text-[11.5px]", tone === "hw" ? "text-white/70" : "text-ink-3")}>{sub}</div>}</div>
    </div>
  );
}

export default function Infrastructure() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["infra"], queryFn: () => get<any>("/api/infrastructure"), refetchInterval: 20_000 });
  const hist = useQuery({ queryKey: ["telemetry-history"], queryFn: () => get<any>("/api/telemetry/history?minutes=60"), refetchInterval: 30_000 });
  const ds = useQuery({ queryKey: ["datasets"], queryFn: () => get<any>("/api/datasets") });
  const { lastTelemetry: lt } = useEvents();
  const offline = useMutation({ mutationFn: () => post<any>("/api/offline/verify"), onSuccess: () => qc.invalidateQueries({ queryKey: ["infra"] }) });
  const remote = useMutation({ mutationFn: (mode: string) => post<any>("/api/remote/mode", { mode }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["infra"] }); qc.invalidateQueries({ queryKey: ["status"] }); } });

  if (q.isLoading) return <StateView kind="loading" />;
  if (q.isError) return <StateView kind="error" detail={String(q.error)} />;
  const d = q.data;
  const t = d.telemetry;
  const demo = t.mode === "demo";
  const local = d.models.find((m: any) => m.tier === "local");
  const large = d.models.find((m: any) => m.tier === "local_large");
  const off = offline.data ?? d.offline;
  const svcs = Object.entries(d.services ?? {}) as [string, any][];

  return (
    <>
      <PageHeader eyebrow="Hardware" title="Infrastructure"
        subtitle="What is physically running this product: the HP ZGX Nano, its GB10 superchip, and every model and service on it."
        right={<ProvenanceTag kind={demo ? "Demo telemetry" : "Live device telemetry"} />} />

      <Card className="mb-6 overflow-hidden">
        <div className="grid grid-cols-1 gap-0 lg:grid-cols-[1.1fr_1fr]">
          <div className="p-8">
            <div className="eyebrow mb-2">Device proof</div>
            <h2 className="text-[26px] font-semibold tracking-[-0.02em] text-ink"><FieldCell f={t.system.product} /></h2>
            <p className="mt-1 text-[14px] text-ink-2"><FieldCell f={t.system.hostname} /> · <FieldCell f={t.gpu.name} /> · <FieldCell f={t.system.architecture} /></p>
            <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-4">
              {[
                { icon: <Gauge className="h-4 w-4" />, label: "GPU utilization", v: isNum(lt?.gpu_util) ? `${lt!.gpu_util}%` : "Unavailable" },
                { icon: <Zap className="h-4 w-4" />, label: "GPU power", v: isNum(lt?.gpu_power_w) ? `${lt!.gpu_power_w.toFixed(1)} W` : "Unavailable" },
                { icon: <Thermometer className="h-4 w-4" />, label: "GPU temperature", v: isNum(lt?.gpu_temp_c) ? `${lt!.gpu_temp_c} °C` : "Unavailable" },
                { icon: <MemoryStick className="h-4 w-4" />, label: "Unified memory", v: isNum(lt?.mem_used_bytes) ? `${fmtBytes(lt!.mem_used_bytes)}` : "Unavailable" },
              ].map((x) => (
                <div key={x.label} className="rounded-2xl border border-hair bg-white/70 p-4">
                  <div className="flex items-center gap-1.5 text-[12px] text-ink-3">{x.icon}{x.label}</div>
                  <div className={clsx("mt-2 text-[22px] font-semibold tracking-tight", x.v === "Unavailable" ? "text-[15px] text-ink-3" : "text-ink")}>{x.v}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 text-[11.5px] text-ink-3">
              Live via server-sent events · last sample {lt?.ts ? fmtTime(lt.ts) : "pending"} · throughput {isNum(lt?.tokens_per_s) ? `${fmtNum(lt!.tokens_per_s, 1)} tok/s (last generation)` : "Unavailable until a generation runs"}
            </div>
          </div>
          <div className="border-t border-hair bg-white/40 p-8 lg:border-l lg:border-t-0">
            <div className="eyebrow mb-4">Trusted local boundary</div>
            <div className="flex flex-col items-stretch gap-2">
              <Box icon={<Boxes className="h-4 w-4" />} title="Application (OpenAI SDK)" sub="base_url + api_key only" tone="out" />
              <div className="mx-auto h-3 w-px bg-line" />
              <div className="rounded-3xl border-2 border-dashed border-mint/40 p-3">
                <div className="mb-2 px-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-mint">On device</div>
                <Box icon={<ShieldCheck className="h-4 w-4" />} title="NanoGate gateway" sub={`uptime ${fmtDuration(d.uptime_s)} · ${d.services.router?.ok ? "router loaded" : "router unavailable"}`} />
                <div className="my-2 grid grid-cols-3 gap-2">
                  <Box icon={<ShieldCheck className="h-4 w-4" />} title="DLP" sub={d.services.dlp?.ok ? "ready" : "down"} />
                  <Box icon={<Database className="h-4 w-4" />} title="Cache" sub={d.services.cache?.ok ? "ready" : "down"} />
                  <Box icon={<Gauge className="h-4 w-4" />} title="Router" sub={d.services.router?.ok ? d.services.router.version : "untrained"} />
                </div>
                <Box icon={<Cpu className="h-4 w-4" />} title="Local LLM" sub={`${local?.name ?? "—"} · ${local?.status?.state ?? "unknown"}`} />
                <div className="mx-auto h-2 w-px bg-line" />
                <Box icon={<Server className="h-4 w-4" />} title="HP ZGX Nano · NVIDIA GB10" sub={<FieldCell f={t.memory.unified_total} fmt={fmtBytes} />} tone="hw" />
              </div>
              <div className="mx-auto h-3 w-px border-l border-dashed border-line" />
              <div className="flex items-center justify-between gap-2">
                <Box icon={<Globe2 className="h-4 w-4" />} title="Remote provider" sub="outside the trusted boundary" tone="out" className="flex-1" />
                <ConnectorBadge mode={d.remote?.mode} state={d.remote?.state} />
              </div>
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader icon={<Cpu className="h-4 w-4" />} title="Device identity & telemetry sources" subtitle="Every field reports its source; unavailable fields state why." />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead><tr className="border-b border-hair">{["Field", "Value", "Source", "Sampled", "State"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
              <tbody>
                <ProofRow label="Product" f={t.system.product} ts={t.ts} />
                <ProofRow label="Hostname" f={t.system.hostname} ts={t.ts} />
                <ProofRow label="Architecture" f={t.system.architecture} ts={t.ts} />
                <ProofRow label="OS / kernel" f={{ ...t.system.os, value: `${t.system.os.value} · ${t.system.kernel.value}` }} ts={t.ts} />
                <ProofRow label="CPU" f={t.system.cpu_model} ts={t.ts} />
                <ProofRow label="CPU cores" f={t.system.cpu_cores} ts={t.ts} />
                <ProofRow label="GPU" f={t.gpu.name} ts={t.ts} />
                <ProofRow label="Compute capability" f={t.gpu.compute_capability} ts={t.ts} />
                <ProofRow label="Driver" f={t.gpu.driver_version} ts={t.ts} />
                <ProofRow label="CUDA" f={t.gpu.cuda_version} ts={t.ts} />
                <ProofRow label="Unified memory total" f={t.memory.unified_total} fmt={fmtBytes} ts={t.ts} />
                <ProofRow label="Unified memory used" f={t.memory.unified_used} fmt={fmtBytes} ts={t.ts} />
                <ProofRow label="GPU dedicated memory" f={t.gpu.dedicated_memory} ts={t.ts} />
                <ProofRow label="GPU utilization" f={t.gpu.utilization} ts={t.ts} />
                <ProofRow label="GPU SM clock" f={t.gpu.sm_clock} ts={t.ts} />
                <ProofRow label="GPU power" f={t.power.gpu_power} ts={t.ts} />
                <ProofRow label="GPU energy counter" f={t.power.gpu_energy_total} fmt={(v) => `${fmtNum(v / 3.6e6, 3)} kWh`} ts={t.ts} />
                <ProofRow label="Whole-system power" f={t.power.system_power} ts={t.ts} />
                <ProofRow label="GPU temperature" f={t.temperature.gpu} ts={t.ts} />
                <ProofRow label="SoC max temperature" f={t.temperature.soc_max} ts={t.ts} />
                <ProofRow label="CPU utilization" f={t.cpu.utilization} ts={t.ts} />
                <ProofRow label="Host uptime" f={{ status: "live", value: d.host_uptime_s, unit: null, source: "/proc/uptime", reason: null }} fmt={fmtDuration} ts={t.ts} />
              </tbody>
            </table>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader icon={<WifiOff className="h-4 w-4" />} title="Offline verification" subtitle="Issues a real request with all outbound network blocked, then reports what happened." />
            <div className="space-y-4 p-6">
              <button className="btn-primary" onClick={() => offline.mutate()} disabled={offline.isPending} data-testid="offline-verify">
                {offline.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <WifiOff className="h-4 w-4" />} Run Offline Verification
              </button>
              {offline.isError && <StateView kind="error" detail={String(offline.error)} />}
              {!off && !offline.isPending && <div className="text-[13px] text-ink-3">Not executed yet. No offline claim is made until it has run.</div>}
              {off && (
                <div className="animate-fadein space-y-3" data-testid="offline-result">
                  <div className={clsx("flex items-center gap-3 rounded-2xl border px-4 py-3", off.passed ? "border-mint/25 bg-mint-soft" : "border-danger/25 bg-danger-soft")}>
                    <div className={clsx("text-[20px] font-semibold", off.passed ? "text-mint" : "text-danger")}>OFFLINE CORE: {off.verdict}</div>
                  </div>
                  {off.passed && <div className="text-[13.5px] font-medium text-ink">Core AI works with internet disconnected{off.isolation_mode === "process_socket_guard" ? " (verified with a process-level network block)" : ""}.</div>}
                  <KV cols={1} rows={[
                    ["Executed", fmtDateTime(off.executed_at)], ["Isolation", off.isolation_mode === "os_disconnected" ? "Host had no default route" : "Process socket guard (host online)"],
                    ["Route / reason", `${off.route ?? "—"} · ${off.reason ?? "—"}`], ["Tokens generated", fmtInt(off.completion_tokens)],
                    ["Blocked outbound attempts", fmtInt(off.blocked_outbound_attempts?.length)], ["Duration", `${fmtNum(off.duration_s, 2)} s`],
                  ]} />
                  <div className="grid grid-cols-2 gap-1.5">{Object.entries(off.checks ?? {}).map(([k, v]) => (
                    <div key={k} className={clsx("rounded-lg px-2.5 py-1.5 text-[11.5px]", v ? "bg-mint-soft text-mint" : "bg-danger-soft text-danger")}>{v ? "✓" : "✕"} {k.replace(/_/g, " ")}</div>))}
                  </div>
                  {off.error && <div className="text-[12.5px] text-danger">{off.error}</div>}
                </div>
              )}
            </div>
          </Card>
          <Card>
            <CardHeader icon={<PlugZap className="h-4 w-4" />} title="Remote connector" subtitle="Outside the trusted boundary. Sensitive data never uses it." />
            <div className="space-y-3 p-6">
              <div className="flex items-center justify-between"><ConnectorBadge mode={d.remote?.mode} state={d.remote?.state} /><span className="text-[12px] text-ink-3">{d.remote?.reason ?? ""}</span></div>
              <div className="flex flex-wrap gap-2">
                {["disabled", "mock", "outage-test", "live"].map((m) => (
                  <button key={m} className={clsx("btn-ghost !py-1.5 !text-xs", d.remote?.mode === m && "!border-ink !bg-ink !text-white")} onClick={() => remote.mutate(m)} disabled={remote.isPending}>{m}</button>
                ))}
              </div>
              {remote.data?.state === "unavailable" && d.remote?.mode === "live" && <div className="text-[12px] text-warn">{remote.data.reason}</div>}
              <KV cols={1} rows={[
                ["Remote requests (session)", fmtInt(d.egress.remote_requests)], ["Bytes sent", fmtBytes(d.egress.remote_bytes_out)],
                ["Blocked attempts", fmtInt(d.egress.blocked_attempts)], ["Instrumentation", d.egress.instrumentation],
              ]} />
            </div>
          </Card>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader icon={<Boxes className="h-4 w-4" />} title="Models on this device" />
          <div className="divide-y divide-hair">
            {[local, large].filter(Boolean).map((m: any) => (
              <div key={m.tier} className="p-6">
                <div className="mb-3 flex items-center justify-between">
                  <div className="text-[15px] font-semibold text-ink">{m.name}</div>
                  <Badge tone={m.status?.state === "ready" ? "good" : "bad"}>{m.tier} · {m.status?.state ?? "unknown"}</Badge>
                </div>
                <KV cols={2} rows={[
                  ["Family", m.details?.family], ["Parameters", m.details?.parameter_size], ["Quantization", m.details?.quantization],
                  ["Context", fmtInt(m.details?.context_length)], ["Revision (digest)", m.revision], ["Runtime", `${m.details?.runtime ?? "—"} ${m.details?.runtime_version ?? ""}`],
                  ["Serving", m.placement?.loaded ? `KV cache ${fmtPct(m.placement.kv_cache_usage)} · ${fmtInt(m.placement.requests_running)} running / ${fmtInt(m.placement.requests_waiting)} waiting` : "unknown"],
                  ["Warm-up", fmtMs(m.warmup_ms)], ["Last tokens/s", fmtNum(m.tokens_per_s, 1)], ["Queue / in flight", `${m.queue_depth} / ${m.in_flight}`],
                  ["Size on disk", fmtBytes(m.details?.size_bytes)], ["Downloaded", m.details?.modified_at?.slice(0, 19) ?? "—"],
                ]} />
                {m.status?.reason && <div className="mt-3 text-[12px] text-danger">{m.status.reason}</div>}
              </div>
            ))}
            {d.aux_models.map((m: any) => (
              <div key={m.role} className="flex items-center justify-between gap-4 px-6 py-3 text-[13px]">
                <div className="min-w-0"><div className="font-medium text-ink">{m.role}</div><div className="mono truncate text-ink-3">{m.revision ?? m.name}</div></div>
                <div className="flex items-center gap-2"><Badge tone="neutral">{m.device}</Badge><StatusDot state={m.loaded ? "ok" : "bad"} /></div>
              </div>
            ))}
          </div>
        </Card>
        <div className="space-y-6">
          <Card className="p-6">
            {(hist.data?.rows ?? []).length < 2 ? <StateView kind="empty" title="Collecting telemetry history" detail="Samples are persisted every 10 seconds." /> : (
              <ChartFrame title="GPU power and utilization · last hour" meta={`${fmtInt(hist.data.rows.length)} samples · NVML`}>
                <ResponsiveContainer>
                  <LineChart data={hist.data.rows} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="ts" {...AXIS} tickFormatter={(v) => fmtTime(v).slice(0, 5)} type="number" domain={["dataMin", "dataMax"]} />
                    <YAxis {...AXIS} width={40} />
                    <Tooltip content={<ChartTooltip labelFmt={(l) => fmtTime(l)} fmt={(v, n) => (n.includes("W") ? `${v.toFixed(1)} W` : `${v}%`)} />} />
                    <Line dataKey="gpu_power_w" name="GPU power (W)" stroke={SERIES[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line dataKey="gpu_util" name="GPU utilization (%)" stroke={SERIES[1]} strokeWidth={2} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </ChartFrame>
            )}
          </Card>
          <Card>
            <CardHeader icon={<Server className="h-4 w-4" />} title="Services" />
            <div className="grid grid-cols-2 gap-2 p-6 md:grid-cols-3">
              {svcs.map(([k, v]) => (
                <div key={k} className="flex items-center gap-2 rounded-xl border border-hair bg-white/60 px-3 py-2 text-[12.5px]" title={v.reason ?? ""}>
                  <StatusDot state={v.ok ? "ok" : "bad"} /><span className="font-medium text-ink">{k.replace(/_/g, " ")}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card>
            <CardHeader icon={<Network className="h-4 w-4" />} title="Network & storage" />
            <div className="p-6">
              <KV cols={2} rows={[
                ["Default route", <FieldCell f={t.network.default_route} fmt={(v) => (v ? "present (online)" : "absent")} />],
                ["Interfaces up", <FieldCell f={t.network.interfaces} fmt={(v) => v.filter((i: any) => i.up).map((i: any) => i.name).join(", ") || "none"} />],
                ["Host bytes sent", <FieldCell f={t.network.bytes_sent_total} fmt={fmtBytes} />], ["Host bytes received", <FieldCell f={t.network.bytes_recv_total} fmt={fmtBytes} />],
                [<span className="inline-flex items-center gap-1"><HardDrive className="h-3.5 w-3.5" />Disk used</span>, <FieldCell f={t.storage.used} fmt={fmtBytes} />],
                ["Disk free", <FieldCell f={t.storage.free} fmt={fmtBytes} />],
              ]} />
            </div>
          </Card>
        </div>
      </div>

      <Card className="mt-6">
        <CardHeader icon={<Database className="h-4 w-4" />} title="Public data sources" subtitle="Freshness is always shown; snapshots are never presented as live." />
        <div className="grid grid-cols-1 gap-4 p-6 md:grid-cols-2 xl:grid-cols-4">
          {(ds.data?.datasets ?? []).map((x: any) => (
            <div key={x.name} className="rounded-2xl border border-hair bg-white/70 p-4">
              <div className="mb-2 flex items-center justify-between"><div className="text-[14px] font-semibold text-ink">{x.name}</div>
                {x.stale ? <Badge tone="warn">stale</Badge> : <ProvenanceTag kind="Public dataset" />}</div>
              <KV cols={1} rows={[
                ["Publisher", <span className="text-[12px]">{x.publisher}</span>], ["Version", <span className="text-[12px]">{x.version ?? "—"}</span>],
                ["Retrieved", fmtDateTime(Date.parse(x.retrieved_at) / 1000)], ["Age", fmtAge(x.age_s)], ["Rows", fmtInt(x.rows)],
                ["License", <span className="text-[12px]">{x.license}</span>], ["SHA-256", <span className="mono">{x.sha256.slice(0, 12)}…</span>],
              ]} />
              {x.catalog_released && <div className="mt-2 text-[11.5px] text-ink-3">Source updated {x.catalog_released}</div>}
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
