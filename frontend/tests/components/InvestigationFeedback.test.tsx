import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InvestigationFeedback } from "@/components/InvestigationFeedback";

const PATH = "/api/v1/incidents/INC-7/investigations/3/feedback";

function mockApi(summaries: unknown[]) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  let n = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const body = (init.method ?? "GET") === "GET" ? summaries[Math.min(n++, summaries.length - 1)] : { id: 1 };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}

const empty = { mine: null, useful: 2, incorrect: 0, reasons: {} };

describe("InvestigationFeedback", () => {
  it("records a 👍 with one click", async () => {
    const calls = mockApi([empty, { ...empty, useful: 3, mine: { rating: 1 } }]);
    render(<InvestigationFeedback incident="INC-7" investigationId={3} canRate />);
    expect(await screen.findByText("2 useful · 0 incorrect")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "👍 Useful" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "👍 Useful" })).toHaveAttribute("aria-pressed", "true"));
    expect(calls.find((c) => c.method === "PUT")).toEqual({ method: "PUT", url: PATH, body: { rating: 1 } });
  });

  it("asks why before recording a 👎", async () => {
    const calls = mockApi([empty]);
    render(<InvestigationFeedback incident="INC-7" investigationId={3} canRate />);
    await userEvent.click(await screen.findByRole("button", { name: "👎 Incorrect" }));
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    await userEvent.selectOptions(screen.getByLabelText("What was wrong?"), "MISSING_EVIDENCE");
    await userEvent.type(screen.getByLabelText("Comment"), "Missed the deploy at 09:02");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      rating: -1, reason: "MISSING_EVIDENCE", comment: "Missed the deploy at 09:02",
    }));
  });

  it("is read-only for viewers", async () => {
    mockApi([empty]);
    render(<InvestigationFeedback incident="INC-7" investigationId={3} canRate={false} />);
    expect(await screen.findByText("2 useful · 0 incorrect")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
