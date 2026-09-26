import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import {
  AlertTriangle, Check, CircleSlash, Copy, FlaskConical, Globe2, Loader2, Radio, ShieldAlert, Sparkles, WifiOff,
} from "lucide-react";
import { ROUTE_COLORS, ROUTE_LABEL } from "../lib/format";

export function Card({ children, className, hover, ...rest }: { children: ReactNode; className?: string; hover?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <section className={clsx("panel animate-fadein", hover && "panel-hover", className)} {...rest}>
      {children}
    </section>
  );
}

export function CardHeader({ title, eyebrow, right, icon, subtitle }: {
  title: ReactNode; eyebrow?: ReactNode; right?: ReactNode; icon?: ReactNode; subtitle?: ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-4 border-b border-hair px-6 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <div className="mt-0.5 text-ink-3">{icon}</div>}
        <div className="min-w-0">
          {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
          <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle && <p className="mt-0.5 text-[13px] text-ink-3">{subtitle}</p>}
        </div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </header>
  );
}

export function PageHeader({ title, subtitle, right, eyebrow }: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
        <h1 className="text-[34px] font-semibold leading-tight tracking-[-0.02em] text-ink">{title}</h1>
        {subtitle && <p className="mt-2 max-w-2xl text-[15px] text-ink-2">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}

/** Provenance label: every number says where it came from. */
export type Prov = "Measured" | "Target" | "Scenario" | "Public dataset" | "Live device telemetry" | "Demo telemetry"
  | "Simulated larger tier" | "Preview" | "Benchmark" | "Configuration" | "Synthetic security data";

const PROV_STYLE: Record<Prov, string> = {
  Measured: "bg-mint-soft text-mint",
  "Live device telemetry": "bg-cyan-soft text-cyan",
  Benchmark: "bg-peri-soft text-peri",
  "Public dataset": "bg-lilac-soft text-lilac",
  Target: "bg-hair text-ink-2",
  Scenario: "bg-warn-soft text-warn",
  Preview: "bg-warn-soft text-warn",
  Configuration: "bg-hair text-ink-2",
  "Demo telemetry": "bg-danger-soft text-danger",
  "Simulated larger tier": "bg-blush-soft text-blush",
  "Synthetic security data": "bg-blush-soft text-blush",
};

export function ProvenanceTag({ kind, title }: { kind: Prov; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold tracking-wide", PROV_STYLE[kind])}>
      {kind}
    </span>
  );
}

/** Explains the thing under the pointer in plain words. The card renders in a top layer (portal, fixed position),
 *  opens after a short pause on hover or immediately on keyboard focus, flips above when there is no room below,
 *  and stays inside the viewport. */
export function Hint({ title, text, note, children, className, as: Tag = "div" }: {
  title?: ReactNode; text: ReactNode; note?: ReactNode; children: ReactNode; className?: string; as?: "div" | "li" | "span";
}) {
  const ref = useRef<HTMLElement>(null);
  const timer = useRef<number>();
  const [pos, setPos] = useState<{ left: number; top: number; below: boolean } | null>(null);
  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const W = 272, H = 150, M = 12;
    const left = Math.min(Math.max(M, r.left + r.width / 2 - W / 2), window.innerWidth - W - M);
    const below = r.bottom + 8 + H < window.innerHeight || r.top < H + 16;
    setPos({ left, top: below ? r.bottom + 8 : r.top - 8, below });
  }, []);
  const show = (delay: number) => { window.clearTimeout(timer.current); timer.current = window.setTimeout(place, delay); };
  const hide = () => { window.clearTimeout(timer.current); setPos(null); };
  useEffect(() => {
    if (!pos) return;
    const close = () => hide();
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); };
  }, [pos]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const El = Tag as any;
  return (
    <El ref={ref} tabIndex={0} className={clsx("hint", className)}
      onMouseEnter={() => show(220)} onMouseLeave={hide} onFocus={() => show(0)} onBlur={hide}>
      {children}
      {pos && createPortal(
        <div role="tooltip" className="hovercard"
          style={pos.below ? { left: pos.left, top: pos.top } : { left: pos.left, bottom: window.innerHeight - pos.top }}>
          {title && <div className="hovercard-title">{title}</div>}
          <div>{text}</div>
          {note && <div className="hovercard-note">{note}</div>}
        </div>, document.body)}
    </El>
  );
}

