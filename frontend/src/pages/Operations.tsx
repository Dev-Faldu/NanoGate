import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Cpu, Download, Eraser, FileDown, Lock, LockOpen, Play, Radar, Timer } from "lucide-react";
import { download, get, post, put } from "../api/client";
import { Badge, Card, CardHeader, KV, PageHeader, StateView, StatusDot } from "../components/ui";
import { Field, ReadOnlyNote, Toggle, errText, useMe } from "../components/kit";
import { fmtBytes, fmtDateTime, fmtInt, fmtNum } from "../lib/format";

export default function Operations() {
  const { canWrite } = useMe();
  const q = useQuery({ queryKey: ["ops"], queryFn: () => get<any>("/api/ops"), refetchInterval: 30_000 });
  const d = q.data;
  return (
    <>
      <PageHeader eyebrow="Operations" title="Run it like production"
        subtitle="Encryption in transit, backups, data retention, erasure requests, audit exports and security-monitoring feeds." />
      {!canWrite && <div className="mb-6"><ReadOnlyNote /></div>}
      {q.isError && <Card className="mb-6"><StateView kind="error" detail={errText(q.error)} /></Card>}
      {q.isLoading ? <Card><StateView kind="loading" /></Card> : d && (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Tls tls={d.tls} />
          <Models />
          <Backups d={d.backups} canWrite={canWrite} />
          <Retention d={d.retention} data={d.data} canWrite={canWrite} />
          <Exports />
          <Erasure canWrite={canWrite} />
          <Siem d={d.siem} canWrite={canWrite} />
        </div>
      )}
    </>
  );
}

function Tls({ tls }: { tls: any }) {
  const https = location.protocol === "https:";
  return (
    <Card>
      <CardHeader icon={tls.enabled ? <Lock className="h-4 w-4" /> : <LockOpen className="h-4 w-4" />} title="HTTPS"
        subtitle="Encrypts API keys, prompts and answers on the network."
        right={<Badge tone={tls.enabled ? "good" : "warn"}>{tls.enabled ? "Enabled" : "Not enabled"}</Badge>} />
      <div className="space-y-3 p-6">
        {tls.enabled ? <KV cols={1} rows={[
          ["Certificate", tls.cert], ["Subject", tls.subject ?? "—"], ["Valid until", tls.not_after ? `${tls.not_after.slice(0, 10)} (${tls.days_left} days)` : "—"],
          ["Issuer", tls.self_signed ? "Self-signed (replace with your company CA for production)" : "Certificate authority"],
          ["This page", https ? "Loaded over HTTPS" : "Loaded over HTTP (use https://…)"],
        ]} /> : <>
          <p className="text-[13px] text-ink-2">The gateway is serving plain HTTP. To turn on HTTPS on this device:</p>
          <pre className="overflow-x-auto rounded-xl bg-ink/[0.04] p-3 font-mono text-[12px] leading-relaxed text-ink">{`scripts/make_tls_cert.sh            # self-signed, or use your CA's files
# then add to .env:
NANOGATE_TLS_CERT=var/tls/nanogate.crt
NANOGATE_TLS_KEY=var/tls/nanogate.key
scripts/gateway.sh restart`}</pre>
        </>}
      </div>
    </Card>
  );
}

function Models() {
  const q = useQuery({ queryKey: ["models"], queryFn: () => get<any>("/api/models"), refetchInterval: 20_000 });
  const d = q.data;
  return (
    <Card>
      <CardHeader icon={<Cpu className="h-4 w-4" />} title="Models on this device" subtitle="Served by the HP Z Runtime (vLLM) on the GB10 GPU." />
      {!d ? <StateView kind={q.isError ? "error" : "loading"} detail={q.isError ? errText(q.error) : undefined} /> : (
        <div className="space-y-3 p-6">
          {d.tiers.map((t: any) => (
            <div key={t.tier} className="glass-result flex items-center gap-3 !py-3">
              <StatusDot state={t.status?.state === "ready" ? "ok" : "bad"} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink">{t.model}</div>
                <div className="text-[12px] text-ink-3">{t.tier === "local" ? "Default tier" : "Escalation tier"} · {t.details?.parameter_size ?? ""} {t.details?.quantization ?? ""}
                  {t.tokens_per_s ? ` · ${fmtNum(t.tokens_per_s, 1)} tok/s` : ""}</div>
              </div>
              <Badge tone={t.status?.state === "ready" ? "good" : "bad"}>{t.status?.state ?? "unknown"}</Badge>
            </div>
          ))}
          <div className="text-[12px] text-ink-3">Also on-device: {d.support_models.map((m: any) => `${m.role} (${m.model})`).join(" · ")}.</div>
          <details className="text-[12.5px] text-ink-2">
            <summary className="cursor-pointer select-none font-medium">Change models</summary>
            <p className="mt-2">Models are managed by the runtime on the device. Serve a different model, then set its name in <span className="mono">.env</span>:</p>
            <pre className="mt-2 overflow-x-auto rounded-xl bg-ink/[0.04] p-3 font-mono text-[12px] leading-relaxed">{`${d.commands.status}\n${d.commands.serve}\n${d.commands.stop}`}</pre>
          </details>
        </div>
      )}
    </Card>
  );
}

