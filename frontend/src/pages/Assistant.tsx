import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, Check, ChevronDown, Cpu, Loader2, RotateCcw, Sparkles, Wrench, X } from "lucide-react";
import { post } from "../api/client";
import { Badge } from "../components/ui";
import { Markdown, errText, useMe } from "../components/kit";

interface Step { tool: string; ok: boolean; summary: string }
interface Proposal { proposal_id: string; tool: string; summary: string; args: Record<string, unknown>;
  state?: "pending" | "done" | "dismissed" | "failed"; result?: any; error?: string }
interface Turn { role: "user" | "assistant"; content: string; steps?: Step[]; proposals?: Proposal[];
  meta?: { models: { model: string; ms: number }[]; elapsed_ms: number }; error?: boolean }

const STORE = "nanogate.assistant";
const SUGGESTIONS = [
  "What happened in the last 24 hours?",
  "Why were requests denied today?",
  "Which department spent the most this month?",
  "Is everything healthy right now?",
  "How do I connect my app to NanoGate?",
  "Create an app key for the sales team called crm-bot",
  "Does this prompt stay on the device for HR: “Update Maria's salary to 82k”?",
  "Show me the latest benchmark results",
];
const TOOL_LABEL: Record<string, string> = {
  overview: "Traffic overview", search_requests: "Searched requests", explain_receipt: "Read a receipt", verify_receipt: "Verified a receipt",
  spend: "Spend report", organisation: "Departments", policy: "Policy", test_prompt: "Tested a prompt", keys: "API keys",
  alerts: "Alerts", system_status: "System health", benchmarks: "Benchmarks", knowledge_sources: "Knowledge sources",
  audit_log: "Activity log", backups: "Backups", product_help: "Product guide",
};

function load(): Turn[] {
  try { return JSON.parse(sessionStorage.getItem(STORE) ?? "[]"); } catch { return []; }
}

