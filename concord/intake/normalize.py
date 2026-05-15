"""Surface-level normalization of inbound text.

Conservative on purpose. We do NOT auto-correct spelling or rephrase; that
would obscure the customer's actual words from the trace. We just collapse
whitespace, strip control chars, trim, and detect language as a coarse hint.
"""

from __future__ import annotations

import re
import unicodedata

# Strip C0 controls (except \t and \n) and Unicode-format chars (zero-width etc.).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮﻿]")
_WHITESPACE = re.compile(r"[ \t  -   ]+")
_NEWLINES = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip()


def detect_language(text: str) -> str:
    """Coarse heuristic: ASCII vs not. Real deployments should swap in fasttext
    or langid; we only need a hint for routing.
    """
    if not text:
        return "en"
    sample = text[:500]
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    if non_ascii / max(len(sample), 1) > 0.15:
        return "non-en"
    return "en"


def is_likely_gibberish(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3:
        return True
    if not any(c.isalpha() for c in stripped):
        return True
    letters = sum(1 for c in stripped if c.isalpha())
    if letters / len(stripped) < 0.2:
        return True
    return False
