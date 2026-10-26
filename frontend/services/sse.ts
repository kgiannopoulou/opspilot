// Minimal Server-Sent Events parser over a fetch() body (text/event-stream, https://html.spec.whatwg.org/#server-sent-events).

export interface SSEMessage { event: string; data: string; id: string | null }

/** Split a chunked stream into events. Comment lines (": keep-alive") and `retry:` fields are skipped. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const message = parseBlock(block);
        if (message) yield message;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function parseBlock(block: string): SSEMessage | null {
  let event = "message";
  let id: string | null = null;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  return data.length ? { event, data: data.join("\n"), id } : null;
}