export default function Assistant() {
  const qc = useQueryClient();
  const { canWrite } = useMe();
  const [turns, setTurns] = useState<Turn[]>(load);
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    try { sessionStorage.setItem(STORE, JSON.stringify(turns.slice(-40))); } catch { /* storage unavailable */ }
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = useMutation({
    mutationFn: (message: string) => post<any>("/api/assistant/chat", {
      message, history: turns.filter((t) => !t.error).map((t) => ({ role: t.role, content: t.content })),
    }),
    onSuccess: (r) => setTurns((t) => [...t, { role: "assistant", content: r.answer, steps: r.steps,
      proposals: r.proposals.map((p: Proposal) => ({ ...p, state: "pending" })), meta: { models: r.models, elapsed_ms: r.elapsed_ms } }]),
    onError: (e) => setTurns((t) => [...t, { role: "assistant", content: errText(e), error: true }]),
  });
  const send = (m: string) => {
    const msg = m.trim();
    if (!msg || ask.isPending) return;
    setTurns((t) => [...t, { role: "user", content: msg }]);
    setText("");
    ask.mutate(msg);
  };
  const setProposal = (pid: string, patch: Partial<Proposal>) => setTurns((ts) => ts.map((t) => t.proposals
    ? { ...t, proposals: t.proposals.map((p) => p.proposal_id === pid ? { ...p, ...patch } : p) } : t));
  const decide = async (p: Proposal, ok: boolean) => {
    try {
      if (!ok) { await post(`/api/assistant/proposals/${p.proposal_id}/dismiss`); setProposal(p.proposal_id, { state: "dismissed" }); return; }
      const r = await post<any>(`/api/assistant/proposals/${p.proposal_id}/confirm`);
      setProposal(p.proposal_id, { state: "done", result: r.result });
      qc.invalidateQueries();
    } catch (e) {
      setProposal(p.proposal_id, { state: "failed", error: errText(e) });
    }
  };

  return (
    <div className="mx-auto flex min-h-[calc(100vh-11rem)] max-w-[880px] flex-col">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <div className="eyebrow mb-2">Assistant</div>
          <h1 className="text-[34px] font-semibold leading-tight tracking-[-0.02em] text-ink">Ask NanoGate</h1>
          <p className="mt-2 max-w-2xl text-[15px] text-ink-2">
            Questions about traffic, costs, policies and health are answered from live data. It can also prepare tasks, like creating a
            key or changing a budget, for you to confirm. It runs on this device's own models.
          </p>
        </div>
        {turns.length > 0 && <button className="btn-ghost shrink-0" onClick={() => { setTurns([]); ask.reset(); }}><RotateCcw className="h-4 w-4" />New chat</button>}
      </div>

      <div className="flex-1 space-y-5">
        {turns.length === 0 && (
          <div className="panel p-6">
            <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-ink-2"><Sparkles className="h-4 w-4 text-peri" />Try asking</div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => <button key={s} className="glass-chip !py-1.5 text-left" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {turns.map((t, i) => t.role === "user" ? (
          <div key={i} className="flex justify-end">
            <div className="max-w-[78%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[13.5px] leading-relaxed text-white shadow-soft">{t.content}</div>
          </div>
        ) : (
          <div key={i} className="flex gap-3">
            <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-[#22304f] to-[#0f1b33] text-white shadow-soft">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1 space-y-2">
              <div className={clsx("panel px-4 py-3", t.error && "!border-danger/30")}>
                {t.error ? <p className="text-[13.5px] text-danger">{t.content}</p> : <Markdown text={t.content} />}
              </div>
              {t.proposals?.map((p) => <ProposalCard key={p.proposal_id} p={p} canWrite={canWrite} onDecide={decide} />)}
              {!!t.steps?.length && <Steps steps={t.steps} meta={t.meta} />}
            </div>
          </div>
        ))}
        {ask.isPending && (
          <div className="flex gap-3">
            <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink/80 text-white"><Loader2 className="h-4 w-4 animate-spin" /></div>
            <div className="panel px-4 py-3 text-[13px] text-ink-2">Looking at live data on the device…</div>
          </div>
        )}
        <div ref={end} />
      </div>

      <form className="sticky bottom-4 mt-6" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <div className="glass-menu flex items-end gap-2 !rounded-[22px] !p-2">
          <textarea ref={box} rows={1} value={text} placeholder="Ask about traffic, spend, policies, health, or ask it to do something…"
            aria-label="Message the assistant" className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-3 py-2.5 text-[14px] text-ink outline-none placeholder:text-ink-3"
            onChange={(e) => { setText(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
          <button className="btn-primary !h-11 !w-11 shrink-0 justify-center !rounded-full !p-0" disabled={!text.trim() || ask.isPending} aria-label="Send">
            <ArrowUp className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-2 text-center text-[11.5px] text-ink-3">Answers come from NanoGate's own records; it cannot see the text of anyone's prompts. Changes always need your confirmation.</p>
      </form>
    </div>
  );
}

function ProposalCard({ p, canWrite, onDecide }: { p: Proposal; canWrite: boolean; onDecide: (p: Proposal, ok: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  const key = p.result?.api_key as string | undefined;
  return (
    <div className="glass-result border-peri/30 !bg-peri-soft/40">
      <div className="flex items-start gap-3">
        <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-peri" />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-semibold uppercase tracking-[0.08em] text-peri">Proposed action</div>
          <div className="mt-0.5 text-[13.5px] font-medium text-ink">{p.summary}</div>
          {p.state === "done" && <div className="mt-2 space-y-2 text-[12.5px] text-mint">
            <div className="flex items-center gap-1"><Check className="h-3.5 w-3.5" />Done, recorded in the activity log.</div>
            {key && <div className="rounded-lg bg-white/80 p-2 font-mono text-[12px] text-ink break-all">{key}
              <div className="mt-1 font-sans text-[11.5px] text-ink-3">Copy this key now: it will not be shown again.</div></div>}
          </div>}
          {p.state === "dismissed" && <div className="mt-2 text-[12.5px] text-ink-3">Dismissed, nothing changed.</div>}
          {p.state === "failed" && <div className="mt-2 text-[12.5px] text-danger">{p.error}</div>}
        </div>
      </div>
      {p.state === "pending" && (canWrite ? (
        <div className="mt-3 flex gap-2 pl-7">
          <button className="btn-primary !py-1.5" disabled={busy} onClick={async () => { setBusy(true); await onDecide(p, true); setBusy(false); }}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Confirm</button>
          <button className="btn-ghost !py-1.5" disabled={busy} onClick={() => onDecide(p, false)}><X className="h-4 w-4" />Dismiss</button>
        </div>
      ) : <div className="mt-2 pl-7 text-[12px] text-ink-3">Only an admin can confirm this.</div>)}
    </div>
  );
}

function Steps({ steps, meta }: { steps: Step[]; meta?: Turn["meta"] }) {
  const [open, setOpen] = useState(false);
  const reads = steps.filter((s) => !s.summary.startsWith("proposed"));
  return (
    <div className="text-[12px] text-ink-3">
      <button className="inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 hover:bg-white/60 hover:text-ink-2" onClick={() => setOpen(!open)}>
        <ChevronDown className={clsx("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        {reads.length ? `Checked ${reads.map((s) => TOOL_LABEL[s.tool] ?? s.tool).filter((v, i, a) => a.indexOf(v) === i).join(", ")}` : "How this was answered"}
        {meta && <span>· {(meta.elapsed_ms / 1000).toFixed(1)} s</span>}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 pl-6">
          {steps.map((s, i) => <div key={i} className="flex items-center gap-2">
            <Badge tone={s.ok ? "good" : "warn"}>{TOOL_LABEL[s.tool] ?? s.tool}</Badge><span className="truncate">{s.summary}</span></div>)}
          {meta && <div className="flex items-center gap-1.5 pt-1"><Cpu className="h-3.5 w-3.5" />
            {Array.from(new Set(meta.models.map((m) => m.model))).join(", ")} on this device · {meta.models.length} model calls</div>}
        </div>
      )}
    </div>
  );
}
