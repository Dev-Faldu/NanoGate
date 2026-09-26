import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, KeyRound, Plus, ShieldOff, UserRound, Users } from "lucide-react";
import { get, post } from "../api/client";
import { Badge, Card, CardHeader, CopyButton, Hint, PageHeader, StateView, Tabs } from "../components/ui";
import { Bar, Field, Modal, ReadOnlyNote, errText, useMe } from "../components/kit";
import { fmtAge, fmtDateTime, fmtInt, fmtUsd } from "../lib/format";

interface Dept { department_id: string; display_name: string; policy_id: string; policy_version: string; keys: number;
  budget_usd: number; spent_usd: number; remote_allowed: boolean; retention_days: number }
interface Tenant { tenant_id: string; display_name: string; departments: Dept[] }
interface KeyRow { key_id: string; prefix: string; tenant_id: string; department_id: string; label: string | null; kind: string;
  state: string; created_at: number; expires_at: number | null; revoked_at: number | null; last_used: number | null; requests: number }
interface AuditRow { audit_id: number; ts: number; actor_key_id: string | null; actor_label: string | null; source: string;
  action: string; target: string | null; detail: Record<string, unknown> }

const KIND_INFO: Record<string, { label: string; tone: "info" | "lilac" | "cyan" | "neutral"; help: string }> = {
  app: { label: "App", tone: "info", help: "An application calling the OpenAI-compatible API (/v1)." },
  person: { label: "Person", tone: "cyan", help: "An employee signing in to the NanoGate chat app." },
  auditor: { label: "Auditor", tone: "lilac", help: "Read-only access to this dashboard: sees everything, changes nothing." },
  admin: { label: "Admin", tone: "neutral", help: "Full dashboard access, including keys, policies and data erasure." },
};

export default function Access() {
  const [tab, setTab] = useState<"keys" | "org" | "activity">("keys");
  const { canWrite } = useMe();
  return (
    <>
      <PageHeader eyebrow="Access" title="People, apps and departments"
        subtitle="Give each app, employee and auditor their own key, organise departments, and see every administrative change." />
      {!canWrite && <div className="mb-6"><ReadOnlyNote /></div>}
      <div className="mb-6">
        <Tabs value={tab} onChange={setTab} items={[{ id: "keys", label: "API keys" }, { id: "org", label: "Departments" },
          { id: "activity", label: "Activity log" }]} />
      </div>
      {tab === "keys" && <Keys canWrite={canWrite} />}
      {tab === "org" && <Org canWrite={canWrite} />}
      {tab === "activity" && <Activity />}
    </>
  );
}

function useOrg() {
  return useQuery({ queryKey: ["org"], queryFn: () => get<{ tenants: Tenant[]; policies: string[] }>("/api/org") });
}

