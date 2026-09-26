/** Shared building blocks for the operations pages: modal, form fields, Markdown, role hook. */
import { useEffect, useRef, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { X } from "lucide-react";
import { ApiError, get } from "../api/client";

export interface Me { key_id: string; tenant_id: string; department_id: string; role: string; scopes: string[] }

export function useMe() {
  const q = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/me"), staleTime: 60_000 });
  return { me: q.data, canWrite: !!q.data?.scopes.includes("admin"), loading: q.isLoading };
}

export function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

export function Modal({ open, onClose, title, subtitle, children, width = 520 }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const t = setTimeout(() => ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus(), 60);
    return () => { window.removeEventListener("keydown", onKey); clearTimeout(t); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <button className="absolute inset-0 bg-ink/15 backdrop-blur-[3px]" aria-label="Close" onClick={onClose} />
      <div ref={ref} className="glass-menu relative max-h-[calc(100vh-2rem)] w-full animate-fadein overflow-y-auto !p-0"
        style={{ maxWidth: width }}>
        <header className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
          <div>
            <h2 className="text-[16px] font-semibold tracking-tight text-ink">{title}</h2>
            {subtitle && <p className="mt-1 text-[13px] text-ink-3">{subtitle}</p>}
          </div>
          <button type="button" className="-mr-2 -mt-1 rounded-lg p-1.5 text-ink-3 hover:bg-ink/5 hover:text-ink" onClick={onClose}
            aria-label="Close"><X className="h-4 w-4" /></button>
        </header>
        <div className="glass-rule" />
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={clsx("block", className)}>
      <span className="mb-1.5 block text-[12.5px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}

export function Toggle({ on, onChange, disabled, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
      className={clsx("relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-50",
        on ? "bg-ink" : "bg-ink/15")}>
      <span className={clsx("inline-block h-5 w-5 rounded-full bg-white shadow transition-transform duration-200",
        on ? "translate-x-[22px]" : "translate-x-0.5")} />
    </button>
  );
}

export function ReadOnlyNote() {
  return <div className="rounded-xl border border-white/80 bg-warn-soft/70 px-3 py-2 text-[12.5px] text-warn">
    You are signed in as an auditor: you can see everything here but not change it.
  </div>;
}

/** Minimal, safe Markdown (paragraphs, headings, lists, bold, italics, inline code, code blocks). No HTML injection. */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={blocks.length} className="my-2 overflow-x-auto rounded-xl bg-ink/[0.04] p-3 font-mono text-[12px] leading-relaxed text-ink">{body.join("\n")}</pre>);
      continue;
    }
    if (/^\s*([-*•]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+\.)\s+/, ""));
      const Tag = ordered ? "ol" : "ul";
      blocks.push(<Tag key={blocks.length} className={clsx("my-1.5 space-y-1 pl-5", ordered ? "list-decimal" : "list-disc")}>
        {items.map((it, j) => <li key={j}>{inline(it)}</li>)}</Tag>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l);
    if (h) {
      blocks.push(<div key={blocks.length} className="mb-1 mt-3 font-semibold text-ink first:mt-0">{inline(h[2])}</div>);
      i++;
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*•]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={blocks.length} className="my-1.5 first:mt-0 last:mb-0">{inline(para.join(" "))}</p>);
  }
  return <div className={clsx("text-[13.5px] leading-relaxed text-ink", className)}>{blocks}</div>;
}

function inline(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) out.push(<code key={out.length} className="rounded bg-ink/[0.06] px-1 py-0.5 font-mono text-[12px]">{t.slice(1, -1)}</code>);
    else if (t.startsWith("**")) out.push(<strong key={out.length} className="font-semibold">{t.slice(2, -2)}</strong>);
    else out.push(<em key={out.length}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export function Bar({ value, tone = "ink" }: { value: number | null | undefined; tone?: "ink" | "warn" | "bad" }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink/[0.07]">
      <div className={clsx("h-full rounded-full transition-all duration-500", tone === "bad" ? "bg-danger" : tone === "warn" ? "bg-warn" : "bg-ink/70")}
        style={{ width: `${v * 100}%` }} />
    </div>
  );
}
