"""Split Markdown documents into heading-aligned, citable chunks.

A chunk never crosses a `##`/`###` heading, so every chunk belongs to exactly one numbered section and can be cited
as `§2` or `§2.1`. Long sections are split on paragraph boundaries into parts that carry the same section number.
The text that gets embedded is prefixed with the document title and the heading path: a passage like "1. Check the
provider status page" is meaningless on its own, but not under "Payment Gateway Timeout Runbook › Triage".
"""

import re
from dataclasses import dataclass

MAX_WORDS = 160  # ≈ 220 tokens: small enough to be specific, large enough to keep a numbered list together
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_TOKEN = re.compile(r"\w+|[^\w\s]")


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str  # "0" = text before the first heading, "2", "2.1", ...
    heading: str  # "Triage (first 5 minutes)" or "Mitigation › Rollback"
    content: str  # the passage as written (shown to users and to the LLM)
    embed_text: str  # title + heading path + passage (what gets embedded)
    token_count: int


def count_tokens(text: str) -> int:
    """Rough token estimate (words + punctuation); good enough for budgeting prompt context."""
    return len(_TOKEN.findall(text))


def _split_long(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    words = 0
    for para in [p for p in re.split(r"\n\s*\n", body) if p.strip()]:
        n = len(para.split())
        if current and words + n > MAX_WORDS:
            parts.append("\n\n".join(current))
            current, words = [], 0
        current.append(para)
        words += n
    if current:
        parts.append("\n\n".join(current))
    return parts


def chunk_markdown(title: str, content: str) -> list[Chunk]:
    sections: list[tuple[str, str, list[str]]] = []  # (number, heading path, lines)
    h2 = h3 = 0
    h2_title = ""
    current: tuple[str, str, list[str]] = ("0", "", [])
    in_code = False
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match is None:
            current[2].append(line)
            continue
        level, text = len(match.group(1)), match.group(2).strip()
        if level == 1:
            continue  # the document title; already known
        sections.append(current)
        if level == 2:
            h2, h3, h2_title = h2 + 1, 0, text
            current = (str(h2), text, [])
        else:
            h3 += 1
            current = (f"{h2}.{h3}", f"{h2_title} › {text}" if h2_title else text, [])
    sections.append(current)

    chunks: list[Chunk] = []
    for number, heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for part in _split_long(body):
            path = f"{title} › {heading}" if heading else title
            chunks.append(
                Chunk(
                    index=len(chunks),
                    section=number,
                    heading=heading or "Overview",
                    content=part,
                    embed_text=f"{path}\n\n{part}",
                    token_count=count_tokens(part),
                )
            )
    return chunks
