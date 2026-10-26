import type { IncidentDetail, Investigation } from "@/services/api";

const SEVERITY_WORD = { SEV1: "Critical", SEV2: "Major", SEV3: "Minor", SEV4: "Low" } as const;

/** Plain-text status update for Slack / Teams: the root cause, the evidence and the next steps, with citations. */
export function statusUpdate(i: IncidentDetail, inv: Investigation): string {
  const findings = inv.evidence.filter((e) => e.kind !== "DOCUMENT" && e.stance !== "CONTEXT");
  return [
    `${i.number} — ${i.title} (${i.severity} ${SEVERITY_WORD[i.severity]}, ${i.status})`,
    `Probable root cause (${Math.round((inv.confidence ?? 0) * 100)}% confidence): ${inv.root_cause}`,
    "Evidence:",
    ...findings.slice(0, 5).map((e) => `  ${e.stance === "SUPPORTS" ? "✓" : "✗"} ${e.summary} [${e.ref}]`),
    "Next steps:",
    ...inv.recommended_steps.slice(0, 3).map((s, n) => `  ${n + 1}. ${s}`),
  ].join("\n");
}
