/** NanoGate Chat: the employee-facing private assistant. Every message goes through the full gateway pipeline
 *  (identity, DLP, policy, budget, cache, local models, router) with the signed-in person's own key. */
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { ArrowUp, KeyRound, Loader2, LogOut, Lock, MessageSquarePlus, ShieldCheck, Sparkles } from "lucide-react";
import { Logo } from "../components/Shell";
import { Markdown } from "../components/kit";

const TOKEN = "nanogate.chat.session";
const WHO = "nanogate.chat.identity";
const HISTORY = "nanogate.chat.history";

interface Identity { name: string; tenant: string; department: string; department_id: string }
interface Msg { role: "user" | "assistant"; content: string; route?: string; reason?: string; receipt?: string; dataClass?: string;
  error?: boolean }

const ss = {
  get: (k: string) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string | null) => { try { v === null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* ignore */ } },
};

async function call<T>(path: string, body: unknown, token?: string | null): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body) });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (!r.ok) throw Object.assign(new Error(data?.detail?.message ?? `HTTP ${r.status}`), { status: r.status });
  return data as T;
}

const ROUTE_TEXT: Record<string, string> = {
  cache: "Answered from a verified earlier answer", local: "Answered on this device", local_large: "Answered on this device (larger model)",
  remote: "Answered by an approved external model", denied: "Blocked by policy",
};

export default function ChatApp() {
  const [token, setTok] = useState(() => ss.get(TOKEN));
  const [who, setWho] = useState<Identity | null>(() => { try { return JSON.parse(ss.get(WHO) ?? "null"); } catch { return null; } });
  if (!token || !who) return <ChatLogin onIn={(t, w) => { ss.set(TOKEN, t); ss.set(WHO, JSON.stringify(w)); setTok(t); setWho(w); }} />;
  return <Conversation token={token} who={who} onOut={() => { ss.set(TOKEN, null); ss.set(WHO, null); ss.set(HISTORY, null); setTok(null); setWho(null); }} />;
}

function ChatLogin({ onIn }: { onIn: (t: string, w: Identity) => void }) {
  const [key, setKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-[440px] animate-fadein">
        <Logo className="mb-10 justify-center" />
        <div className="panel p-8">
          <h1 className="text-[26px] font-semibold tracking-[-0.02em] text-ink">Your private AI assistant</h1>
          <p className="mt-2 text-[14px] text-ink-2">Runs on your company's own AI hardware. Sign in with the access key your IT team gave you.</p>
          <form className="mt-6 space-y-3" onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setErr(null);
            try {
              const r = await call<{ token: string; identity: Identity }>("/api/chat/session", { api_key: key.trim() });
              onIn(r.token, r.identity);
            } catch (ex: any) {
              setErr(ex.status === 401 ? "That key is not valid (or was revoked)." : ex.message);
            } finally { setBusy(false); }
          }}>
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
              <input type="password" autoComplete="off" className="input !pl-9 font-mono" placeholder="ng_live_…" aria-label="Access key"
                value={key} onChange={(e) => setKey(e.target.value)} required />
            </div>
            {err && <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{err}</div>}
            <button className="btn-primary w-full justify-center" disabled={busy || !key}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}Start chatting</button>
          </form>
          <ul className="mt-6 space-y-2 text-[12.5px] text-ink-2">
            <li className="flex gap-2"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mint" />Personal and confidential data stays on the device.</li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mint" />Passwords and keys you paste are blocked, not stored.</li>
          </ul>
        </div>
        <p className="mt-6 text-center text-[12px] text-ink-3"><a className="underline decoration-ink/20 underline-offset-2 hover:text-ink" href="/overview">Administrator console →</a></p>
      </div>
    </div>
  );
}

