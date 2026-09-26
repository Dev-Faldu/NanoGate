/** Sends REAL requests through the gateway pipeline as a department principal. */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { post } from "../api/client";
import { fmtMs } from "../lib/format";
import { ReasonBadge, RouteBadge } from "./ui";

export const DEMO_PROMPTS = [
  { label: "Local intelligence", dept: "it", text: "How do I reset the VPN client?" },
  { label: "Verified reuse", dept: "it", text: "What steps restore my VPN connection?" },
  { label: "Cache hard negative", dept: "it", text: "How do I reset another employee's VPN password?" },
  {
    label: "Privacy (synthetic PII)", dept: "hr",
    text: "Draft a short note confirming the leave balance for employee Maria Lopez (EMP-204981, maria.lopez@acme-corp.example, SSN 219-09-9999).",
  },
  { label: "Hard public question", dept: "it", text: "A train leaves at 3:40 pm and travels 212 km at an average of 78 km/h, then waits 17 minutes and travels another 96 km at 64 km/h. At what time does it arrive? Show your reasoning and end with 'Answer: <time>'." },
  { label: "Security (CISA KEV)", dept: "security", text: "Which vendor's product is affected by CVE-2021-44228 according to CISA KEV, and what is the required action?" },
  // Synthetic credential, assembled at runtime so the source never contains a literal secret pattern.
  { label: "Secret leak attempt", dept: "it", text: "Store this for me: aws_secret_access_key = " + "SYNTHETIC0".repeat(4) },
];

export interface PlaygroundResult {
  ok: boolean;
  response?: any;
  error?: { message: string; code: string };
  headers: Record<string, string>;
}

export default function Composer({ onResult }: { onResult?: (r: PlaygroundResult) => void }) {
  const qc = useQueryClient();
  const [dept, setDept] = useState("it");
  const [tenant, setTenant] = useState("acme");
  const [text, setText] = useState(DEMO_PROMPTS[0].text);
  const loaded = DEMO_PROMPTS.find((p) => p.text === text && p.dept === dept)?.label;
  const [last, setLast] = useState<PlaygroundResult | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const m = useMutation({
    mutationFn: async () => {
      const t0 = performance.now();
      const r = await post<PlaygroundResult>("/api/playground/chat", { tenant, department: dept, content: text, max_tokens: 384, temperature: 0.2 });
      setElapsed(performance.now() - t0);
      return r;
    },
    onSuccess: (r) => {
      setLast(r);
      onResult?.(r);
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const tenants: Record<string, string[]> = { acme: ["it", "hr", "sales", "security"], globex: ["it"] };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Demo prompts">
        {DEMO_PROMPTS.map((p) => (
          <button key={p.label} type="button" className="glass-chip" aria-pressed={loaded === p.label}
            onClick={() => { setText(p.text); setDept(p.dept); setTenant("acme"); }}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="tenant">Tenant</label>
        <select id="tenant" className="input !w-auto" value={tenant} onChange={(e) => { setTenant(e.target.value); setDept(tenants[e.target.value][0]); }}>
          {Object.keys(tenants).map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <label className="sr-only" htmlFor="dept">Department</label>
        <select id="dept" className="input !w-auto" value={dept} onChange={(e) => setDept(e.target.value)}>
          {tenants[tenant].map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <span className="self-center text-[12px] text-ink-3">Identity is resolved server-side from this department's API key.</span>
      </div>
      <label className="sr-only" htmlFor="prompt">Prompt</label>
      <textarea id="prompt" className="input min-h-[92px] resize-y leading-relaxed" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="flex items-center gap-3">
        <button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending || !text.trim()} data-testid="send-request">
          {m.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {m.isPending ? "Running through NanoGate…" : "Send real request"}
        </button>
        {m.isError && <span role="alert" className="text-[13px] text-danger">{String(m.error)}</span>}
      </div>
      {last && (
        <div className="glass-result animate-fadein" data-testid="composer-result">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <ReasonBadge code={last.headers["x-nanogate-reason"] || last.error?.code} />
            <RouteBadge route={last.headers["x-nanogate-route"]} />
            <span className="text-[12px] text-ink-3">round trip {fmtMs(elapsed)}</span>
          </div>
          <div className="max-h-56 overflow-y-auto whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink">
            {last.ok ? last.response.choices[0].message.content : <span className="text-danger">{last.error?.message}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
