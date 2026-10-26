import type { ActivityItem } from "@/services/api";

// AI and system actions (no human actor) are marked so the reader can tell what the machine did on its own.
function actorKind(item: ActivityItem): "ai" | "human" {
  return item.actor ? "human" : "ai";
}

/** The audit trail as a story: "14:27:01  Ticket created by Sarah", oldest first. */
export function ActivityTimeline({ items, showDate = false }: { items: ActivityItem[]; showDate?: boolean }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">No activity recorded.</p>;
  return (
    <ol className="relative space-y-2.5 border-l border-slate-800 pl-4">
      {items.map((item) => {
        const kind = actorKind(item);
        const ts = new Date(item.occurred_at).toISOString();
        return (
          <li key={item.id} className="relative text-[13px] leading-snug">
            <span
              className={`absolute -left-[21px] top-1 flex h-2.5 w-2.5 rounded-full ring-2 ring-slate-950 ${kind === "ai" ? "bg-sky-400" : "bg-slate-500"}`}
              aria-hidden
            />
            <time dateTime={item.occurred_at} className="mr-2 font-mono text-[11px] tabular-nums text-slate-500">
              {showDate ? ts.slice(5, 10) + " " : ""}{ts.slice(11, 19)}
            </time>
            <span className="text-slate-200">{item.description}</span>
            {kind === "ai" && (
              <span className="ml-1.5 rounded bg-sky-500/10 px-1 text-[10px] uppercase tracking-wide text-sky-300" title="Performed by OpsPilot (AI / background worker)">
                auto
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