function Backups({ d, canWrite }: { d: any; canWrite: boolean }) {
  const qc = useQueryClient();
  const now = useMutation({ mutationFn: () => post<any>("/api/ops/backups"), onSuccess: () => qc.invalidateQueries({ queryKey: ["ops"] }) });
  const cfg = useMutation({ mutationFn: (v: any) => put("/api/ops/backups/config", v), onSuccess: () => qc.invalidateQueries({ queryKey: ["ops"] }) });
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader icon={<Archive className="h-4 w-4" />} title="Backups"
        subtitle="Database, key pepper, receipt signing key and policies: everything needed to restore with valid keys and receipts."
        right={canWrite && <button className="btn-primary" disabled={now.isPending} onClick={() => now.mutate()}>{now.isPending ? "Backing up…" : "Back up now"}</button>} />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
          <Toggle on={d.config.scheduled} disabled={!canWrite} label="Scheduled backups" onChange={(v) => cfg.mutate({ ...d.config, scheduled: v })} />
          <span>Automatic every</span>
          <input type="number" className="input !w-20 !py-1" min={1} max={720} defaultValue={d.config.every_hours} disabled={!canWrite}
            onBlur={(e) => Number(e.target.value) !== d.config.every_hours && cfg.mutate({ ...d.config, every_hours: Number(e.target.value) })} />
          <span>hours, keep</span>
          <input type="number" className="input !w-20 !py-1" min={1} max={365} defaultValue={d.config.keep} disabled={!canWrite}
            onBlur={(e) => Number(e.target.value) !== d.config.keep && cfg.mutate({ ...d.config, keep: Number(e.target.value) })} />
        </div>
        {d.items.length === 0 ? <p className="text-[13px] text-ink-3">No backup yet.</p> : (
          <ul className="divide-y divide-hair rounded-2xl border border-white/80 bg-white/40">
            {d.items.slice(0, 8).map((b: any) => (
              <li key={b.backup_id} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                <span className="min-w-0 flex-1 truncate">{fmtDateTime(b.ts)} <span className="text-ink-3">· {b.kind} · {fmtBytes(b.bytes)}</span></span>
                <span className="mono hidden text-ink-3 md:inline" title={b.sha256}>{b.sha256.slice(0, 10)}…</span>
                {canWrite && b.present && <button className="btn-ghost !px-2 !py-1 !text-xs" onClick={() =>
                  download(`/api/ops/backups/${b.backup_id}/download`, b.filename).catch((e) => setErr(errText(e)))}>
                  <Download className="h-3.5 w-3.5" />Download</button>}
              </li>
            ))}
          </ul>
        )}
        <p className="text-[12px] text-ink-3">Stored in <span className="mono">{d.directory}</span> (owner-only). Restore with <span className="mono">scripts/restore.sh &lt;file&gt;</span>. Copy backups off the device for disaster recovery.</p>
        {[now, cfg].map((m, i) => m.isError && <p key={i} role="alert" className="text-[13px] text-danger">{errText(m.error)}</p>)}
        {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      </div>
    </Card>
  );
}

