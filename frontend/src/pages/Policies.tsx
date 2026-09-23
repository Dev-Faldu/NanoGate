import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowRight, FileCog, History, Loader2, Play, ShieldCheck, Upload } from "lucide-react";
import { ApiError, get, post } from "../api/client";
import { Badge, Card, CardHeader, DataClassBadge, Explainer, KV, PageHeader, ReasonBadge, StateView, Tabs } from "../components/ui";
import { fmtDateTime, fmtInt, fmtUsd } from "../lib/format";

interface PolicyItem {
  policy_id: string;
  current_version: string;
  body: any;
  departments: string[];
  versions: { version: number; version_tag: string; status: string; body_sha256: string; published_at: number; published_by: string }[];
}

const PRESETS = [
  { label: "Clean IT question", dept: "it", text: "How do I reset the VPN client?" },
  { label: "HR with PII", dept: "hr", text: "Update the address for employee Maria Lopez (EMP-204981), email maria.lopez@acme-corp.example, SSN 219-09-9999." },
  { label: "Sales customer email", dept: "sales", text: "Write a follow-up to jordan.baker@example.com about the Q3 renewal, phone 415-555-0132." },
  // Synthetic credential, assembled at runtime so the source never contains a literal secret pattern.
  { label: "Leaked credential", dept: "security", text: `Why does this fail? ${"postgres"}://svc_demo:${"Synthetic" + "Pw9"}@db.internal:5432/prod` },
];

function Node({ label, value, tone, sub }: { label: string; value: React.ReactNode; tone?: "good" | "bad" | "warn" | "lilac"; sub?: React.ReactNode }) {
  return (
    <div className={clsx("min-w-[150px] flex-1 rounded-2xl border px-4 py-3", tone === "good" ? "border-mint/25 bg-mint-soft/60" :
      tone === "bad" ? "border-danger/25 bg-danger-soft" : tone === "warn" ? "border-warn/25 bg-warn-soft/70" : tone === "lilac" ? "border-lilac/25 bg-lilac-soft/70" : "border-line bg-white/70")}>
      <div className="eyebrow mb-1">{label}</div>
      <div className="text-[14px] font-semibold text-ink">{value}</div>
      {sub && <div className="mt-1 text-[11.5px] text-ink-3">{sub}</div>}
    </div>
  );
}

