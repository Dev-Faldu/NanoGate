import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, Plus, Send, Trash2, Webhook } from "lucide-react";
import { del, get, post } from "../api/client";
import { Badge, Card, CardHeader, PageHeader, StateView } from "../components/ui";
import { Field, Modal, ReadOnlyNote, Toggle, errText, useMe } from "../components/kit";
import { fmtDateTime } from "../lib/format";

interface Alert { alert_id: string; ts: number; rule: string; severity: string; subject: string | null; title: string;
  detail: Record<string, unknown>; delivered: { channel: string; ok: boolean; outcome: string }[]; acknowledged_at: number | null }
interface Channel { id: string; name: string; kind: string; host: string; enabled: boolean }
interface AlertsResp { alerts: Alert[]; rules: Record<string, { enabled: boolean; percent?: number; min_requests?: number }>;
  rule_info: Record<string, { severity: string; title: string }>; channels: Channel[]; open: number }

const RULE_HELP: Record<string, string> = {
  budget_threshold: "When a department has used this share of its monthly budget (and again at 100%).",
  model_down: "When a local model stops answering, so requests would fail or fall back.",
  secret_blocked: "When someone sends a password, key or token and NanoGate blocks it.",
  spoof_attempt: "When a request claims to be another tenant or department than its key.",
  sensitive_egress: "If any bytes of sensitive requests are ever sent to a remote service.",
  chain_integrity: "Checked every 10 minutes: fires if any receipt was altered, reordered or deleted.",
  error_spike: "When at least this share of requests failed in the last 15 minutes.",
};
const SEV_TONE: Record<string, "bad" | "warn" | "info"> = { critical: "bad", warning: "warn", info: "info" };

