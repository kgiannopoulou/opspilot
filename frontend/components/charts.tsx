"use client";

import { useEffect, useRef, useState } from "react";

// Chart tokens (validated for the dark surface): one categorical hue for single-series lines, the reserved
// status palette for severity. Text always uses ink tokens, never the series colour.
const INK = { primary: "#f0efec", secondary: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835" };
const SERIES_BLUE = "#3987e5";
export const SEVERITY_COLORS = { SEV1: "#d03b3b", SEV2: "#ec835a", SEV3: "#fab219", SEV4: "#64748b" } as const;
type Sev = keyof typeof SEVERITY_COLORS;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s * 4 >= value) ?? magnitude * 10;
  return step * 4;
}

const shortDate = (iso: string) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: React.ReactNode }) {
  const left = Math.min(Math.max(x - 70, 0), width - 150);
  return (
    <div
      className="pointer-events-none absolute z-10 w-[150px] rounded-md border border-white/10 bg-slate-950/95 px-2.5 py-2 text-xs shadow-xl"
      style={{ left, top: Math.max(y - 8, 0), transform: "translateY(-100%)" }}
    >
      {children}
    </div>
  );
}

const PAD = { top: 12, right: 8, bottom: 22, left: 28 };

/** Daily incident counts, stacked by severity. */
export function SeverityStackedBars({ data, height = 190 }: {
  data: ({ date: string } & Record<Sev, number>)[]; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const severities: Sev[] = ["SEV1", "SEV2", "SEV3", "SEV4"];
  const totals = data.map((d) => severities.reduce((sum, s) => sum + d[s], 0));
  const max = niceMax(Math.max(1, ...totals));
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const slot = plotW / data.length;
  const barW = Math.max(3, Math.min(18, slot - 4));
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];

  return (
    <div ref={ref} className="relative">
      <Legend items={severities.map((s) => ({ label: s, color: SEVERITY_COLORS[s] }))} />
      <svg width={width} height={height} role="img" aria-label="Incidents per day by severity, last 30 days">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? INK.axis : INK.grid} />
            <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" fontSize="10" fill={INK.muted}>{t}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = PAD.left + i * slot + (slot - barW) / 2;
          let acc = 0;
          return (
            <g key={d.date} opacity={hover === null || hover === i ? 1 : 0.45}>
              {severities.map((s) => {
                if (!d[s]) return null;
                const top = y(acc + d[s]);
                const h = y(acc) - top - (acc > 0 ? 2 : 0); // 2px surface gap between stacked segments
                acc += d[s];
                return <rect key={s} x={x} y={top} width={barW} height={Math.max(h, 1)} rx={2} fill={SEVERITY_COLORS[s]} />;
              })}
            </g>
          );
        })}
        {data.map((d, i) =>
          i % 7 === 0 || i === data.length - 1 ? (
            <text key={d.date} x={PAD.left + i * slot + slot / 2} y={height - 6} textAnchor="middle" fontSize="10" fill={INK.muted}>
              {shortDate(d.date)}
            </text>
          ) : null,
        )}
        {/* Hit targets: full-height columns, wider than the bars. */}
        {data.map((d, i) => (
          <rect key={`hit-${d.date}`} x={PAD.left + i * slot} y={PAD.top} width={slot} height={plotH} fill="transparent"
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      {hover !== null && (
        <Tooltip x={PAD.left + hover * slot + slot / 2} y={y(totals[hover]) + 24} width={width}>
          <div className="mb-1 font-medium text-slate-100">{shortDate(data[hover].date)}</div>
          {severities.map((s) => (
            <div key={s} className="flex items-center justify-between text-slate-300">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm" style={{ background: SEVERITY_COLORS[s] }} />{s}
              </span>
              <span className="tabular-nums">{data[hover][s]}</span>
            </div>
          ))}
          <div className="mt-1 flex justify-between border-t border-white/10 pt-1 text-slate-100">
            <span>Total</span><span className="tabular-nums">{totals[hover]}</span>
          </div>
        </Tooltip>
      )}
    </div>
  );
}

/** Single-series daily volume as an area with a crosshair tooltip. */
export function VolumeArea({ data, label, height = 190 }: {
  data: { date: string; count: number }[]; label: string; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(1, ...data.map((d) => d.count)));
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (data.length === 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d.count)}`).join("");
  const area = `${line}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - box.left) / box.width) * (data.length - 1));
    setHover(Math.min(Math.max(i, 0), data.length - 1));
  };

  return (
    <div ref={ref} className="relative">
      <svg width={width} height={height} role="img" aria-label={`${label}, last 30 days`}>
        <defs>
          <linearGradient id="vol-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={SERIES_BLUE} stopOpacity="0.35" />
            <stop offset="1" stopColor={SERIES_BLUE} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? INK.axis : INK.grid} />
            <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" fontSize="10" fill={INK.muted}>{t}</text>
          </g>
        ))}
        <path d={area} fill="url(#vol-fill)" />
        <path d={line} fill="none" stroke={SERIES_BLUE} strokeWidth={2} strokeLinejoin="round" />
        {data.map((d, i) =>
          i % 7 === 0 || i === data.length - 1 ? (
            <text key={d.date} x={x(i)} y={height - 6} textAnchor="middle" fontSize="10" fill={INK.muted}>{shortDate(d.date)}</text>
          ) : null,
        )}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={y(0)} stroke={INK.secondary} strokeDasharray="3 3" />
            <circle cx={x(hover)} cy={y(data[hover].count)} r={4.5} fill={SERIES_BLUE} stroke="#0f172a" strokeWidth={2} />
          </g>
        )}
        <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="transparent" onMouseMove={onMove}
          onMouseLeave={() => setHover(null)} />
      </svg>
      {hover !== null && (
        <Tooltip x={x(hover)} y={y(data[hover].count)} width={width}>
          <div className="font-medium text-slate-100">{shortDate(data[hover].date)}</div>
          <div className="flex justify-between text-slate-300"><span>{label}</span><span className="tabular-nums">{data[hover].count}</span></div>
        </Tooltip>
      )}
    </div>
  );
}

/** Tiny trend line for tables; the latest value is spelled out next to it, so the line only shows shape. */
export function Sparkline({ values, color = SERIES_BLUE, width = 120, height = 28 }: {
  values: number[]; color?: string; width?: number; height?: number;
}) {
  if (values.length < 2) return <span className="text-xs text-slate-600">no data</span>;
  const max = Math.max(...values, 1e-9);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - 2 - (v / max) * (height - 4)}`);
  return (
    <svg width={width} height={height} aria-hidden="true">
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth={1.75} strokeLinejoin="round" />
    </svg>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="mb-2 flex flex-wrap gap-3 text-xs text-slate-400">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />{item.label}
        </span>
      ))}
    </div>
  );
}
