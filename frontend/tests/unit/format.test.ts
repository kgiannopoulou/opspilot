import { describe, expect, it } from "vitest";
import { formatDuration, relativeTime, utcDateTime, utcTime } from "@/components/ui";

describe("time formatting", () => {
  const now = Date.parse("2026-10-26T14:30:00Z");

  it("describes how long ago something happened", () => {
    expect(relativeTime("2026-10-26T14:29:45Z", now)).toBe("just now");
    expect(relativeTime("2026-10-26T14:03:00Z", now)).toBe("27m ago");
    expect(relativeTime("2026-10-26T11:10:00Z", now)).toBe("3h 20m ago");
    expect(relativeTime("2026-10-20T14:30:00Z", now)).toBe("6d ago");
  });

  it("formats durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(42)).toBe("42m");
    expect(formatDuration(135)).toBe("2h 15m");
  });

  it("shows times in UTC regardless of the browser's zone", () => {
    expect(utcTime("2026-10-26T16:27:09+02:00")).toBe("14:27 UTC");
    expect(utcDateTime("2026-10-26T14:27:09Z")).toBe("2026-10-26 14:27 UTC");
  });
});
