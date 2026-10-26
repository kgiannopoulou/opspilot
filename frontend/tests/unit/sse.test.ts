import { describe, expect, it } from "vitest";
import { parseBlock, readEvents } from "@/services/sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

describe("server-sent events parser", () => {
  it("parses event, id and multi-line data", () => {
    expect(parseBlock("event: progress\nid: 3\ndata: {\"a\":\ndata: 1}")).toEqual({ event: "progress", id: "3", data: '{"a":\n1}' });
  });

  it("skips comments and retry-only blocks", () => {
    expect(parseBlock(": keep-alive")).toBeNull();
    expect(parseBlock("retry: 3000")).toBeNull();
  });

  it("reassembles events split across network chunks", async () => {
    const events = [];
    for await (const e of readEvents(streamOf("retry: 3000\n\nevent: prog", "ress\ndata: {\"status\":\"RUNNING\"}\n\n: keep-alive\n\n", "event: done\r\ndata: {}\r\n\r\n"))) {
      events.push(e);
    }
    expect(events.map((e) => e.event)).toEqual(["progress", "done"]);
    expect(JSON.parse(events[0].data)).toEqual({ status: "RUNNING" });
  });
});