export default function Policies() {
  const qc = useQueryClient();
  const pq = useQuery({ queryKey: ["policies"], queryFn: () => get<{ policies: PolicyItem[] }>("/api/policies") });
  const [sel, setSel] = useState("hr");
  const [dept, setDept] = useState("hr");
  const [prompt, setPrompt] = useState(PRESETS[1].text);
  const [tab, setTab] = useState<"test" | "edit" | "history">("test");
  const [draft, setDraft] = useState("");
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const test = useMutation({ mutationFn: () => post<any>("/api/policies/test", { tenant: "acme", department: dept, prompt }) });
  const publish = useMutation({
    mutationFn: (body: any) => post<any>("/api/policies/publish", { policy_id: sel, body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["policies"] }); setDraftErr(null); },
    onError: (e) => setDraftErr(e instanceof ApiError ? e.message : String(e)),
  });
  const policy = pq.data?.policies.find((p) => p.policy_id === sel);
  useEffect(() => { if (policy) setDraft(JSON.stringify(policy.body, null, 2)); }, [policy?.current_version]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = test.data;
  return (
    <>
      <PageHeader eyebrow="Governance" title="Policy Studio"
        subtitle="Versioned, typed policies decide what data may go where. One engine evaluates every request; this page runs that same engine." />
      {pq.isLoading ? <StateView kind="loading" /> : pq.isError ? <StateView kind="error" detail={String(pq.error)} /> : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[360px_1fr]">
          <div className="space-y-3">
            {pq.data!.policies.map((p) => (
              <button key={p.policy_id} onClick={() => { setSel(p.policy_id); const d = p.departments[0]?.split("/")[1]; if (d) setDept(d); }}
                className={clsx("panel panel-hover w-full p-5 text-left", sel === p.policy_id && "!border-peri/40 ring-2 ring-peri/15")}>
                <div className="flex items-center justify-between">
                  <div className="text-[15px] font-semibold capitalize text-ink">{p.policy_id}</div>
                  <Badge tone="good">{p.current_version}</Badge>
                </div>
                <p className="mt-1 line-clamp-2 text-[12.5px] text-ink-2">{p.body.description}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {p.body.routes.map((r: string) => <Badge key={r} tone={r === "remote" ? (p.body.remote.enabled ? "info" : "neutral") : "neutral"}>{r}</Badge>)}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-[11.5px] text-ink-3">
                  <span>PII → <b className="font-semibold text-ink-2">{p.body.dlp.pii_action}</b></span>
                  <span>Secrets → <b className="font-semibold text-ink-2">{p.body.dlp.secret_action}</b></span>
                  <span>Remote ≤ <b className="font-semibold text-ink-2">{p.body.remote.enabled ? p.body.remote.max_data_class : "never"}</b></span>
                  <span>Budget <b className="font-semibold text-ink-2">{fmtUsd(p.body.budget.monthly_usd, 0)}/mo</b></span>
                </div>
                <div className="mt-2 text-[11.5px] text-ink-3">{p.departments.join(" · ")}</div>
              </button>
            ))}
          </div>

          <div className="space-y-6">
            <Card>
              <CardHeader icon={<ShieldCheck className="h-4 w-4" />} eyebrow="Rule graph" title={`How ${sel} requests are decided`}
                subtitle={t ? "Showing the result of your last test run." : "Run a test prompt to light up the graph with a real evaluation."} />
              <div className="flex flex-wrap items-stretch gap-2 p-6">
                <Node label="Request" value={t ? `${prompt.slice(0, 28)}…` : "Incoming"} sub={`acme / ${dept}`} />
                <ArrowRight className="hidden h-4 w-4 self-center text-ink-3 md:block" />
                <Node label="Classification" value={t ? <DataClassBadge value={t.classification} /> : "DLP scan"}
                  tone={t && ["Confidential", "Restricted", "Secret"].includes(t.classification) ? "lilac" : t ? "good" : undefined}
                  sub={t ? Object.entries(t.findings).map(([k, n]) => `${k}×${n}`).join(", ") || "no entities" : "regex · secrets · Presidio"} />
                <ArrowRight className="hidden h-4 w-4 self-center text-ink-3 md:block" />
                <Node label="Policy" value={t?.policy_version ?? policy?.current_version} sub={policy?.body.description?.slice(0, 60)} />
                <ArrowRight className="hidden h-4 w-4 self-center text-ink-3 md:block" />
                <Node label="Route set" value={t ? (t.allowed_routes.join(" · ") || "none") : (policy?.body.routes ?? []).join(" · ")}
                  tone={t && t.allowed_routes.length === 0 ? "bad" : t ? "good" : undefined}
                  sub={t ? (t.remote_path_removed ? "remote path removed" : t.egress.permitted ? "remote permitted" : "") : ""} />
                <ArrowRight className="hidden h-4 w-4 self-center text-ink-3 md:block" />
                <Node label="Action" value={t?.action ?? "—"} tone={t ? (["deny", "human_review"].includes(t.action) ? "bad" : t.action === "allow" ? "good" : "warn") : undefined}
                  sub={t?.reason_codes?.join(", ")} />
              </div>
            </Card>

            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hair px-6 py-4">
                <Tabs value={tab} onChange={setTab} items={[{ id: "test", label: "Test" }, { id: "edit", label: "Edit & publish" }, { id: "history", label: "Versions" }]} />
                <span className="text-[12px] text-ink-3">Policy engine only · no inference runs here</span>
              </div>
              {tab === "test" && (
                <div className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-2">
                  <div className="space-y-3">
                    <div className="flex flex-wrap gap-1.5">{PRESETS.map((p) => (
                      <button key={p.label} className="rounded-full border border-line bg-white/70 px-3 py-1 text-[12px] text-ink-2 hover:text-ink"
                        onClick={() => { setPrompt(p.text); setDept(p.dept); const pol = pq.data!.policies.find((x) => x.departments.includes(`acme/${p.dept}`)); if (pol) setSel(pol.policy_id); }}>{p.label}</button>))}
                    </div>
                    <select aria-label="Department" className="input !w-auto" value={dept} onChange={(e) => setDept(e.target.value)}>
                      {["it", "hr", "sales", "security"].map((d) => <option key={d} value={d}>acme / {d}</option>)}
                    </select>
                    <textarea aria-label="Test prompt" className="input min-h-[140px]" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
                    <p className="text-[11.5px] text-ink-3">Test values above are synthetic security evaluation data, not real personal data.</p>
                    <button className="btn-primary" onClick={() => test.mutate()} disabled={test.isPending || !prompt.trim()} data-testid="policy-test">
                      {test.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Evaluate policy
                    </button>
                  </div>
                  <div>
                    {test.isError && <StateView kind="error" detail={String(test.error)} />}
                    {!t && !test.isError && <StateView kind="empty" title="No evaluation yet" detail="Results come from the live DLP detector and policy engine." />}
                    {t && (
                      <div className="space-y-4 animate-fadein" data-testid="policy-result">
                        {t.remote_path_removed ? (
                          <div className="rounded-2xl border border-lilac/25 bg-lilac-soft/70 px-4 py-3">
                            <div className="text-[18px] font-semibold text-lilac">0 bytes may leave device</div>
                            <div className="text-[12px] text-ink-2">Enforced: the evaluated decision removes every remote route ({t.egress.reason}).</div>
                          </div>
                        ) : (
                          <div className="rounded-2xl border border-line bg-white/70 px-4 py-3 text-[13px] text-ink-2">
                            Remote egress {t.egress.permitted ? "is permitted" : "is not permitted"} for this input · connector mode <b>{t.remote_connector_mode}</b>.
                          </div>
                        )}
                        <KV cols={1} rows={[
                          ["Policy version", t.policy_version], ["Classification", <DataClassBadge value={t.classification} />],
                          ["Action", t.action], ["Allowed routes", t.allowed_routes.join(", ") || "none"],
                          ["Cache eligible", t.cache_eligible ? "Yes" : "No"], ["Detector time", `${t.dlp.elapsed_ms} ms · ${t.dlp.layers.join(", ")}`],
                        ]} />
                        <div>
                          <div className="eyebrow mb-2">Detected entities</div>
                          <div className="flex flex-wrap gap-1.5">{t.dlp.findings.length ? t.dlp.findings.map((f: any, i: number) =>
                            <Badge key={i} tone={["API_KEY", "PASSWORD", "PRIVATE_KEY", "DB_CREDENTIAL", "CLOUD_CREDENTIAL", "JWT", "AUTH_TOKEN"].includes(f.type) ? "bad" : "lilac"}>{f.type} · {f.layer}</Badge>) : <span className="text-[12.5px] text-ink-3">none</span>}</div>
                        </div>
                        <div>
                          <div className="eyebrow mb-2">Denied routes</div>
                          {Object.entries(t.denied_routes).map(([k, v]) => <div key={k} className="flex justify-between gap-4 text-[12.5px]"><span className="font-medium">{k}</span><span className="text-right text-ink-3">{String(v)}</span></div>)}
                        </div>
                        <div className="flex flex-wrap gap-1.5">{t.reason_codes.map((c: string) => <ReasonBadge key={c} code={c} />)}</div>
                        {t.transformed_preview && <div><div className="eyebrow mb-1">Transformed prompt</div><div className="rounded-xl bg-white/80 p-3 text-[12.5px] mono">{t.transformed_preview}</div></div>}
                      </div>
                    )}
                  </div>
                </div>
              )}
              {tab === "edit" && policy && (
                <div className="space-y-3 p-6">
                  <Explainer>Publishing creates a new immutable version (<b>{policy.policy_id}-v{policy.versions[0].version + 1}</b>). Cache entries created under the previous version are invalidated automatically.</Explainer>
                  <textarea aria-label="Policy JSON" className="input min-h-[360px] font-mono !text-[12.5px]" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
                  {draftErr && <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[12.5px] text-danger">{draftErr}</div>}
                  {publish.data && <div className="rounded-lg bg-mint-soft px-3 py-2 text-[12.5px] text-mint">Published {publish.data.published}</div>}
                  <button className="btn-primary" disabled={publish.isPending} onClick={() => {
                    try { publish.mutate(JSON.parse(draft)); } catch (e) { setDraftErr(`Invalid JSON: ${String(e)}`); }
                  }}><Upload className="h-4 w-4" /> Publish new version</button>
                </div>
              )}
              {tab === "history" && policy && (
                <div className="p-6">
                  <table className="w-full"><thead><tr className="border-b border-hair">{["Version", "Status", "Published", "By", "Body SHA-256"].map((h) => <th key={h} className="table-head">{h}</th>)}</tr></thead>
                    <tbody>{policy.versions.map((v) => (
                      <tr key={v.version} className="border-b border-hair last:border-0">
                        <td className="table-cell font-medium">{v.version_tag}</td>
                        <td className="table-cell"><Badge tone={v.status === "published" ? "good" : "neutral"}>{v.status}</Badge></td>
                        <td className="table-cell text-ink-2">{fmtDateTime(v.published_at)}</td>
                        <td className="table-cell text-ink-2">{v.published_by}</td>
                        <td className="table-cell"><span className="mono">{v.body_sha256.slice(0, 16)}…</span></td>
                      </tr>))}</tbody></table>
                </div>
              )}
            </Card>

            {policy && (
              <Card>
                <CardHeader icon={<FileCog className="h-4 w-4" />} title="Current configuration" subtitle={policy.current_version}
                  right={<Badge tone="neutral"><History className="h-3 w-3" />{fmtInt(policy.versions.length)} versions</Badge>} />
                <div className="p-6">
                  <KV cols={3} rows={[
                    ["Routes", policy.body.routes.join(", ")], ["Remote", policy.body.remote.enabled ? `≤ ${policy.body.remote.max_data_class}` : "disabled"],
                    ["PII action", policy.body.dlp.pii_action], ["Secret action", policy.body.dlp.secret_action],
                    ["DLP failure", policy.body.dlp.fail_mode === "closed" ? "fail closed" : "local only"], ["Output PII", policy.body.dlp.output_pii_action],
                    ["Cache", policy.body.cache.enabled ? `≤ ${policy.body.cache.max_data_class} · TTL ${policy.body.cache.ttl_s}s` : "disabled"],
                    ["Retention", `raw prompts ${policy.body.retention.store_raw_prompts ? "stored" : "not stored"} · receipts ${policy.body.retention.receipt_days}d`],
                    ["Budget", `${fmtUsd(policy.body.budget.monthly_usd, 2)} · ${fmtInt(policy.body.budget.monthly_tokens)} tok / month`],
                    ["Per-request cap", `${fmtInt(policy.body.budget.max_tokens_per_request)} tokens`], ["Rate limit", `${policy.body.rate_limit.rpm} rpm`],
                    ["Router abstain", policy.body.router.abstain_action],
                  ]} />
                </div>
              </Card>
            )}
          </div>
        </div>
      )}
    </>
  );
}