export function Stat({ label, value, sub, prov, icon, tone, testid, hint }: {
  label: string; value: ReactNode; sub?: ReactNode; prov?: Prov; icon?: ReactNode; tone?: "good" | "warn" | "bad"; testid?: string;
  hint?: { text: ReactNode; note?: ReactNode };
}) {
  const unavailable = value === "Unavailable";
  const body = (
    <div className="flex min-w-0 flex-col gap-2 px-6 py-5" data-testid={testid}>
      <div className="flex min-w-0 items-center gap-2 text-[12.5px] font-medium text-ink-2">
        {icon && <span className="shrink-0 text-ink-3">{icon}</span>}
        <span className="truncate">{label}</span>
      </div>
      <div className={clsx("truncate text-[28px] font-semibold leading-none tracking-[-0.02em]",
        unavailable ? "text-[18px] font-medium text-ink-3" : tone === "bad" ? "text-danger" : tone === "warn" ? "text-warn" : "text-ink")}>
        {value}
      </div>
      <div className="flex min-w-0 items-center gap-2">
        {prov && <ProvenanceTag kind={prov} />}
        {sub && <div className="min-w-0 truncate text-[12px] text-ink-3" title={typeof sub === "string" ? sub : undefined}>{sub}</div>}
      </div>
    </div>
  );
  if (!hint) return body;
  return (
    <Hint className="tile min-w-0" title={label} text={hint.text}
      note={unavailable ? "Shows a number once real requests have flowed through the gateway." : hint.note}>
      {body}
    </Hint>
  );
}

export function Badge({ children, tone = "neutral", className, title }: {
  children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info" | "lilac" | "cyan" | "blush"; className?: string; title?: string;
}) {
  const t = {
    neutral: "bg-hair text-ink-2 border-line",
    good: "bg-mint-soft text-mint border-mint/15",
    warn: "bg-warn-soft text-warn border-warn/15",
    bad: "bg-danger-soft text-danger border-danger/15",
    info: "bg-peri-soft text-peri border-peri/15",
    lilac: "bg-lilac-soft text-lilac border-lilac/15",
    cyan: "bg-cyan-soft text-cyan border-cyan/15",
    blush: "bg-blush-soft text-blush border-blush/15",
  }[tone];
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold", t, className)}>
      {children}
    </span>
  );
}

const REASON_TONE: Record<string, Parameters<typeof Badge>[0]["tone"]> = {
  CACHE_VERIFIED: "good", LOCAL_CONFIDENT: "good", LOCAL_LARGE_SELECTED: "cyan", REMOTE_ALLOWED: "info",
  SENSITIVE_LOCAL_ONLY: "lilac", PII_REDACTED: "lilac", OUTPUT_REDACTED: "lilac", CACHE_HARD_NEGATIVE: "warn",
  CACHE_NAMESPACE_MISMATCH: "warn", CACHE_STALE: "warn", CACHE_CONTEXT_MISMATCH: "warn", CACHE_SOURCE_REVOKED: "warn",
  ROUTER_ABSTAIN: "warn", ROUTER_POST_HOC_STREAM: "warn", REMOTE_DISABLED: "neutral", REMOTE_UNAVAILABLE: "warn",
  CONNECTOR_UNAVAILABLE: "warn", ROUTER_UNAVAILABLE: "warn", CACHE_MISS: "neutral", CACHE_INELIGIBLE: "neutral",
};

export function ReasonBadge({ code, className }: { code: string | null | undefined; className?: string }) {
  if (!code) return <span className="text-ink-3">—</span>;
  const tone = REASON_TONE[code] ?? "bad";
  return <Badge tone={tone} className={clsx("font-mono !text-[10.5px] tracking-tight", className)}>{code}</Badge>;
}

export function RouteBadge({ route }: { route: string | null | undefined }) {
  const r = route ?? "none";
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] text-ink">
      <span className="h-2 w-2 rounded-full" style={{ background: ROUTE_COLORS[r] ?? "#c3c2b7" }} aria-hidden />
      {ROUTE_LABEL[r] ?? r}
    </span>
  );
}

const CLASS_TONE: Record<string, Parameters<typeof Badge>[0]["tone"]> = {
  Public: "neutral", Internal: "info", Confidential: "lilac", Restricted: "blush", Secret: "bad", Unknown: "warn",
};

