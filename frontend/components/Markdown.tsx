// Minimal Markdown renderer for the knowledge base: headings, paragraphs, lists, tables, code blocks, inline code,
// bold and italics. It builds React elements (never raw HTML), so document content cannot inject markup.

function inline(text: string, keyBase: string): React.ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean);
  return parts.map((p, i) => {
    const key = `${keyBase}-${i}`;
    if (p.startsWith("`")) return <code key={key} className="rounded bg-slate-800 px-1 py-0.5 font-mono text-[0.85em] text-sky-200">{p.slice(1, -1)}</code>;
    if (p.startsWith("**")) return <strong key={key} className="font-semibold text-slate-100">{p.slice(2, -2)}</strong>;
    if (p.startsWith("*") && p.length > 2) return <em key={key}>{p.slice(1, -1)}</em>;
    return p;
  });
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (line.trim().startsWith("```")) {
      const indent = line.indexOf("`");
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++].slice(indent));
      i++;
      out.push(<pre key={key} className="my-3 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-xs leading-relaxed text-slate-200">{code.join("\n")}</pre>);
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const cls = level === 1 ? "mb-3 text-2xl font-semibold" : level === 2 ? "mb-2 mt-6 text-lg font-semibold" : "mb-2 mt-4 font-semibold";
      out.push(<div key={key} role="heading" aria-level={level} className={cls}>{inline(heading[2], key)}</div>);
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].split("|").slice(1, -1).map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      out.push(
        <div key={key} className="my-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-slate-700 text-left text-xs text-slate-400">{head.map((c, n) => <th key={n} className="py-1.5 pr-4 font-medium">{inline(c, `${key}h${n}`)}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-800">{body.map((r, rn) => <tr key={rn}>{r.map((c, n) => <td key={n} className="py-1.5 pr-4 text-slate-300">{inline(c, `${key}r${rn}c${n}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^(\d+\.|-)\s/.test(line)) {
      const ordered = /^\d+\./.test(line);
      const start = ordered ? parseInt(line, 10) : undefined; // lists split by a code block keep counting
      const items: string[] = [];
      while (i < lines.length && (/^(\d+\.|-)\s/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && !lines[i].trim().startsWith("```")))) {
        if (/^(\d+\.|-)\s/.test(lines[i])) items.push(lines[i].replace(/^(\d+\.|-)\s+/, ""));
        else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      const List = ordered ? "ol" : "ul";
      out.push(
        <List key={key} start={start} className={`my-2 space-y-1 pl-5 text-sm text-slate-300 ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((it, n) => <li key={n}>{inline(it, `${key}-${n}`)}</li>)}
        </List>,
      );
      continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#|\||```|\d+\.\s|-\s)/.test(lines[i].trim())) para.push(lines[i++]);
    if (para.length === 0) para.push(lines[i++]); // a line no block rule claimed: render it as text
    out.push(<p key={key} className="my-2 text-sm leading-relaxed text-slate-300">{inline(para.join(" "), key)}</p>);
  }
  return <div>{out}</div>;
}
