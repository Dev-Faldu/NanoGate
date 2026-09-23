/** Chart primitives. Palette: validated categorical order (dataviz reference instance, light mode).
 * Thin marks, recessive grid/axes, text in ink tokens, tooltips show real values + sample counts. */
import type { ReactNode } from "react";

export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
export const DIVERGING = { neg: "#2a78d6", pos: "#e34948", mid: "#f0efec" };
export const GRID = "#e1e0d9";
export const AXIS = { stroke: "#c3c2b7", tick: { fill: "#898781", fontSize: 11 }, tickLine: false } as const;

export function ChartTooltip({ active, payload, label, fmt, labelFmt, extra }: {
  active?: boolean; payload?: any[]; label?: any; fmt?: (v: number, name: string) => string; labelFmt?: (l: any) => ReactNode;
  extra?: (p: any) => ReactNode;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-line bg-white/95 px-3 py-2 text-[12px] shadow-lift backdrop-blur">
      {label !== undefined && <div className="mb-1 font-semibold text-ink">{labelFmt ? labelFmt(label) : label}</div>}
      {payload.map((p) => (
        <div key={p.dataKey + p.name} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-ink-2">
            <span className="h-2 w-2 rounded-full" style={{ background: p.color ?? p.fill ?? p.stroke }} />{p.name}
          </span>
          <span className="num font-medium text-ink">{typeof p.value === "number" ? (fmt ? fmt(p.value, p.name) : p.value.toFixed(3)) : String(p.value)}</span>
        </div>
      ))}
      {extra && extra(payload[0]?.payload)}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2" role="list">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5" role="listitem">
          <span className="inline-block h-[2px] w-4 rounded" style={{ background: i.dashed ? `repeating-linear-gradient(90deg, ${i.color} 0 4px, transparent 4px 7px)` : i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

export function ChartFrame({ title, meta, children, legend }: { title: string; meta?: ReactNode; children: ReactNode; legend?: ReactNode }) {
  return (
    <figure className="flex flex-col gap-3">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[13.5px] font-semibold text-ink">{title}</span>
        {meta && <span className="text-[11.5px] text-ink-3">{meta}</span>}
      </figcaption>
      {legend}
      <div className="h-[260px] w-full">{children}</div>
    </figure>
  );
}