function Retention({ d, data, canWrite }: { d: any; data: any; canWrite: boolean }) {
  const qc = useQueryClient();
  const run = useMutation({ mutationFn: () => post<any>("/api/ops/retention/run"), onSuccess: () => qc.invalidateQueries({ queryKey: ["ops"] }) });
  const cfg = useMutation({ mutationFn: (v: any) => put("/api/ops/retention/config", v), onSuccess: () => qc.invalidateQueries({ queryKey: ["ops"] }) });
  return (
    <Card>
      <CardHeader icon={<Timer className="h-4 w-4" />} title="Retention"
        subtitle="Old records are deleted per department policy. Old receipts keep their hash so the chain still verifies."
        right={canWrite && <button className="btn-ghost" disabled={run.isPending} onClick={() => run.mutate()}><Play className="h-4 w-4" />Run now</button>} />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
          <Toggle on={d.config.enabled} disabled={!canWrite} label="Automatic retention" onChange={(v) => cfg.mutate({ ...d.config, enabled: v })} />
          <span>Automatic hourly cleanup · GPU telemetry kept</span>
          <input type="number" className="input !w-20 !py-1" min={1} max={3650} defaultValue={d.config.telemetry_days} disabled={!canWrite}
            onBlur={(e) => Number(e.target.value) !== d.config.telemetry_days && cfg.mutate({ ...d.config, telemetry_days: Number(e.target.value) })} />
          <span>days</span>
        </div>
        <table className="w-full">
          <thead className="border-b border-hair"><tr>{["Department", "Policy", "Keep for", "Prompt text"].map((h) => <th key={h} className="table-head !px-2">{h}</th>)}</tr></thead>
          <tbody>{d.departments.map((r: any) => (
            <tr key={r.department} className="border-b border-hair last:border-0">
              <td className="table-cell !px-2">{r.department}</td><td className="table-cell !px-2 text-ink-2">{r.policy}</td>
              <td className="table-cell !px-2 num">{fmtInt(r.receipt_days)} days</td>
              <td className="table-cell !px-2">{r.store_raw_prompts ? <Badge tone="warn">stored</Badge> : <Badge tone="good">never stored</Badge>}</td>
            </tr>))}</tbody>
        </table>
        <KV cols={2} rows={[
          ["Requests on record", fmtInt(data.requests)], ["Receipts (pruned)", `${fmtInt(data.receipts)} (${fmtInt(data.receipts_pruned)})`],
          ["Oldest request", data.oldest_request ? fmtDateTime(data.oldest_request) : "—"], ["Database size", fmtBytes(data.db_bytes)],
          ["Last cleanup", d.last_run ? `${fmtDateTime(d.last_run.ts)} · ${d.last_run.requests_deleted} deleted` : "not yet in this session"],
          ["Prompts stored", fmtInt(data.raw_prompts_stored)],
        ]} />
        {run.data && <p className="text-[12.5px] text-ink-2">Done: {run.data.requests_deleted} requests deleted, {run.data.receipts_pruned} receipts pruned.</p>}
      </div>
    </Card>
  );
}

function Exports() {
  const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null);
  const since = Math.floor(Date.now() / 1000 - days * 86400);
  return (
    <Card>
      <CardHeader icon={<FileDown className="h-4 w-4" />} title="Exports for audit"
        subtitle="Request metadata for spreadsheets, and sealed receipts that auditors can verify independently. No prompt text." />
      <div className="space-y-4 p-6">
        <Field label="Period">
          <select className="input !w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[[1, "Last 24 hours"], [7, "Last 7 days"], [30, "Last 30 days"], [365, "Last 12 months"], [36500, "Everything"]].map(([v, l]) =>
              <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => download(`/api/export/requests.csv?since=${since}`, "nanogate-requests.csv").catch((e) => setErr(errText(e)))}>
            <Download className="h-4 w-4" />Requests (CSV)</button>
          <button className="btn-ghost" onClick={() => download(`/api/export/receipts.jsonl?since=${since}`, "nanogate-receipts.jsonl").catch((e) => setErr(errText(e)))}>
            <Download className="h-4 w-4" />Receipts (JSON Lines)</button>
        </div>
        <p className="text-[12px] text-ink-3">Each receipt line has its canonical body, previous hash, hash and HMAC: hash = SHA-256(previous hash + body). Exports are recorded in the activity log.</p>
        {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      </div>
    </Card>
  );
}

