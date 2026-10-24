import type { IncidentStatus, Priority, Severity, TicketStatus } from "@/services/api";

export function Card({ title, action, children, className = "" }: {
  title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/60 ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

// Severity uses the reserved status palette (critical / serious / warning) and always ships with its label.
const SEVERITY_STYLE: Record<Severity, string> = {
  SEV1: "bg-[#d03b3b]/15 text-[#f08a8a] ring-[#d03b3b]/50",
  SEV2: "bg-[#ec835a]/15 text-[#f3ab8e] ring-[#ec835a]/50",
  SEV3: "bg-[#fab219]/15 text-[#fcd27a] ring-[#fab219]/50",
  SEV4: "bg-slate-500/15 text-slate-300 ring-slate-500/40",
};
const SEVERITY_LABEL: Record<Severity, string> = { SEV1: "Critical", SEV2: "Major", SEV3: "Minor", SEV4: "Low" };

export function SeverityBadge({ severity, long = false }: { severity: Severity; long?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold ring-1 ${SEVERITY_STYLE[severity]}`}>
      {severity}{long && <span className="font-normal uppercase">· {SEVERITY_LABEL[severity]}</span>}
    </span>
  );
}

const PRIORITY_TO_SEVERITY: Record<Priority, Severity> = { P1: "SEV1", P2: "SEV2", P3: "SEV3", P4: "SEV4" };
export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold ring-1 ${SEVERITY_STYLE[PRIORITY_TO_SEVERITY[priority]]}`}>
      {priority}
    </span>
  );
}

const STATUS_STYLE: Record<string, string> = {
  OPEN: "text-sky-300 bg-sky-500/10 ring-sky-500/30",
  TRIAGED: "text-violet-300 bg-violet-500/10 ring-violet-500/30",
  INVESTIGATING: "text-amber-300 bg-amber-500/10 ring-amber-500/30",
  IDENTIFIED: "text-orange-300 bg-orange-500/10 ring-orange-500/30",
  MONITORING: "text-cyan-300 bg-cyan-500/10 ring-cyan-500/30",
  RESOLVED: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30",
  CLOSED: "text-slate-400 bg-slate-500/10 ring-slate-500/30",
};

export function StatusBadge({ status }: { status: TicketStatus | IncidentStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${STATUS_STYLE[status]}`}>
      {status !== "RESOLVED" && status !== "CLOSED" && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {status}
    </span>
  );
}

const HEALTH: Record<string, { label: string; dot: string; icon: string }> = {
  healthy: { label: "Healthy", dot: "bg-[#0ca30c]", icon: "✓" },
  degraded: { label: "Degraded", dot: "bg-[#fab219]", icon: "!" },
  critical: { label: "Critical", dot: "bg-[#d03b3b]", icon: "✕" },
  unknown: { label: "No data", dot: "bg-slate-500", icon: "?" },
};

export function HealthPill({ status }: { status: string }) {
  const h = HEALTH[status] ?? HEALTH.unknown;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-200">
      <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-slate-950 ${h.dot}`}>
        {h.icon}
      </span>
      {h.label}
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="animate-pulse py-10 text-center text-sm text-slate-500">{label}</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-200">{message}</div>;
}

export function relativeTime(iso: string, now = Date.now()): string {
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function formatDuration(minutes: number | null): string {
  if (minutes === null) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  return `${h}h ${Math.round(minutes % 60)}m`;
}

export function utcTime(iso: string): string {
  return new Date(iso).toISOString().slice(11, 16) + " UTC";
}

export function utcDateTime(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function Pager({ page, total, size, onChange }: { page: number; total: number; size: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
      <span>{total} total</span>
      <div className="flex items-center gap-2">
        <button disabled={page <= 1} onClick={() => onChange(page - 1)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">Prev</button>
        <span>{page} / {pages}</span>
        <button disabled={page >= pages} onClick={() => onChange(page + 1)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">Next</button>
      </div>
    </div>
  );
}
