"""Document chunking.

Markdown-aware splitter: prefers to break on heading boundaries first, then on
paragraphs, then by character length. Each chunk keeps the heading trail in its
metadata so retrieved passages can be cited with section context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(slots=True)
class Chunk:
    doc_id: str
    title: str
    text: str
    source: str
    chunk_index: int
    heading_trail: str


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Returns (heading_trail, body) sections."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []
    trail: list[str] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        trail = trail[: level - 1] + [heading]
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            sections.append((" > ".join(trail), body))
    return sections


def _pack(text: str, max_chars: int, overlap: int) -> list[str]:
    """Pack text into chunks of approx `max_chars` with `overlap` continuity."""
    if len(text) <= max_chars:
        return [text]
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}" if buf else para
            continue
        if buf:
            chunks.append(buf.strip())
        if len(para) > max_chars:
            # Hard split a long paragraph.
            start = 0
            while start < len(para):
                chunks.append(para[start : start + max_chars])
                start += max_chars - overlap
            buf = ""
        else:
            buf = para
    if buf:
        chunks.append(buf.strip())
    return chunks


def chunk_markdown(path: Path, max_chars: int, overlap: int) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    title = path.stem.replace("-", " ").replace("_", " ").title()
    doc_id = str(path.relative_to(path.anchor)).replace("/", ":")[:120] if path.is_absolute() else str(path).replace("/", ":")[:120]
    source = str(path)

    sections = _split_by_headings(raw)
    chunks: list[Chunk] = []
    idx = 0
    for trail, body in sections:
        for piece in _pack(body, max_chars=max_chars, overlap=overlap):
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    title=title,
                    text=piece,
                    source=source,
                    chunk_index=idx,
                    heading_trail=trail,
                )
            )
            idx += 1
    return chunks
