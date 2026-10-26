import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import type { ActivityItem } from "@/services/api";

const item = (id: number, description: string, human: boolean, at: string): ActivityItem => ({
  id, description, occurred_at: at, action: "x", entity_type: "ticket", entity_id: "TCK-1",
  actor: human ? { id: 1, email: "sam@opspilot.dev", full_name: "Sam Rivera" } : null,
});

describe("ActivityTimeline", () => {
  it("lists events with UTC times and marks what OpsPilot did on its own", () => {
    render(<ActivityTimeline items={[
      item(1, "Ticket TCK-1 created by Sam Rivera", true, "2026-10-26T14:27:01Z"),
      item(2, "AI classification started for TCK-1", false, "2026-10-26T14:27:04Z"),
    ]} />);

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("14:27:01Ticket TCK-1 created by Sam Rivera");
    expect(rows[0]).not.toHaveTextContent("auto");
    expect(rows[1]).toHaveTextContent("14:27:04AI classification started for TCK-1auto");
  });

  it("says so when there is nothing", () => {
    render(<ActivityTimeline items={[]} />);
    expect(screen.getByText("No activity recorded.")).toBeInTheDocument();
  });
});