function Keys({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["keys"], queryFn: () => get<{ keys: KeyRow[]; you: string }>("/api/keys") });
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const revoke = useMutation({
    mutationFn: (id: string) => post(`/api/keys/${id}/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keys"] }),
  });
  const rows = (q.data?.keys ?? []).filter((k) => filter === "all" || k.state === "active");
  return (
    <Card>
      <CardHeader icon={<KeyRound className="h-4 w-4" />} title="API keys"
        subtitle="Each key carries its department, so every request is attributed, governed and billed correctly."
        right={<>
          <Tabs value={filter} onChange={setFilter} items={[{ id: "active", label: "Active" }, { id: "all", label: "All" }]} />
          {canWrite && <button className="btn-primary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" />New key</button>}
        </>} />
      {q.isLoading ? <StateView kind="loading" /> : q.isError ? <StateView kind="error" detail={errText(q.error)} /> :
        rows.length === 0 ? <StateView kind="empty" title="No keys" detail="Create a key for each app or person that uses NanoGate." /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead className="border-b border-hair"><tr>
                {["Name", "Kind", "Department", "Key", "Last used", "Requests", "State", ""].map((h) => <th key={h} className="table-head">{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.map((k) => {
                  const info = KIND_INFO[k.kind] ?? KIND_INFO.app;
                  return (
                    <tr key={k.key_id} className="border-b border-hair last:border-0 transition-colors hover:bg-white/60">
                      <td className="table-cell font-medium">{(k.label ?? "").split(":").slice(1).join(":") || k.label || "—"}
                        {k.key_id === q.data?.you && <Badge tone="good" className="ml-2">you</Badge>}</td>
                      <td className="table-cell"><Hint as="span" className="inline-flex" title={info.label} text={info.help}>
                        <Badge tone={info.tone}>{info.label}</Badge></Hint></td>
                      <td className="table-cell">{k.tenant_id}/{k.department_id}</td>
                      <td className="table-cell mono text-ink-2">{k.prefix}…</td>
                      <td className="table-cell text-ink-2">{k.last_used ? `${fmtAge(Date.now() / 1000 - k.last_used)} ago` : "never"}</td>
                      <td className="table-cell num">{fmtInt(k.requests)}</td>
                      <td className="table-cell">
                        <Badge tone={k.state === "active" ? "good" : k.state === "expired" ? "warn" : "neutral"}>{k.state}</Badge>
                        {k.expires_at && k.state === "active" && <span className="ml-2 text-[11.5px] text-ink-3">until {fmtDateTime(k.expires_at)}</span>}
                      </td>
                      <td className="table-cell text-right">
                        {canWrite && k.state === "active" && k.key_id !== q.data?.you && (
                          <button className="btn-ghost !px-2.5 !py-1 !text-xs" disabled={revoke.isPending}
                            onClick={() => confirm(`Revoke "${k.label}"? Apps using it stop working immediately.`) && revoke.mutate(k.key_id)}>
                            <ShieldOff className="h-3.5 w-3.5" />Revoke
                          </button>)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      {revoke.isError && <div className="px-6 pb-4 text-[13px] text-danger" role="alert">{errText(revoke.error)}</div>}
      <NewKey open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}

function NewKey({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const org = useOrg();
  const [tenant, setTenant] = useState("acme");
  const [dept, setDept] = useState("it");
  const [kind, setKind] = useState("app");
  const [name, setName] = useState("");
  const [days, setDays] = useState("");
  const m = useMutation({
    mutationFn: () => post<any>("/api/keys", { tenant_id: tenant, department_id: dept, kind, name, days: days ? Number(days) : null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keys"] }),
  });
  const depts = org.data?.tenants.find((t) => t.tenant_id === tenant)?.departments ?? [];
  const close = () => { m.reset(); setName(""); setDays(""); onClose(); };
  const base = `${location.protocol}//${location.host}/v1`;
  return (
    <Modal open={open} onClose={close} title={m.data ? "Key created" : "New API key"}
      subtitle={m.data ? "Copy it now: NanoGate stores only a keyed hash and cannot show it again." : "The key decides the department, policy and budget for every request made with it."}>
      {m.data ? (
        <div className="space-y-4">
          <div className="glass-result">
            <div className="eyebrow mb-1">{m.data.label} · {m.data.tenant_id}/{m.data.department_id}</div>
            <div className="break-all font-mono text-[13px] text-ink">{m.data.api_key}</div>
            <div className="mt-3"><CopyButton text={m.data.api_key} label="Copy key" /></div>
          </div>
          {m.data.kind === "app" && <>
            <div className="text-[12.5px] font-medium text-ink-2">Use it from any OpenAI SDK</div>
            <pre className="overflow-x-auto rounded-xl bg-ink/[0.04] p-3 font-mono text-[12px] leading-relaxed text-ink">{`from openai import OpenAI
client = OpenAI(base_url="${base}", api_key="<this key>")
r = client.chat.completions.create(model="nanogate-auto",
    messages=[{"role": "user", "content": "Hello"}])`}</pre>
          </>}
          {m.data.kind === "person" && <p className="text-[13px] text-ink-2">Give it to the employee: they sign in at <span className="mono">{location.host}/chat</span>.</p>}
          <button className="btn-primary w-full justify-center" onClick={close}>Done, I saved it</button>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(KIND_INFO).map(([id, k]) => (
              <button type="button" key={id} onClick={() => setKind(id)} aria-pressed={kind === id}
                className="glass-chip !rounded-xl !px-3 !py-2 text-left">
                <div className="font-semibold">{k.label}</div>
                <div className={kind === id ? "text-white/75" : "text-ink-3"}>{k.help}</div>
              </button>
            ))}
          </div>
          <Field label={kind === "person" ? "Person's name" : kind === "app" ? "App name" : "Name"}>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60}
              placeholder={kind === "person" ? "Priya Shah" : "helpdesk-bot"} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Tenant">
              <select className="input" value={tenant} onChange={(e) => { setTenant(e.target.value); setDept(org.data?.tenants.find((t) => t.tenant_id === e.target.value)?.departments[0]?.department_id ?? ""); }}>
                {org.data?.tenants.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name}</option>)}
              </select>
            </Field>
            <Field label="Department">
              <select className="input" value={dept} onChange={(e) => setDept(e.target.value)}>
                {depts.map((d) => <option key={d.department_id} value={d.department_id}>{d.display_name}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Expires after (days)" hint="Leave empty for no expiry.">
            <input className="input" type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} placeholder="90" />
          </Field>
          {m.isError && <div role="alert" className="text-[13px] text-danger">{errText(m.error)}</div>}
          <button className="btn-primary w-full justify-center" disabled={m.isPending || !name.trim() || !dept}>Create key</button>
        </form>
      )}
    </Modal>
  );
}