export default function Alerts() {
  const qc = useQueryClient();
  const { canWrite } = useMe();
  const q = useQuery({ queryKey: ["alerts"], queryFn: () => get<AlertsResp>("/api/alerts"), refetchInterval: 15_000 });
  const inv = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const ack = useMutation({ mutationFn: (id: string) => post(`/api/alerts/${id}/ack`), onSuccess: inv });
  const rule = useMutation({ mutationFn: (v: { rule: string; enabled: boolean; percent?: number }) => post("/api/alerts/rules", v), onSuccess: inv });
  const removeCh = useMutation({ mutationFn: (id: string) => del(`/api/alerts/channels/${id}`), onSuccess: inv });
  const test = useMutation({ mutationFn: () => post<any>("/api/alerts/test"), onSuccess: inv });
  const [addOpen, setAddOpen] = useState(false);
  const d = q.data;
  return (
    <>
      <PageHeader eyebrow="Alerts" title="Know when something needs you"
        subtitle="NanoGate watches budgets, models, blocked secrets, impersonation, data egress and receipt integrity, and tells your team where they already work." />
      {!canWrite && <div className="mb-6"><ReadOnlyNote /></div>}
      {q.isError && <Card className="mb-6"><StateView kind="error" detail={errText(q.error)} /></Card>}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader icon={<BellRing className="h-4 w-4" />} title="Recent alerts"
            subtitle={d ? `${d.open} open` : "…"} />
          {q.isLoading ? <StateView kind="loading" /> : !d?.alerts.length ? (
            <StateView kind="empty" title="All quiet" detail="No alert has fired. Use “Send test alert” to check your channels." />
          ) : (
            <ul className="divide-y divide-hair">
              {d.alerts.map((a) => (
                <li key={a.alert_id} className="flex items-start gap-4 px-6 py-4 transition-colors hover:bg-white/50">
                  <Badge tone={SEV_TONE[a.severity] ?? "neutral"} className="mt-0.5 capitalize">{a.severity}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-ink">{a.title}{a.subject && <span className="text-ink-3"> · {a.subject}</span>}</div>
                    <div className="mt-0.5 truncate text-[12px] text-ink-3">
                      {fmtDateTime(a.ts)} · {Object.entries(a.detail).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                    </div>
                    <div className="mt-1 text-[11.5px] text-ink-3">
                      {a.delivered.length ? a.delivered.map((x) => `${x.channel} ${x.ok ? "✓" : `✗ ${x.outcome}`}`).join(" · ") : "not sent (no channel)"}
                    </div>
                  </div>
                  {a.acknowledged_at ? <span className="text-[12px] text-ink-3">acknowledged</span> : canWrite && (
                    <button className="btn-ghost !px-2.5 !py-1 !text-xs" onClick={() => ack.mutate(a.alert_id)}><Check className="h-3.5 w-3.5" />Acknowledge</button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader icon={<Webhook className="h-4 w-4" />} title="Where alerts go"
              subtitle="Slack, Microsoft Teams or any webhook. Messages carry codes and ids, never prompt text."
              right={canWrite && <button className="btn-ghost" onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" />Add</button>} />
            <div className="space-y-2 p-6">
              {!d?.channels.length && <p className="text-[13px] text-ink-3">No channel yet: alerts are only listed here.</p>}
              {d?.channels.map((c) => (
                <div key={c.id} className="glass-result flex items-center justify-between gap-3 !py-3">
                  <div className="min-w-0">
                    <div className="font-medium text-ink">{c.name}</div>
                    <div className="text-[12px] text-ink-3">{c.kind} · {c.host}</div>
                  </div>
                  {canWrite && <button className="btn-ghost !p-2" aria-label={`Remove ${c.name}`}
                    onClick={() => confirm(`Remove ${c.name}?`) && removeCh.mutate(c.id)}><Trash2 className="h-4 w-4" /></button>}
                </div>
              ))}
              {canWrite && <button className="btn-primary mt-2" disabled={test.isPending} onClick={() => test.mutate()}>
                <Send className="h-4 w-4" />Send test alert</button>}
              {test.data && <p className="text-[12.5px] text-ink-2">{test.data.note ?? test.data.delivered.map((x: any) => `${x.channel}: ${x.ok ? "delivered" : x.outcome}`).join(" · ")}</p>}
            </div>
          </Card>
          <Card>
            <CardHeader title="What to watch" subtitle="Each rule fires at most once every 15 minutes per subject." />
            <ul className="divide-y divide-hair">
              {d && Object.entries(d.rules).map(([id, r]) => (
                <li key={id} className="flex items-start gap-4 px-6 py-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 font-medium text-ink">{d.rule_info[id]?.title ?? id}
                      <Badge tone={SEV_TONE[d.rule_info[id]?.severity] ?? "neutral"} className="capitalize">{d.rule_info[id]?.severity}</Badge></div>
                    <div className="mt-0.5 text-[12.5px] text-ink-3">{RULE_HELP[id]}</div>
                    {r.percent !== undefined && (
                      <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
                        <span>At</span>
                        <input type="number" min={1} max={100} className="input !w-20 !py-1" defaultValue={r.percent} disabled={!canWrite}
                          onBlur={(e) => Number(e.target.value) !== r.percent && rule.mutate({ rule: id, enabled: r.enabled, percent: Number(e.target.value) })} />
                        <span>%</span>
                      </div>
                    )}
                  </div>
                  <Toggle on={r.enabled} disabled={!canWrite || rule.isPending} label={`Enable ${id}`}
                    onChange={(v) => rule.mutate({ rule: id, enabled: v })} />
                </li>
              ))}
            </ul>
            {rule.isError && <div className="px-6 pb-4 text-[13px] text-danger" role="alert">{errText(rule.error)}</div>}
          </Card>
        </div>
      </div>
      <AddChannel open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  );
}

function AddChannel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState("slack");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const m = useMutation({
    mutationFn: () => post("/api/alerts/channels", { name, kind, url }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); setName(""); setUrl(""); onClose(); },
  });
  return (
    <Modal open={open} onClose={onClose} title="Add alert channel"
      subtitle="Paste an incoming-webhook URL. It is stored on this device and only its host is shown afterwards.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="flex gap-2">
          {[["slack", "Slack"], ["teams", "Microsoft Teams"], ["webhook", "Webhook (JSON)"]].map(([id, l]) => (
            <button type="button" key={id} className="glass-chip" aria-pressed={kind === id} onClick={() => setKind(id)}>{l}</button>
          ))}
        </div>
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="#ai-ops" required /></Field>
        <Field label="Webhook URL" hint={kind === "webhook" ? "Receives a JSON body: rule, severity, subject, title, detail." : "Create an incoming webhook in the channel's settings."}>
          <input className="input font-mono" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.slack.com/services/…" required />
        </Field>
        {m.isError && <div role="alert" className="text-[13px] text-danger">{errText(m.error)}</div>}
        <button className="btn-primary w-full justify-center" disabled={m.isPending}>Add channel</button>
      </form>
    </Modal>
  );
}
