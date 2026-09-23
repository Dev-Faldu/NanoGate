/** Formatting helpers. `null`/`undefined` always renders as "Unavailable" — never as zero. */
export const UNAVAILABLE = "Unavailable";

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function fmtInt(v: number | null | undefined): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-US") : UNAVAILABLE;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : UNAVAILABLE;
}

export function fmtMs(v: number | null | undefined): string {
  if (!isNum(v)) return UNAVAILABLE;
  if (v >= 10000) return `${(v / 1000).toFixed(1)} s`;
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${v.toFixed(v < 10 ? 1 : 0)} ms`;
}

export function fmtUsd(v: number | null | undefined, digits?: number): string {
  if (!isNum(v)) return UNAVAILABLE;
  const d = digits ?? (Math.abs(v) >= 1 ? 2 : Math.abs(v) >= 0.01 ? 4 : 6);
  return `$${v.toFixed(d)}`;
}

export function fmtBytes(v: number | null | undefined): string {
  if (!isNum(v)) return UNAVAILABLE;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let x = v;
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024;
    i++;
  }
  return `${x.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  return isNum(v) ? v.toFixed(digits) : UNAVAILABLE;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!isNum(ts)) return UNAVAILABLE;
  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false });
}

export function fmtDateTime(ts: number | null | undefined): string {
  if (!isNum(ts)) return UNAVAILABLE;
  return new Date(ts * 1000).toLocaleString("en-US", { hour12: false, dateStyle: "medium", timeStyle: "medium" });
}

export function fmtAge(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return UNAVAILABLE;
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return UNAVAILABLE;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(seconds % 60)}s`;
}

export const short = (s: string | null | undefined, n = 10) => (s ? (s.length > n ? `${s.slice(0, n)}…` : s) : "—");

/** Fixed categorical order (validated palette, dataviz skill reference instance). Color follows the route entity. */
export const ROUTE_COLORS: Record<string, string> = {
  local: "#2a78d6",
  cache: "#eb6834",
  local_large: "#1baf7a",
  remote: "#eda100",
  remote_simulated: "#e87ba4",
  denied: "#898781",
  error: "#d03b3b",
};

export const ROUTE_LABEL: Record<string, string> = {
  local: "Local",
  cache: "Verified cache",
  local_large: "Local large",
  remote: "Remote",
  remote_simulated: "Simulated larger tier",
  denied: "Denied",
  error: "Error",
  none: "—",
};