export function DataClassBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-ink-3">—</span>;
  return <Badge tone={CLASS_TONE[value] ?? "neutral"}>{value}</Badge>;
}

export function StatusDot({ state }: { state: "ok" | "warn" | "bad" | "off" | "unknown" }) {
  const c = { ok: "bg-mint", warn: "bg-warn", bad: "bg-danger", off: "bg-ink-3/40", unknown: "bg-ink-3/40" }[state];
  return <span className={clsx("inline-block h-2 w-2 rounded-full", c)} aria-label={state} />;
}

/** Explicit panel states. Never render fake data in place of an unavailable source. */
export function StateView({ kind, title, detail, action }: {
  kind: "loading" | "empty" | "unavailable" | "error" | "degraded"; title?: string; detail?: ReactNode; action?: ReactNode;
}) {
  const icon = {
    loading: <Loader2 className="h-5 w-5 animate-spin" />, empty: <CircleSlash className="h-5 w-5" />,
    unavailable: <WifiOff className="h-5 w-5" />, error: <ShieldAlert className="h-5 w-5" />, degraded: <AlertTriangle className="h-5 w-5" />,
  }[kind];
  const defaults = { loading: "Loading", empty: "Nothing recorded yet", unavailable: "Unavailable", error: "Request failed", degraded: "Degraded" };
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center" role={kind === "error" ? "alert" : "status"}>
      <div className={clsx("text-ink-3", kind === "error" && "text-danger", kind === "degraded" && "text-warn")}>{icon}</div>
      <div className="text-sm font-medium text-ink-2">{title ?? defaults[kind]}</div>
      {detail && <div className="max-w-md text-[12.5px] text-ink-3">{detail}</div>}
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-lg bg-hair", className)} />;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="btn-ghost !px-2.5 !py-1.5 !text-xs" aria-label={`${label}: ${text}`}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        });
      }}>
      {done ? <Check className="h-3.5 w-3.5 text-mint" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? "Copied" : label}
    </button>
  );
}

export function KV({ rows, cols = 2 }: { rows: [ReactNode, ReactNode][]; cols?: 1 | 2 | 3 }) {
  return (
    <dl className={clsx("grid gap-x-8 gap-y-3", cols === 1 ? "grid-cols-1" : cols === 2 ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1 md:grid-cols-3")}>
      {rows.map(([k, v], i) => (
        <div key={i} className="flex min-w-0 items-baseline justify-between gap-4 border-b border-hair pb-2.5">
          <dt className="shrink-0 text-[12.5px] text-ink-3">{k}</dt>
          <dd className="min-w-0 truncate text-right text-[13px] font-medium text-ink num">{v ?? <span className="text-ink-3">Unavailable</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Hash({ value, n = 16 }: { value: string | null | undefined; n?: number }) {
  if (!value) return <span className="text-ink-3">—</span>;
  return <span className="mono text-ink-2" title={value}>{value.slice(0, n)}…</span>;
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode }[] }) {
  return (
    <div role="tablist" className="inline-flex rounded-xl border border-white/80 bg-white/50 p-1 shadow-[0_0_0_1px_rgba(15,27,51,0.05)] backdrop-blur-md">
      {items.map((it) => (
        <button key={it.id} role="tab" aria-selected={value === it.id} onClick={() => onChange(it.id)}
          className={clsx("rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors duration-180",
            value === it.id ? "bg-ink text-white shadow-soft" : "text-ink-2 hover:bg-white/80 hover:text-ink")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function ConnectorBadge({ mode, state }: { mode?: string; state?: string }) {
  if (mode === "mock") return <Badge tone="blush"><FlaskConical className="h-3 w-3" />Simulated larger tier</Badge>;
  if (mode === "disabled") return <Badge tone="neutral"><CircleSlash className="h-3 w-3" />Remote disabled</Badge>;
  if (mode === "outage-test") return <Badge tone="warn"><AlertTriangle className="h-3 w-3" />Outage test · {state}</Badge>;
  if (mode === "live") return <Badge tone={state === "healthy" ? "info" : "warn"}><Globe2 className="h-3 w-3" />Live remote · {state}</Badge>;
  return <Badge tone="neutral"><Radio className="h-3 w-3" />{mode ?? "unknown"}</Badge>;
}

export function Explainer({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-xl border border-white/80 bg-white/50 px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-2">
      <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-peri" />
      <div>{children}</div>
    </div>
  );
}
