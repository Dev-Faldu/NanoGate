import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileText, Loader2, Plus, RotateCcw, ShieldOff, Trash2, Upload } from "lucide-react";
import { del, get, post } from "../api/client";
import { Badge, Card, CardHeader, DataClassBadge, Explainer, PageHeader, StateView } from "../components/ui";
import { Field, Modal, ReadOnlyNote, errText, useMe } from "../components/kit";
import { fmtBytes, fmtDateTime, fmtInt } from "../lib/format";

interface Doc { doc_id: string; filename: string; bytes: number; chunks: number; created_at: number }
interface Source { source_id: string; tenant_id: string; name: string; description: string; departments: string[]; data_class: string;
  version: string; created_at: number; revoked: boolean; documents: Doc[]; chunks: number }

async function toB64(f: File): Promise<string> {
  const buf = new Uint8Array(await f.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

export default function Knowledge() {
  const qc = useQueryClient();
  const { canWrite } = useMe();
  const q = useQuery({ queryKey: ["knowledge"], queryFn: () => get<{ sources: Source[]; available: boolean; reason: string | null }>("/api/knowledge") });
  const [create, setCreate] = useState(false);
  const inv = () => qc.invalidateQueries({ queryKey: ["knowledge"] });
  return (
    <>
      <PageHeader eyebrow="Knowledge" title="Answer from your own documents"
        subtitle="Upload policies, handbooks and runbooks. NanoGate finds the relevant passages on this device and the model answers from them, naming the document."
        right={canWrite && q.data?.available && <button className="btn-primary" onClick={() => setCreate(true)}><Plus className="h-4 w-4" />New source</button>} />
      {!canWrite && <div className="mb-6"><ReadOnlyNote /></div>}
      <div className="mb-6"><Explainer>
        Documents never leave the device: text is split into passages and indexed with the local embedding model. Only the departments
        you choose can use a source, and its data class is applied to every answer built from it, so a Confidential handbook keeps
        those answers on-device. Revoking a source stops its use at once and invalidates cached answers that relied on it.
      </Explainer></div>
      {q.isLoading ? <Card><StateView kind="loading" /></Card> : q.isError ? <Card><StateView kind="error" detail={errText(q.error)} /></Card> :
        !q.data!.available ? <Card><StateView kind="unavailable" title="Knowledge unavailable" detail={q.data!.reason} /></Card> :
          q.data!.sources.length === 0 ? <Card><StateView kind="empty" title="No company documents yet"
            detail="Create a source (for example “HR handbook”), choose who may use it, then upload files." /></Card> : (
            <div className="space-y-6">
              {q.data!.sources.map((s) => <SourceCard key={s.source_id} s={s} canWrite={canWrite} onChange={inv} />)}
            </div>
          )}
      <NewSource open={create} onClose={() => setCreate(false)} />
    </>
  );
}

function SourceCard({ s, canWrite, onChange }: { s: Source; canWrite: boolean; onChange: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const out = [];
      for (const [i, f] of files.entries()) {
        setProgress(`Indexing ${f.name} (${i + 1}/${files.length})…`);
        out.push(await post<any>(`/api/knowledge/${s.source_id}/documents`, { filename: f.name, content_b64: await toB64(f) }));
      }
      return out;
    },
    onSettled: () => { setProgress(null); onChange(); },
  });
  const rm = useMutation({ mutationFn: (d: string) => del(`/api/knowledge/${s.source_id}/documents/${d}`), onSuccess: onChange });
  const toggle = useMutation({ mutationFn: () => post(`/api/knowledge/${s.source_id}/${s.revoked ? "restore" : "revoke"}`), onSuccess: onChange });
  const drop = useMutation({ mutationFn: () => del(`/api/knowledge/${s.source_id}`), onSuccess: onChange });
  return (
    <Card>
      <CardHeader icon={<BookOpen className="h-4 w-4" />} eyebrow={`${s.tenant_id} · ${s.source_id}`} title={s.name}
        subtitle={s.description || `${s.documents.length} documents · ${fmtInt(s.chunks)} passages · version ${s.version}`}
        right={<>
          <DataClassBadge value={s.data_class} />
          {s.revoked ? <Badge tone="warn">Revoked</Badge> : <Badge tone="good">In use</Badge>}
        </>} />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-2">
          <span>Used by</span>{s.departments.map((d) => <Badge key={d} tone="info">{d}</Badge>)}
        </div>
        {s.documents.length > 0 && (
          <ul className="divide-y divide-hair rounded-2xl border border-white/80 bg-white/40">
            {s.documents.map((d) => (
              <li key={d.doc_id} className="flex items-center gap-3 px-4 py-2.5">
                <FileText className="h-4 w-4 shrink-0 text-ink-3" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{d.filename}</span>
                <span className="text-[12px] text-ink-3">{fmtBytes(d.bytes)} · {d.chunks} passages · {fmtDateTime(d.created_at)}</span>
                {canWrite && <button className="rounded-lg p-1.5 text-ink-3 hover:bg-ink/5 hover:text-danger" aria-label={`Remove ${d.filename}`}
                  onClick={() => confirm(`Remove ${d.filename}? Cached answers built from this source are invalidated.`) && rm.mutate(d.doc_id)}>
                  <Trash2 className="h-4 w-4" /></button>}
              </li>
            ))}
          </ul>
        )}
        {canWrite && (
          <div className="flex flex-wrap items-center gap-2">
            <input ref={file} type="file" multiple hidden accept=".pdf,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.html,.htm"
              onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; f.length && upload.mutate(f); }} />
            <button className="btn-primary" disabled={upload.isPending} onClick={() => file.current?.click()}>
              {upload.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}Upload files
            </button>
            <button className="btn-ghost" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
              {s.revoked ? <><RotateCcw className="h-4 w-4" />Restore</> : <><ShieldOff className="h-4 w-4" />Revoke</>}
            </button>
            <button className="btn-ghost" onClick={() => confirm(`Delete "${s.name}" and all its documents?`) && drop.mutate()}>
              <Trash2 className="h-4 w-4" />Delete</button>
            <span className="text-[12px] text-ink-3">PDF (with a text layer), TXT, Markdown, CSV, JSON, YAML, HTML · up to 15 MB each</span>
          </div>
        )}
        {progress && <p className="text-[12.5px] text-ink-2">{progress}</p>}
        {upload.data && <p className="text-[12.5px] text-mint">Indexed {upload.data.map((r: any) => `${r.filename} (${r.chunks} passages)`).join(", ")}.</p>}
        {[upload, rm, toggle, drop].map((m, i) => m.isError && <p key={i} role="alert" className="text-[13px] text-danger">{errText(m.error)}</p>)}
      </div>
    </Card>
  );
}

