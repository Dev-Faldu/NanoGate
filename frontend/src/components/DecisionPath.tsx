import clsx from "clsx";
import {
  BadgeCheck, Cpu, DatabaseZap, Fingerprint, Gauge, Inbox, Route as RouteIcon, ScanSearch, ShieldCheck,
} from "lucide-react";
import type { TimelineEntry } from "../api/types";
import { fmtMs } from "../lib/format";
import { Hint } from "./ui";

const STAGES = [
  { key: "request", label: "Request", icon: Inbox, match: [] as string[],
    help: "The request arrives from an app using the standard OpenAI API." },
  { key: "identity", label: "Identity", icon: Fingerprint, match: ["identity", "rate_limit"],
    help: "Works out who is asking from the API key (company and department), and applies their rate limit." },
  { key: "dlp", label: "DLP", icon: ScanSearch, match: ["dlp", "transform"],
    help: "Scans the prompt for secrets and personal data, and labels how sensitive it is (Public to Secret)." },
  { key: "policy", label: "Policy", icon: ShieldCheck, match: ["policy", "budget"],
    help: "Checks the department's rules and budget: which models are allowed for data this sensitive." },
  { key: "cache", label: "Cache", icon: DatabaseZap, match: ["cache"],
    help: "Looks for an earlier answer to the same question. It is reused only if a checker confirms the two questions mean the same thing." },
  { key: "model", label: "Model", icon: Cpu, match: ["inference", "retrieval"],
    help: "The local model on the ZGX Nano writes the answer, using trusted reference data where relevant." },
  { key: "router", label: "Router", icon: Gauge, match: ["router"],
    help: "Estimates the chance the local answer is wrong. Above the threshold, a larger model may take over, if policy allows." },
  { key: "decision", label: "Decision", icon: RouteIcon, match: ["route", "egress", "output_scan", "settle"],
    help: "Picks the final route, scans the answer for leaks, and records the cost." },
  { key: "receipt", label: "Receipt", icon: BadgeCheck, match: ["receipt"],
    help: "Seals everything that happened into a tamper-evident receipt that can be verified later." },
];

const GOOD = new Set(["ok", "hit", "completed", "selected", "sealed", "scored", "stored", "reserved", "done", "applied", "permitted", "started"]);
const BAD = new Set(["denied", "failed", "blocked", "rejected"]);

function tone(entries: TimelineEntry[]): "good" | "bad" | "warn" | "idle" {
  if (!entries.length) return "idle";
  if (entries.some((e) => BAD.has(e.status))) return "bad";
  const last = entries[entries.length - 1];
  if (GOOD.has(last.status)) return "good";
  return "warn";
}

function summary(key: string, entries: TimelineEntry[]): string {
  const last = entries[entries.length - 1];
  if (!last) return "not reached";
  const d = last.detail as Record<string, any>;
  switch (key) {
    case "identity": return entries.find((e) => e.stage === "identity")?.detail?.department
      ? `${(entries[0].detail as any).tenant}/${(entries[0].detail as any).department}` : String(d.reason ?? last.status);
    case "dlp": return String((entries.find((e) => e.stage === "dlp")?.detail as any)?.data_class ?? last.status);
    case "policy": return String((entries.find((e) => e.stage === "policy")?.detail as any)?.action ?? last.status);
    case "cache": return String(d.decision ?? d.reason ?? last.status).replace("CACHE_", "");
    case "model": {
      const inf = [...entries].reverse().find((e) => e.stage === "inference" && e.status === "completed");
      return inf ? `${(inf.detail as any).completion_tokens ?? "?"} tok · ${fmtMs((inf.detail as any).total_ms)}` : last.status;
    }
    case "router": return d.p_error != null ? `p(err) ${Number(d.p_error).toFixed(3)}` : String(d.reason ? "unavailable" : last.status);
    case "decision": {
      const r = [...entries].reverse().find((e) => e.stage === "route");
      return r ? String((r.detail as any).route ?? r.status) : last.status;
    }
    case "receipt": return last.status;
    default: return last.status;
  }
}

export default function DecisionPath({ timeline, compact }: { timeline: TimelineEntry[] | null; compact?: boolean }) {
  return (
    <ol className={clsx("grid gap-2", compact ? "grid-cols-3" : "grid-cols-3 md:grid-cols-5 xl:grid-cols-9")} aria-label="Decision path">
      {STAGES.map((s, i) => {
        const entries = s.key === "request" ? (timeline?.length ? [{ stage: "request", status: "ok", t_ms: 0, detail: {} }] : [])
          : (timeline ?? []).filter((e) => s.match.includes(e.stage));
        const t = tone(entries as TimelineEntry[]);
        const Icon = s.icon;
        const at = entries.length ? (entries[entries.length - 1] as TimelineEntry).t_ms : null;
        const said = s.key === "request" ? (timeline?.length ? "received" : "awaiting") : summary(s.key, entries as TimelineEntry[]);
        return (
          <Hint as="li" key={s.key} className="rounded-2xl" title={s.label} text={s.help}
            note={timeline?.length ? <>This request: <span className="font-medium text-ink-2">{said}</span>{at != null && s.key !== "request" ? ` at +${fmtMs(at)}` : ""}</> : undefined}>
            <div className={clsx("stage flex h-full flex-col gap-1.5 rounded-2xl border px-3 py-3",
              t === "good" && "border-mint/20 bg-mint-soft/60", t === "bad" && "border-danger/20 bg-danger-soft/70",
              t === "warn" && "border-warn/20 bg-warn-soft/60", t === "idle" && "border-line bg-white/50")}>
              <div className="flex items-center justify-between">
                <Icon className={clsx("h-4 w-4", t === "good" ? "text-mint" : t === "bad" ? "text-danger" : t === "warn" ? "text-warn" : "text-ink-3")} strokeWidth={1.75} />
                <span className="text-[10px] font-semibold text-ink-3 num">{i + 1}</span>
              </div>
              <div className="text-[12.5px] font-semibold text-ink">{s.label}</div>
              <div className="truncate text-[11.5px] text-ink-2">{said}</div>
              {at != null && s.key !== "request" && <div className="text-[10.5px] text-ink-3 num">+{fmtMs(at)}</div>}
            </div>
          </Hint>
        );
      })}
    </ol>
  );
}