function Org({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const q = useOrg();
  const [addDept, setAddDept] = useState<string | null>(null);
  const [addTenant, setAddTenant] = useState(false);
  const move = useMutation({
    mutationFn: (v: { tenant_id: string; department_id: string; policy_id: string }) => post("/api/org/departments/policy", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org"] }),
  });
  if (q.isLoading) return <Card><StateView kind="loading" /></Card>;
  if (q.isError) return <Card><StateView kind="error" detail={errText(q.error)} /></Card>;
  return (
    <div className="space-y-6">
      {q.data!.tenants.map((t) => (
        <Card key={t.tenant_id}>
          <CardHeader icon={<Building2 className="h-4 w-4" />} eyebrow={`Tenant · ${t.tenant_id}`} title={t.display_name}
            subtitle="Tenants never share cached answers, knowledge or data with each other."
            right={canWrite && <button className="btn-ghost" onClick={() => setAddDept(t.tenant_id)}><Plus className="h-4 w-4" />Department</button>} />
          <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4 p-6">
            {t.departments.map((d) => {
              const used = d.budget_usd ? d.spent_usd / d.budget_usd : 0;
              return (
                <div key={d.department_id} className="glass-result panel-hover space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-semibold text-ink">{d.display_name}</div>
                      <div className="mono text-ink-3">{t.tenant_id}/{d.department_id}</div>
                    </div>
                    <Badge tone={d.remote_allowed ? "info" : "lilac"}>{d.remote_allowed ? "Remote allowed" : "On-device only"}</Badge>
                  </div>
                  <div>
                    <div className="mb-1 flex justify-between text-[12px] text-ink-2">
                      <span>Budget this month</span><span className="num">{fmtUsd(d.spent_usd)} / {fmtUsd(d.budget_usd, 0)}</span>
                    </div>
                    <Bar value={used} tone={used >= 1 ? "bad" : used >= 0.8 ? "warn" : "ink"} />
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-[12px] text-ink-2">
                    <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" />{d.keys} keys</span>
                    <span>Receipts kept {d.retention_days} days</span>
                  </div>
                  <Field label="Policy">
                    <select className="input" value={d.policy_id} disabled={!canWrite || move.isPending}
                      onChange={(e) => confirm(`Move ${d.display_name} to the "${e.target.value}" policy? It applies to the next request.`)
                        && move.mutate({ tenant_id: t.tenant_id, department_id: d.department_id, policy_id: e.target.value })}>
                      {q.data!.policies.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </Field>
                </div>
              );
            })}
          </div>
        </Card>
      ))}
      {canWrite && <button className="btn-ghost" onClick={() => setAddTenant(true)}><Plus className="h-4 w-4" />Add tenant</button>}
      {move.isError && <div className="text-[13px] text-danger" role="alert">{errText(move.error)}</div>}
      <NewDept tenant={addDept} policies={q.data!.policies} onClose={() => setAddDept(null)} />
      <NewTenant open={addTenant} onClose={() => setAddTenant(false)} />
    </div>
  );
}

function NewDept({ tenant, policies, onClose }: { tenant: string | null; policies: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [policy, setPolicy] = useState("it");
  const m = useMutation({
    mutationFn: () => post("/api/org/departments", { tenant_id: tenant, department_id: id, display_name: name, policy_id: policy }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["org"] }); setId(""); setName(""); onClose(); },
  });
  return (
    <Modal open={!!tenant} onClose={onClose} title="Add department" subtitle={`In tenant ${tenant}. Its policy decides routes, privacy rules, budget and retention.`}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Field label="Display name"><input className="input" value={name} required onChange={(e) => {
          setName(e.target.value);
          setId(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32));
        }} placeholder="Finance" /></Field>
        <Field label="Department id" hint="Lowercase; appears in keys, receipts and reports."><input className="input font-mono" value={id} required
          onChange={(e) => setId(e.target.value)} /></Field>
        <Field label="Policy"><select className="input" value={policy} onChange={(e) => setPolicy(e.target.value)}>
          {policies.map((p) => <option key={p} value={p}>{p}</option>)}</select></Field>
        {m.isError && <div role="alert" className="text-[13px] text-danger">{errText(m.error)}</div>}
        <button className="btn-primary w-full justify-center" disabled={m.isPending || !id || !name}>Add department</button>
      </form>
    </Modal>
  );
}

function NewTenant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const m = useMutation({
    mutationFn: () => post("/api/org/tenants", { tenant_id: id, display_name: name }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["org"] }); setId(""); setName(""); onClose(); },
  });
  return (
    <Modal open={open} onClose={onClose} title="Add tenant" subtitle="A separate company or business unit, fully isolated from the others.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Field label="Name"><input className="input" value={name} required onChange={(e) => {
          setName(e.target.value);
          setId(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32));
        }} placeholder="Initech" /></Field>
        <Field label="Tenant id"><input className="input font-mono" value={id} required onChange={(e) => setId(e.target.value)} /></Field>
        {m.isError && <div role="alert" className="text-[13px] text-danger">{errText(m.error)}</div>}
        <button className="btn-primary w-full justify-center" disabled={m.isPending || !id || !name}>Add tenant</button>
      </form>
    </Modal>
  );
}