function NewSource({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const org = useQuery({ queryKey: ["org"], queryFn: () => get<any>("/api/org"), enabled: open });
  const [tenant, setTenant] = useState("acme");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [cls, setCls] = useState("Internal");
  const [depts, setDepts] = useState<string[]>([]);
  const m = useMutation({
    mutationFn: () => post("/api/knowledge", { tenant_id: tenant, name, description: desc, data_class: cls, departments: depts }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); setName(""); setDesc(""); setDepts([]); onClose(); },
  });
  const all = org.data?.tenants.find((t: any) => t.tenant_id === tenant)?.departments ?? [];
  return (
    <Modal open={open} onClose={onClose} title="New knowledge source" subtitle="A named collection of documents, for chosen departments of one tenant.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="IT runbooks" required /></Field>
        <Field label="Description (optional)"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Tenant"><select className="input" value={tenant} onChange={(e) => { setTenant(e.target.value); setDepts([]); }}>
            {org.data?.tenants.map((t: any) => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name}</option>)}</select></Field>
          <Field label="Data class" hint="Answers using it are treated at least this sensitive.">
            <select className="input" value={cls} onChange={(e) => setCls(e.target.value)}>
              {["Public", "Internal", "Confidential", "Restricted"].map((c) => <option key={c}>{c}</option>)}</select></Field>
        </div>
        <Field label="Departments that may use it">
          <div className="flex flex-wrap gap-2">
            {all.map((d: any) => (
              <button type="button" key={d.department_id} className="glass-chip" aria-pressed={depts.includes(d.department_id)}
                onClick={() => setDepts((x) => x.includes(d.department_id) ? x.filter((y) => y !== d.department_id) : [...x, d.department_id])}>
                {d.display_name}</button>
            ))}
          </div>
        </Field>
        {m.isError && <div role="alert" className="text-[13px] text-danger">{errText(m.error)}</div>}
        <button className="btn-primary w-full justify-center" disabled={m.isPending || !name.trim() || !depts.length}>Create source</button>
      </form>
    </Modal>
  );
}