function Conversation({ token, who, onOut }: { token: string; who: Identity; onOut: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>(() => { try { return JSON.parse(ss.get(HISTORY) ?? "[]"); } catch { return []; } });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { ss.set(HISTORY, JSON.stringify(msgs.slice(-60))); end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);

  const send = async (m: string) => {
    const content = m.trim();
    if (!content || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content }];
    setMsgs(next);
    setText("");
    setBusy(true);
    try {
      const r = await call<any>("/api/chat/send", { messages: next.filter((x) => !x.error).slice(-16).map((x) => ({ role: x.role, content: x.content })) }, token);
      const h = r.headers ?? {};
      setMsgs((x) => [...x, r.ok
        ? { role: "assistant", content: r.message, route: h["x-nanogate-route"], reason: h["x-nanogate-reason"], receipt: h["x-nanogate-receipt-id"], dataClass: h["x-nanogate-data-class"] }
        : { role: "assistant", content: friendly(r.error?.code, r.error?.message), route: "denied", reason: r.error?.code, receipt: h["x-nanogate-receipt-id"], error: true }]);
    } catch (e: any) {
      if (e.status === 401) { onOut(); return; }
      setMsgs((x) => [...x, { role: "assistant", content: e.message, error: true }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="glass-chrome sticky top-0 z-20 border-b border-white/70">
        <div className="mx-auto flex max-w-[860px] items-center gap-3 px-4 py-3">
          <Logo />
          <div className="ml-auto hidden text-right text-[12px] leading-tight text-ink-2 sm:block">
            <div className="font-medium text-ink">{who.name || "Signed in"}</div><div>{who.department} · {who.tenant}</div>
          </div>
          <button className="btn-ghost !px-3" onClick={() => setMsgs([])} aria-label="New chat"><MessageSquarePlus className="h-4 w-4" /><span className="hidden sm:inline">New chat</span></button>
          <button className="btn-ghost !p-2" onClick={onOut} aria-label="Sign out"><LogOut className="h-4 w-4" /></button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[860px] flex-1 space-y-5 px-4 pb-40 pt-8">
        {msgs.length === 0 && (
          <div className="pt-10 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-b from-[#22304f] to-[#0f1b33] text-white shadow-lift"><Sparkles className="h-5 w-5" /></div>
            <h1 className="text-[26px] font-semibold tracking-[-0.02em] text-ink">How can I help{who.name ? `, ${who.name.split(" ")[0]}` : ""}?</h1>
            <p className="mt-2 text-[14px] text-ink-2">Ask anything. Your {who.department} team's rules decide where each answer is computed.</p>
            <div className="mx-auto mt-6 flex max-w-[620px] flex-wrap justify-center gap-2">
              {["Summarise the main points of our leave policy", "Write a polite follow-up email to a customer", "Explain what a known exploited vulnerability is", "Help me draft a project status update"].map((s) =>
                <button key={s} className="glass-chip !py-1.5" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => m.role === "user" ? (
          <div key={i} className="flex justify-end"><div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[14px] leading-relaxed text-white shadow-soft">{m.content}</div></div>
        ) : (
          <div key={i} className="space-y-1.5">
            <div className={clsx("panel px-5 py-4", m.error && "!bg-danger-soft/60")}>
              {m.error ? <p className="text-[14px] text-danger">{m.content}</p> : <Markdown text={m.content} className="!text-[14px]" />}
            </div>
            <div className="flex flex-wrap items-center gap-2 pl-1 text-[11.5px] text-ink-3">
              {m.route && <span className="inline-flex items-center gap-1"><span className={clsx("h-1.5 w-1.5 rounded-full",
                m.route === "remote" ? "bg-peri" : m.route === "denied" ? "bg-danger" : "bg-mint")} />{ROUTE_TEXT[m.route] ?? m.route}</span>}
              {m.dataClass && m.dataClass !== "Public" && <span>· {m.dataClass} data</span>}
              {m.receipt && <span className="mono" title="Every answer has a tamper-evident receipt your admins can verify">· {m.receipt}</span>}
            </div>
          </div>
        ))}
        {busy && <div className="panel inline-flex items-center gap-2 px-4 py-3 text-[13px] text-ink-2"><Loader2 className="h-4 w-4 animate-spin" />Thinking on the device…</div>}
        <div ref={end} />
      </main>
      <form className="fixed inset-x-0 bottom-0 z-20 px-4 pb-4" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <div className="mx-auto max-w-[860px]">
          <div className="glass-menu flex items-end gap-2 !rounded-[24px] !p-2">
            <textarea rows={1} value={text} aria-label="Message" placeholder="Message your assistant…"
              className="max-h-48 min-h-[46px] flex-1 resize-none bg-transparent px-3 py-3 text-[14.5px] text-ink outline-none placeholder:text-ink-3"
              onChange={(e) => { setText(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
            <button className="btn-primary !h-11 !w-11 shrink-0 justify-center !rounded-full !p-0" disabled={!text.trim() || busy} aria-label="Send"><ArrowUp className="h-4 w-4" /></button>
          </div>
          <p className="mt-2 text-center text-[11px] text-ink-3">AI can make mistakes: check important facts. Conversations are not stored by NanoGate; only the decision metadata is.</p>
        </div>
      </form>
    </div>
  );
}

function friendly(code?: string, msg?: string): string {
  switch (code) {
    case "SECRET_BLOCKED": return "I can't process that: it looks like it contains a password, key or other secret. Remove it and ask again.";
    case "BUDGET_DENY": return "Your team has used its AI budget for this month. Please contact your IT team.";
    case "RATE_LIMITED": return "You're sending messages a little fast. Wait a moment and try again.";
    case "MODEL_UNAVAILABLE": return "The assistant is temporarily unavailable. Please try again in a minute.";
    case "POLICY_BLOCK": case "ROUTE_ESCALATION_DENIED": return "Your team's policy doesn't allow this request.";
    default: return msg ?? "Something went wrong.";
  }
}
