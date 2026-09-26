import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity, BadgeDollarSign, Cpu, DatabaseZap, FlaskConical, LayoutDashboard, LogOut, Radio, ScrollText, Search,
  ShieldCheck, User,
} from "lucide-react";
import { get, setToken } from "../api/client";
import { useEvents } from "../api/events";
import { Badge, ConnectorBadge, StatusDot } from "./ui";

const NAV = [
  { to: "/overview", label: "Overview", icon: LayoutDashboard },
  { to: "/requests", label: "Requests", icon: ScrollText },
  { to: "/policies", label: "Policies", icon: ShieldCheck },
  { to: "/router-lab", label: "Router Lab", icon: FlaskConical },
  { to: "/cache", label: "Verified Cache", icon: DatabaseZap },
  { to: "/finops", label: "FinOps", icon: BadgeDollarSign },
  { to: "/infrastructure", label: "Infrastructure", icon: Cpu },
];

export function Logo({ className }: { className?: string }) {
  return (
    <div className={clsx("flex items-center gap-2.5", className)}>
      <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden>
        <rect width="32" height="32" rx="9" fill="#0F1B33" />
        <path d="M9 22V10l7 7 7-7v12" fill="none" stroke="#E1F5EC" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-ink">NanoGate</div>
        <div className="text-[10.5px] font-medium text-ink-3">Local AI decision control plane</div>
      </div>
    </div>
  );
}

export default function Shell({ children, onLogout }: { children: ReactNode; onLogout: () => void }) {
  const nav = useNavigate();
  const { state: conn, lastTelemetry } = useEvents();
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);
  const status = useQuery({ queryKey: ["status"], queryFn: () => get<any>("/api/status"), refetchInterval: 15_000 });
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<any>("/api/me") });
  const comp = status.data?.components ?? {};
  const modelState = comp.model?.ok ? "ok" : status.isLoading ? "unknown" : "bad";
  const deviceOk = comp.telemetry?.ok;

  return (
    <div className="flex min-h-screen">
      <aside className="glass-chrome sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col border-r border-white/70 px-4 py-6 lg:flex">
        <Logo className="px-2" />
        <nav className="mt-10 flex flex-col gap-1" aria-label="Primary">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to}
              className={({ isActive }) => clsx("group flex items-center gap-3 rounded-xl px-3 py-2 text-[14px] font-medium transition-colors duration-180",
                isActive ? "bg-ink text-white shadow-soft" : "text-ink-2 hover:translate-x-0.5 hover:bg-white/70 hover:text-ink")}>
              <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="glass-surface mt-auto space-y-3 rounded-2xl p-4">
          <div className="eyebrow">Trusted boundary</div>
          <div className="flex items-center gap-2 text-[13px] text-ink">
            <StatusDot state={deviceOk ? "ok" : "unknown"} />
            {status.data ? "HP ZGX Nano · on-device" : "Checking device"}
          </div>
          <div className="text-[12px] text-ink-3">
            {lastTelemetry?.gpu_power_w != null ? `GPU ${lastTelemetry.gpu_power_w.toFixed(1)} W · ${lastTelemetry.gpu_util ?? "—"}% util` : "Telemetry pending"}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass-chrome sticky top-0 z-30 flex items-center gap-3 border-b border-white/70 px-4 py-3 sm:gap-4 sm:px-6 lg:px-10">
          <Logo className="shrink-0 lg:hidden [&>div:last-child]:hidden sm:[&>div:last-child]:block" />
          <form className="relative min-w-0 flex-1 md:max-w-md" role="search"
            onSubmit={(e) => {
              e.preventDefault();
              const v = q.trim();
              if (!v) return;
              if (v.startsWith("rcpt_")) nav(`/requests/${v}`);
              else nav(`/requests?q=${encodeURIComponent(v)}`);
            }}>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <input className="input !rounded-full !pl-9" placeholder="Search request, receipt or reason code" value={q}
              onChange={(e) => setQ(e.target.value)} aria-label="Global search" />
          </form>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Badge className="hidden xl:inline-flex" tone={status.data?.app_mode === "demo" ? "warn" : "neutral"} title="APP_MODE">
              {status.data?.app_mode === "demo" ? "Reproducible demo profile" : status.data?.app_mode ?? "…"}
            </Badge>
            {status.data?.telemetry_mode === "demo" && <Badge tone="bad">Demo telemetry</Badge>}
            <Badge tone={modelState === "ok" ? "good" : modelState === "bad" ? "bad" : "neutral"}
              title={comp.model?.reason ?? comp.model?.model}>
              <Cpu className="h-3 w-3" />
              <span className="hidden sm:inline">{modelState === "ok" ? "Model ready" : modelState === "bad" ? "Model unavailable" : "Model…"}</span>
            </Badge>
            {status.data && <span className="hidden lg:inline-flex"><ConnectorBadge mode={status.data.remote?.mode} state={status.data.remote?.state} /></span>}
            <Badge className="hidden md:inline-flex" tone={conn === "live" ? "good" : conn === "reconnecting" ? "warn" : "neutral"} title="Live event stream (SSE)">
              {conn === "live" ? <Radio className="h-3 w-3" /> : <Activity className="h-3 w-3" />}
              {conn === "live" ? "Live" : conn === "reconnecting" ? "Reconnecting" : "Connecting"}
            </Badge>
            <div className="relative">
              <button className="btn-ghost !rounded-full !p-2" aria-label="Account menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>
                <User className="h-4 w-4" />
              </button>
              {menu && (
                <div className="glass-menu absolute right-0 top-11 w-64 animate-fadein">
                  <div className="eyebrow mb-1">Signed in</div>
                  <div className="text-sm font-medium text-ink">Admin key {me.data?.key_id ?? "…"}</div>
                  <div className="text-[12px] text-ink-3">{me.data ? `${me.data.tenant_id} / ${me.data.department_id} · ${me.data.scopes.join(", ")}` : ""}</div>
                  <div className="mt-3 flex flex-col gap-1">
                    <a className="btn-ghost justify-start" href="/docs" target="_blank" rel="noreferrer">API reference (OpenAPI)</a>
                    <button className="btn-ghost justify-start" onClick={() => { setToken(null); onLogout(); }}>
                      <LogOut className="h-4 w-4" /> Sign out
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>
        <nav className="glass-chrome flex gap-1 overflow-x-auto border-b border-white/70 px-4 py-2 lg:hidden" aria-label="Primary (compact)">
          {NAV.map(({ to, label }) => (
            <NavLink key={to} to={to} className={({ isActive }) => clsx("whitespace-nowrap rounded-lg px-3 py-1.5 text-sm", isActive ? "bg-ink text-white" : "text-ink-2 hover:bg-white/70")}>
              {label}
            </NavLink>
          ))}
        </nav>
        <main className="mx-auto w-full max-w-[1480px] flex-1 px-4 pb-28 pt-8 sm:px-6 sm:pt-10 lg:px-10">{children}</main>
      </div>
    </div>
  );
}
