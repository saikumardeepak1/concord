"""Thread summarization (ADR-008).

When a thread grows past `MAX_HISTORY_CHARS`, we summarize all but the last
`KEEP_RECENT` turns into a compact recap that gets prepended to subsequent
specialist calls. The full thread is preserved in the trace and in the
conversation record so summary-induced information loss is recoverable.
"""

from __future__ import annotations

from concord.config import ModelTier
from concord.llm import get_llm
from concord.observability.tracing import span

MAX_HISTORY_CHARS = 6000
KEEP_RECENT = 4

_SUMMARY_SYSTEM = (
    "You are a concise meeting-minutes writer for a customer support conversation. "
    "Produce a faithful, neutral summary of the older portion of the thread. "
    "Preserve: customer's original problem, what has been tried, what is unresolved, "
    "and any commitments made. Do not paraphrase quotes; do not add opinions. "
    "Output 4-8 bullet points, no preamble."
)


def needs_summarization(messages: list[dict[str, str]]) -> bool:
    total = sum(len(m.get("content", "")) for m in messages)
    return total > MAX_HISTORY_CHARS and len(messages) > KEEP_RECENT


async def summarize_history(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Returns (summary_text, recent_messages_kept_verbatim)."""
    if not needs_summarization(messages):
        return "", messages

    older = messages[:-KEEP_RECENT]
    recent = messages[-KEEP_RECENT:]
    transcript = "\n".join(f"{m['role'].upper()}: {m.get('content', '')}" for m in older)

    async with span("intake.summarize", chars=sum(len(m.get('content', '')) for m in older)):
        llm = get_llm()
        resp = await llm.complete(
            tier=ModelTier.FAST,
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": f"Summarize this thread so far:\n\n{transcript}"}],
            max_tokens=500,
            temperature=0.0,
        )
    return resp.text, recent
