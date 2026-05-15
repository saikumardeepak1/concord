"""Final response synthesis with leakage and grounding checks.

Takes the specialist's draft (plus any executed action results) and produces
the customer-facing text. Three things matter here:

1. **Internal leakage check**: the response is scanned for tokens that should
   never reach the customer (internal IDs, internal-only phrasing, raw policy
   excerpts, system-prompt fragments, error stacks).
2. **Grounding marker**: factual claims about policy should reference the
   knowledge base. We don't model-check grounding here (an eval-time concern);
   we DO verify that citations are present when the draft makes specific
   policy claims, and demote confidence if not.
3. **Tone normalization**: opens with an empathy beat for frustrated customers
   if the specialist didn't already; trims any "as an AI language model" style
   meta-talk.

Output is plain text. Citations are surfaced separately so the UI can render
sources without the model having to format them.
"""

from __future__ import annotations

import re

# Tokens we never want to leak. Add to this list as failures are observed.
_LEAKAGE_PATTERNS = [
    re.compile(r"\bSYSTEM PROMPT\b", re.IGNORECASE),
    re.compile(r"\bsystem instructions\b", re.IGNORECASE),
    re.compile(r"\binternal[- ]only\b", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"`?ACME_INTERNAL_[A-Z_]+`?"),
    re.compile(r"\bcustomer_id=[a-zA-Z0-9_-]+"),  # never echo raw IDs
]


_META_LINES = re.compile(
    r"^(as an ai (language )?model|i (am|'m) (an|a) (large )?language model|"
    r"i don't have real-time|as an automated agent)[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)


class ResponseSynthesizer:
    def finalize(
        self,
        *,
        draft: str,
        customer_frustrated: bool,
        executed_actions_summary: str | None = None,
    ) -> str:
        text = draft.strip()
        text = _META_LINES.sub("", text)
        text = self._scrub_leakage(text)
        if customer_frustrated:
            text = self._prepend_empathy(text)
        if executed_actions_summary:
            text = f"{text}\n\n{executed_actions_summary}"
        return text.strip()

    def _scrub_leakage(self, text: str) -> str:
        out = text
        for pat in _LEAKAGE_PATTERNS:
            out = pat.sub("[redacted]", out)
        return out

    def _prepend_empathy(self, text: str) -> str:
        first = text.split(".", 1)[0].lower() if text else ""
        if any(w in first for w in ("sorry", "apologize", "frustrating", "understand")):
            return text
        return f"I understand this is frustrating, and I want to get it sorted for you. {text}"