const ACTION_LABEL: Record<string, string> = {
  "key.create": "Created key", "key.revoke": "Revoked key", "department.create": "Added department", "tenant.create": "Added tenant",
  "department.policy": "Changed department policy", "policy.budget": "Changed budget", "remote.mode": "Switched remote connector",
  "alerts.channel.add": "Added alert channel", "alerts.channel.remove": "Removed alert channel", "alerts.rule": "Changed alert rule",
  "backup.create": "Backup", "backup.download": "Downloaded backup", "backup.config": "Changed backup schedule",
  "privacy.erase": "Erased data", "retention.prune": "Retention cleanup", "retention.config": "Changed retention",
  "knowledge.create": "Created knowledge source", "knowledge.document.add": "Uploaded document",
  "knowledge.document.remove": "Removed document", "knowledge.revoke": "Revoked knowledge source",
  "knowledge.restore": "Restored knowledge source", "knowledge.delete": "Deleted knowledge source",
  "export.requests": "Exported requests", "export.receipts": "Exported receipts", "siem.config": "Changed SIEM forwarding",
};

function Activity() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => get<{ entries: AuditRow[] }>("/api/audit?limit=300"), refetchInterval: 20_000 });
  const rows = q.data?.entries ?? [];
  const sources = useMemo(() => ({ dashboard: "Dashboard", assistant: "Assistant", system: "Automatic" } as Record<string, string>), []);
  return (
    <Card>
      <CardHeader icon={<UserRound className="h-4 w-4" />} title="Activity log"
        subtitle="Every administrative change, who made it and where. Secrets and prompt text are never recorded here." />
      {q.isLoading ? <StateView kind="loading" /> : rows.length === 0 ? <StateView kind="empty" title="No changes yet" /> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-hair"><tr>{["When", "What", "Target", "By", "Via", "Details"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.audit_id} className="border-b border-hair last:border-0 hover:bg-white/60">
                  <td className="table-cell whitespace-nowrap text-ink-2">{fmtDateTime(r.ts)}</td>
                  <td className="table-cell font-medium">{ACTION_LABEL[r.action] ?? r.action}</td>
                  <td className="table-cell mono text-ink-2">{r.target ?? "—"}</td>
                  <td className="table-cell text-ink-2">{r.actor_label ?? (r.actor_key_id ? `key ${r.actor_key_id}` : "NanoGate")}</td>
                  <td className="table-cell"><Badge tone={r.source === "assistant" ? "lilac" : r.source === "system" ? "neutral" : "info"}>{sources[r.source] ?? r.source}</Badge></td>
                  <td className="table-cell max-w-[340px] truncate text-[12px] text-ink-3" title={JSON.stringify(r.detail)}>
                    {Object.entries(r.detail).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