function Erasure({ canWrite }: { canWrite: boolean }) {
  const [mode, setMode] = useState<"key" | "dept">("key");
  const [keyId, setKeyId] = useState("");
  const [td, setTd] = useState("");
  const [typed, setTyped] = useState("");
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => get<any>("/api/keys"), enabled: canWrite });
  const m = useMutation({
    mutationFn: () => {
      const [tenant_id, department_id] = td.split("/");
      return post<any>("/api/ops/erase", mode === "key" ? { key_id: keyId, confirm: typed } : { tenant_id, department_id, confirm: typed });
    },
    onSuccess: () => setTyped(""),
  });
  const depts = Array.from(new Set((keys.data?.keys ?? []).map((k: any) => `${k.tenant_id}/${k.department_id}`))) as string[];
  return (
    <Card>
      <CardHeader icon={<Eraser className="h-4 w-4" />} title="Right to erasure"
        subtitle="Delete everything recorded for one person or app (its key), or for a whole department: requests, receipt bodies and cached answers." />
      <div className="space-y-4 p-6">
        {!canWrite ? <p className="text-[13px] text-ink-3">Only admins can erase data.</p> : <>
          <div className="flex gap-2">
            <button className="glass-chip" aria-pressed={mode === "key"} onClick={() => setMode("key")}>A person or app</button>
            <button className="glass-chip" aria-pressed={mode === "dept"} onClick={() => setMode("dept")}>A department</button>
          </div>
          {mode === "key" ? (
            <Field label="Key"><select className="input" value={keyId} onChange={(e) => setKeyId(e.target.value)}>
              <option value="">Choose…</option>
              {(keys.data?.keys ?? []).map((k: any) => <option key={k.key_id} value={k.key_id}>{k.label} · {k.tenant_id}/{k.department_id} · {k.requests} requests</option>)}
            </select></Field>
          ) : (
            <Field label="Department"><select className="input" value={td} onChange={(e) => setTd(e.target.value)}>
              <option value="">Choose…</option>{depts.map((d) => <option key={d}>{d}</option>)}</select></Field>
          )}
          <Field label="Type ERASE to confirm" hint="This cannot be undone. Receipt hashes stay so the audit chain still verifies.">
            <input className="input font-mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="ERASE" />
          </Field>
          <button className="btn-primary" disabled={m.isPending || typed !== "ERASE" || (mode === "key" ? !keyId : !td)} onClick={() => m.mutate()}>
            <Eraser className="h-4 w-4" />Erase</button>
          {m.data && <p className="text-[12.5px] text-mint">Erased {m.data.target}: {m.data.requests_deleted} requests, {m.data.receipts_pruned} receipt bodies, {m.data.cache_entries_deleted} cached answers.</p>}
          {m.isError && <p role="alert" className="text-[13px] text-danger">{errText(m.error)}</p>}
        </>}
      </div>
    </Card>
  );
}

function Siem({ d, canWrite }: { d: any; canWrite: boolean }) {
  const qc = useQueryClient();
  const [host, setHost] = useState(d.host);
  const [port, setPort] = useState(d.port);
  const [proto, setProto] = useState(d.protocol);
  const save = useMutation({ mutationFn: (enabled: boolean) => put("/api/ops/siem", { enabled, host, port: Number(port), protocol: proto }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ops"] }) });
  const test = useMutation({ mutationFn: () => post<any>("/api/ops/siem/test") });
  return (
    <Card>
      <CardHeader icon={<Radar className="h-4 w-4" />} title="Security monitoring (SIEM)"
        subtitle="Stream decisions, alerts and admin changes to Splunk, Sentinel, QRadar or any syslog collector."
        right={<Badge tone={d.enabled ? "good" : "neutral"}>{d.enabled ? `On · ${fmtInt(d.sent)} sent` : "Off"}</Badge>} />
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-[1fr_100px_110px] gap-3">
          <Field label="Collector host"><input className="input" value={host} onChange={(e) => setHost(e.target.value)} placeholder="siem.corp.local" disabled={!canWrite} /></Field>
          <Field label="Port"><input className="input" type="number" value={port} onChange={(e) => setPort(e.target.value)} disabled={!canWrite} /></Field>
          <Field label="Protocol"><select className="input" value={proto} onChange={(e) => setProto(e.target.value)} disabled={!canWrite}>
            <option value="udp">UDP</option><option value="tcp">TCP</option></select></Field>
        </div>
        {canWrite && <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={save.isPending || !host} onClick={() => save.mutate(true)}>{d.enabled ? "Save" : "Turn on"}</button>
          {d.enabled && <button className="btn-ghost" onClick={() => save.mutate(false)}>Turn off</button>}
          {d.enabled && <button className="btn-ghost" onClick={() => test.mutate()}>Send test event</button>}
        </div>}
        <p className="text-[12px] text-ink-3">RFC 5424 syslog with a JSON message per event (receipt sealed, policy published, service state, alert, admin change). Metadata only.</p>
        {test.data && <p className="text-[12.5px] text-ink-2">{test.data.sent ? "Test event sent." : `Not sent: ${test.data.error}`}</p>}
        {d.last_error && <p className="text-[12.5px] text-warn">Last error: {d.last_error}</p>}
        {save.isError && <p role="alert" className="text-[13px] text-danger">{errText(save.error)}</p>}
      </div>
    </Card>
  );
}
